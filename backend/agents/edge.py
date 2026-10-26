"""Edge: decide se uma célula (ativo × setup × expiração × sessão) tem vantagem comprovada.

Regra: o limite inferior de Wilson precisa ficar acima do ponto de empate
p* = 1/(1+payout) TANTO no período de descoberta QUANTO no de confirmação,
com amostra mínima. Isso reduz o falso positivo de testar muitas combinações.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..config import Config
from ..stats import breakeven, wilson


@dataclass
class EdgeVerdict:
    approved: bool
    p_hat: float      # acerto histórico (descoberta + confirmação)
    lo: float         # limite inferior de Wilson do total (usado no Kelly)
    breakeven: float
    n: int
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def judge(cell: dict | None, payout: float, cfg: Config) -> EdgeVerdict:
    be = breakeven(payout)
    if not cell:
        return EdgeVerdict(False, 0.0, 0.0, be, 0, "sem histórico para esta combinação")

    dw, dn = cell["disc_wins"], cell["disc_n"]
    cw, cn = cell["conf_wins"], cell["conf_n"]
    w, n = dw + cw, dn + cn
    p = w / n if n else 0.0
    lo, _ = wilson(w, n, cfg.z)
    d_lo, _ = wilson(dw, dn, cfg.z)
    c_lo, _ = wilson(cw, cn, cfg.z)
    min_conf = max(30, int(cfg.min_n * (1 - cfg.discovery_frac) / cfg.discovery_frac))

    if dn < cfg.min_n:
        reason = f"amostra pequena na descoberta ({dn} < {cfg.min_n})"
    elif cn < min_conf:
        reason = f"amostra pequena na confirmação ({cn} < {min_conf})"
    elif d_lo <= be:
        reason = f"descoberta não prova vantagem (inf. {d_lo:.1%} ≤ empate {be:.1%})"
    elif c_lo <= be:
        reason = f"confirmação não sustentou (inf. {c_lo:.1%} ≤ empate {be:.1%})"
    else:
        return EdgeVerdict(True, p, lo, be, n, f"vantagem comprovada: {p:.1%} (inf. {lo:.1%}) > empate {be:.1%}")
    return EdgeVerdict(False, p, lo, be, n, reason)
