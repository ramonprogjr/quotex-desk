"""Backtest de opções binárias.

Modelo de execução (igual ao uso manual no modo "horário" da Quotex):
- o sinal nasce no fechamento do candle t;
- a entrada acontece na abertura do candle t+1;
- a expiração de E candles fecha no fim do candle t+E (em M5 com 5 min: E=1);
- ganha se o preço andou a favor, perde se andou contra, empate devolve o valor.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from ..stats import breakeven, ev_per_unit, wilson


def simulate(df: pd.DataFrame, direction: pd.Series, expiry: int, one_at_a_time: bool = True,
             tf: int = 1) -> pd.DataFrame:
    """`expiry` em candles; `tf` = minutos por candle.
    Devolve uma linha por operação: ts, direction, entry, exit, result (1 ganho, -1 perda, 0 empate)."""
    o = df["open"].to_numpy()
    c = df["close"].to_numpy()
    ts = df.index
    d = direction.reindex(df.index).fillna(0).to_numpy()
    n = len(df)

    idx = np.flatnonzero(d != 0)
    idx = idx[idx + expiry <= n - 1]
    # descarta janelas com buraco nos dados (fim de semana no forex, falhas da fonte)
    if len(idx):
        span = (ts[idx + expiry] - ts[idx]).to_numpy()
        idx = idx[span == np.timedelta64(expiry * tf, "m")]

    if one_at_a_time and len(idx):
        keep = []
        next_free = -1
        for i in idx:
            if i >= next_free:
                keep.append(i)
                next_free = i + expiry
        idx = np.asarray(keep, dtype=int)

    entry = o[idx + 1]
    exit_ = c[idx + expiry]
    dirs = d[idx].astype(int)
    result = np.sign(dirs * (exit_ - entry)).astype(int)
    return pd.DataFrame(
        {"direction": dirs, "entry": entry, "exit": exit_, "result": result},
        index=pd.DatetimeIndex(ts[idx], name="ts"),
    )


@dataclass
class Summary:
    trades: int
    wins: int
    losses: int
    pushes: int
    winrate: float
    lo: float
    hi: float
    breakeven: float
    ev_per_unit: float
    edge_proven: bool

    def to_dict(self) -> dict:
        return asdict(self)


def summarize(trades: pd.DataFrame, payout: float, z: float = 1.645) -> Summary:
    wins = int((trades["result"] == 1).sum())
    losses = int((trades["result"] == -1).sum())
    pushes = int((trades["result"] == 0).sum())
    n = wins + losses
    p = wins / n if n else 0.0
    lo, hi = wilson(wins, n, z)
    be = breakeven(payout)
    return Summary(len(trades), wins, losses, pushes, p, lo, hi, be, ev_per_unit(p, payout), n > 0 and lo > be)


def equity_curve(trades: pd.DataFrame, payout: float, stake_frac: float = 0.01, start: float = 100.0) -> pd.Series:
    """Banca com fração fixa por entrada (sem martingale)."""
    r = trades["result"].to_numpy()
    growth = np.where(r == 1, 1 + stake_frac * payout, np.where(r == -1, 1 - stake_frac, 1.0))
    return pd.Series(start * np.cumprod(growth), index=trades.index)


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    return float(((equity - peak) / peak).min())
