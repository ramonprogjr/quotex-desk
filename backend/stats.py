"""Funções estatísticas usadas pelos agentes e pelo backtest."""
from __future__ import annotations

import math


def breakeven(payout: float) -> float:
    """Taxa de acerto mínima para empatar: p* = 1 / (1 + payout)."""
    return 1.0 / (1.0 + payout)


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de Wilson para uma proporção. Retorna (inferior, superior)."""
    if n <= 0:
        return 0.0, 1.0
    p = wins / n
    z2 = z * z
    denom = 1 + z2 / n
    centre = (p + z2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def ev_per_unit(p: float, payout: float) -> float:
    """Valor esperado por 1 unidade apostada."""
    return p * payout - (1 - p)


def kelly_full(p: float, payout: float) -> float:
    """Fração de Kelly para aposta binária: f* = (p·b − (1−p)) / b."""
    if payout <= 0:
        return 0.0
    return (p * payout - (1 - p)) / payout
