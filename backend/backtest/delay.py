"""Teste de atraso: quanto o acerto cai se você entra alguns segundos depois?

No modo horário da Quotex o horário de expiração não muda. Só o PREÇO de entrada
piora (ou melhora) com o atraso. Usa dados finos (1 s cripto, 5 s forex) para saber
o preço exato em entry_at + atraso.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .binary_bt import simulate

MAX_GAP = pd.Timedelta(seconds=10)  # tolerância para achar o candle fino mais próximo


def entry_prices(fine: pd.DataFrame, at: pd.DatetimeIndex) -> np.ndarray:
    """Abertura do primeiro candle fino que começa em `at` ou logo depois (NaN se não houver)."""
    idx = fine.index.searchsorted(at)
    ok = idx < len(fine)
    out = np.full(len(at), np.nan)
    pos = idx[ok]
    gap = fine.index[pos] - at[ok]
    good = np.asarray(gap <= MAX_GAP)
    vals = fine["open"].to_numpy()[pos]
    sub = np.where(good, vals, np.nan)
    out[np.flatnonzero(ok)] = sub
    return out


def delay_table(df: pd.DataFrame, fine: pd.DataFrame, signals: pd.DataFrame, tf: int,
                expiry_candles: int, delays: list[int] = (0, 5, 10, 20, 30)) -> pd.DataFrame:
    """Linhas: setup × atraso → n, acerto. `df` no timeframe `tf`; `fine` cobre parte do período."""
    if fine.empty:
        return pd.DataFrame()
    window = df[(df.index >= fine.index[0]) & (df.index <= fine.index[-1])]
    rows = []
    for setup in signals.columns:
        tr = simulate(window, signals[setup].reindex(window.index).fillna(0), expiry_candles, tf=tf)
        if tr.empty:
            continue
        entry_at = tr.index + pd.Timedelta(minutes=tf)
        for d in delays:
            ep = entry_prices(fine, entry_at + pd.Timedelta(seconds=d))
            res = np.sign(tr["direction"].to_numpy() * (tr["exit"].to_numpy() - ep))
            ok = ~np.isnan(res) & (res != 0)
            n = int(ok.sum())
            w = int((res[ok] > 0).sum())
            rows.append({"setup": setup, "delay_s": d, "n": n, "winrate": w / n if n else np.nan})
    return pd.DataFrame(rows)
