"""Orquestrador: roda Spotter → Prior → Edge → Kelly → Closer no fechamento de cada candle."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd

from ..assets import Asset
from ..config import Config
from . import breaker, confluence, edge, kelly, prior, risk, specialists
from .spotter import run_all


@dataclass
class Signal:
    id: str
    asset: str
    quotex: str
    tf: int                        # timeframe do candle (1 = M1, 5 = M5)
    setup: str
    direction: str                 # CALL / PUT
    candle_ts: str                 # início do candle que gerou o sinal (UTC)
    entry_at: str                  # abertura do próximo candle
    valid_until: str               # depois disso, não entrar
    expiry: int                    # minutos
    expires_at: str                # horário de expiração (modo horário da Quotex)
    price: float
    approved: bool
    stake: float
    p_hat: float
    lo: float
    breakeven: float
    n: int
    payout: float
    agents: dict[str, str] = field(default_factory=dict)
    votes: dict[str, float] = field(default_factory=dict)   # especialistas, −1..+1
    p_model: float | None = None                              # Confluência: probabilidade de alta
    pre: bool = False                                         # pré-sinal (candle ainda aberto)
    edge_ok: bool = False                                     # Edge aprovou (antes do disjuntor e do risco)

    def to_dict(self) -> dict:
        return asdict(self)


ARROW = {1: "↑", -1: "↓", 0: "·"}


def _votes_text(votes: dict[str, float], p_model: float | None) -> str:
    parts = [f"{k.replace('_', ' ')} {ARROW[int(np.sign(v))]}" for k, v in votes.items()]
    if p_model is not None:
        parts.append(f"confluência {max(p_model, 1 - p_model):.0%} {'CALL' if p_model >= .5 else 'PUT'}")
    return " · ".join(parts)


def _best_verdict(cells: list[dict], payout: float, cfg: Config) -> edge.EdgeVerdict:
    verdicts = [edge.judge(c, payout, cfg) for c in cells] or [edge.judge(None, payout, cfg)]
    approved = [v for v in verdicts if v.approved]
    pool = approved or verdicts
    return max(pool, key=lambda v: v.lo)


def evaluate(df: pd.DataFrame, asset: Asset, priors: dict, state: risk.RiskState,
             cfg: Config, tf: int, payout: float | None = None, model: dict | None = None,
             pre: bool = False, suspended: dict[str, dict] | None = None,
             evolved: list[dict] | None = None) -> list[Signal]:
    """Avalia o último candle de `df` (timeframe `tf`). Devolve um sinal por setup disparado.
    `pre=True`: o último candle ainda está aberto (pré-sinal, só aviso).
    `suspended`: estratégias desligadas pelo disjuntor (chave breaker.key)."""
    suspended = suspended or {}
    if len(df) < 120:
        return []
    payout = cfg.payout if payout is None else payout
    last_ts = df.index[-1]
    fired = run_all(df).iloc[-1]
    votes = {k: float(v) for k, v in specialists.analyze(df)[1].iloc[-1].items()}
    p_model = None
    if model is not None:
        p_model, d = confluence.predict_last(df, model)
        fired[confluence.NAME] = d
    if evolved:  # estratégias criadas pelo Laboratório Evolutivo e aprovadas no cofre
        from ..lab.evolve import signals_for
        for name, s in signals_for(df, evolved).items():
            fired[name] = int(s.iloc[-1])
    entry_at = last_ts + pd.Timedelta(minutes=tf)
    out: list[Signal] = []

    for setup, d in fired.items():
        if d == 0:
            continue
        direction = "CALL" if d > 0 else "PUT"
        # Prior + Edge: escolhe a expiração com melhor evidência
        best_exp, best_v = None, None
        for e in cfg.expiries_for(tf):
            v = _best_verdict(prior.lookup(priors, asset.key, setup, e, last_ts, cfg, tf), payout, cfg)
            if best_v is None or (v.approved, v.lo) > (best_v.approved, best_v.lo):
                best_exp, best_v = e, v
        agents = {
            "spotter": f"M{tf} {setup} → {direction} em {float(df['close'].iloc[-1]):g}",
            "prior": f"{best_v.n} operações históricas, acerto {best_v.p_hat:.1%} ({best_exp} min)",
            "edge": best_v.reason,
            "especialistas": _votes_text(votes, p_model),
        }
        stake = 0.0
        approved = False
        edge_ok = best_v.approved
        brk = suspended.get(breaker.key(asset.key, tf, setup))
        if edge_ok and brk:
            agents["edge"] = brk["reason"]
            agents["kelly"] = "0 — estratégia suspensa"
            agents["closer"] = "vetado pelo disjuntor (piorou ao vivo)"
        elif best_v.approved:
            sz = kelly.size(best_v.lo, payout, risk.base_bankroll(state), cfg)
            agents["kelly"] = sz.reason
            dec = risk.check(state, sz.stake, cfg)
            agents["closer"] = "; ".join(dec.reasons)
            approved, stake = dec.allowed, dec.stake
        else:
            agents["kelly"] = "0 — sem vantagem comprovada"
            agents["closer"] = "vetado pelo Edge"

        out.append(Signal(
            id=uuid.uuid4().hex[:12],
            asset=asset.key, quotex=asset.quotex, tf=tf, setup=setup, direction=direction,
            candle_ts=last_ts.isoformat(), entry_at=entry_at.isoformat(),
            valid_until=(entry_at + pd.Timedelta(seconds=cfg.signal_ttl(tf))).isoformat(),
            expiry=best_exp, expires_at=(entry_at + pd.Timedelta(minutes=best_exp)).isoformat(),
            price=float(df["close"].iloc[-1]), approved=approved, stake=stake,
            p_hat=best_v.p_hat, lo=best_v.lo, breakeven=best_v.breakeven, n=best_v.n,
            payout=payout, agents=agents, votes=votes, p_model=p_model, pre=pre, edge_ok=edge_ok,
        ))
    out.sort(key=lambda s: (s.approved, s.lo), reverse=True)
    return out
