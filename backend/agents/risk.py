"""Closer: regras de risco que valem acima de qualquer sinal.

- stop diário e meta diária (opcional)
- máximo de entradas por dia
- pausa obrigatória após N perdas seguidas
- uma operação por vez
- ANTI-MARTINGALE: depois de uma perda o valor nunca sobe; o tamanho é calculado
  sobre a menor banca entre o início do dia e a atual
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from zoneinfo import ZoneInfo

import pandas as pd

from ..config import Config


@dataclass
class RiskState:
    bankroll: float
    day_start_bankroll: float
    day_pnl: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    last_loss_at: pd.Timestamp | None = None
    last_stake: float | None = None
    last_result: str | None = None
    open_trades: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["last_loss_at"] = self.last_loss_at.isoformat() if self.last_loss_at is not None else None
        return d


@dataclass
class RiskDecision:
    allowed: bool
    stake: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def state_from_journal(trades: list[dict], bankroll_start: float, cfg: Config,
                       now: pd.Timestamp | None = None) -> RiskState:
    """`trades`: linhas do diário ordenadas por created_at (ISO UTC), com result em win/loss/push/pending."""
    now = now or pd.Timestamp.now(tz="UTC")
    tz = ZoneInfo(cfg.timezone)
    today = now.tz_convert(tz).date()

    bankroll = bankroll_start
    day_start = bankroll_start
    st = RiskState(bankroll, day_start)
    for t in trades:
        ts = pd.Timestamp(t["created_at"])
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
        is_today = ts.tz_convert(tz).date() == today
        if t["result"] == "pending":
            st.open_trades += 1
            if is_today:
                st.trades_today += 1
            continue
        bankroll += t.get("pnl") or 0.0
        if not is_today:
            day_start = bankroll
        else:
            st.trades_today += 1
            st.day_pnl += t.get("pnl") or 0.0
            if t["result"] == "loss":
                st.consecutive_losses += 1
                st.last_loss_at = ts
            elif t["result"] == "win":
                st.consecutive_losses = 0
        st.last_stake = t.get("stake")
        st.last_result = t["result"]
    st.bankroll = bankroll
    st.day_start_bankroll = day_start
    return st


def check(state: RiskState, proposed_stake: float, cfg: Config,
          now: pd.Timestamp | None = None) -> RiskDecision:
    now = now or pd.Timestamp.now(tz="UTC")
    reasons: list[str] = []

    if state.open_trades:
        return RiskDecision(False, 0.0, ["há operação aberta sem resultado no diário"])
    if state.day_pnl <= -cfg.daily_stop_frac * state.day_start_bankroll:
        return RiskDecision(False, 0.0, [f"stop diário atingido ({state.day_pnl:+.2f})"])
    if cfg.daily_target_frac > 0 and state.day_pnl >= cfg.daily_target_frac * state.day_start_bankroll:
        return RiskDecision(False, 0.0, [f"meta do dia batida ({state.day_pnl:+.2f}) — encerre"])
    if state.trades_today >= cfg.max_trades_day:
        return RiskDecision(False, 0.0, [f"limite de {cfg.max_trades_day} entradas no dia"])
    if state.consecutive_losses >= cfg.max_consecutive_losses and state.last_loss_at is not None:
        until = state.last_loss_at + pd.Timedelta(minutes=cfg.cooldown_minutes)
        if now < until:
            mins = int((until - now).total_seconds() // 60) + 1
            return RiskDecision(False, 0.0, [f"{state.consecutive_losses} perdas seguidas: pausa de mais {mins} min"])
    if proposed_stake <= 0:
        return RiskDecision(False, 0.0, ["sem tamanho de entrada"])

    stake = proposed_stake
    if state.last_result == "loss" and state.last_stake and stake > state.last_stake:
        stake = state.last_stake
        reasons.append(f"anti-martingale: limitado ao valor anterior {stake:.2f}")
    remaining = cfg.daily_stop_frac * state.day_start_bankroll + state.day_pnl
    if stake > remaining:
        stake = round(max(remaining, 0.0), 2)
        reasons.append(f"limitado ao que falta para o stop ({stake:.2f})")
    if stake < cfg.min_stake:
        return RiskDecision(False, 0.0, [*reasons, "valor abaixo do mínimo da corretora"])
    return RiskDecision(True, stake, reasons or ["dentro das regras de risco"])


def base_bankroll(state: RiskState) -> float:
    """Banca usada para dimensionar: a menor entre o início do dia e a atual."""
    return min(state.bankroll, state.day_start_bankroll)
