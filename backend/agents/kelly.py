"""Kelly: tamanho da entrada.

Usa o limite INFERIOR do acerto (conservador), Kelly fracionado e teto de % da banca.
Sem vantagem → 0. Nunca aumenta para "recuperar" — isso é papel do Closer garantir.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..config import Config
from ..stats import kelly_full


@dataclass
class Sizing:
    stake: float
    fraction: float
    full_kelly: float
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def size(p_conservative: float, payout: float, bankroll: float, cfg: Config) -> Sizing:
    fk = kelly_full(p_conservative, payout)
    if fk <= 0 or bankroll <= 0:
        return Sizing(0.0, 0.0, fk, "Kelly ≤ 0: sem vantagem, não entrar")
    frac = min(fk * cfg.kelly_fraction, cfg.max_stake_frac)
    stake = round(bankroll * frac, 2)
    cap = bankroll * cfg.max_stake_frac
    if stake < cfg.min_stake:
        if cfg.min_stake <= cap:
            return Sizing(cfg.min_stake, cfg.min_stake / bankroll, fk,
                          f"Kelly sugere {stake:.2f}; usando mínimo da corretora {cfg.min_stake:.2f}")
        return Sizing(0.0, 0.0, fk, f"banca pequena: mínimo {cfg.min_stake:.2f} passa do teto de {cfg.max_stake_frac:.0%}")
    capped = " (no teto)" if frac == cfg.max_stake_frac else ""
    return Sizing(stake, frac, fk, f"¼ Kelly = {frac:.2%} da banca{capped}")
