"""Walk-forward: o MÉTODO de seleção funciona fora da amostra?

Em cada janela: escolhe as células (setup × expiração × sessão) que passaram no
treino e mede só as operações dessas células no período seguinte, que nunca foi visto.
Se o acerto agregado fora da amostra não passar do empate, não há o que operar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..agents.prior import bucket_of
from ..agents.spotter import run_all
from ..config import Config
from ..stats import breakeven, wilson
from .binary_bt import simulate, summarize


def run(df: pd.DataFrame, cfg: Config, tf: int, train_days: int = 30, test_days: int = 7,
        extra: dict[str, pd.Series] | None = None) -> dict:
    """`df` já no timeframe `tf`."""
    be = breakeven(cfg.payout)
    signals = run_all(df, extra=extra)
    all_trades = []
    for setup in signals.columns:
        for expiry in cfg.expiries_for(tf):
            tr = simulate(df, signals[setup], cfg.expiry_candles(expiry, tf), tf=tf)
            if tr.empty:
                continue
            tr = tr.assign(setup=setup, expiry=expiry, bucket=np.asarray(bucket_of(tr.index, cfg.hour_bucket)))
            all_trades.append(tr)
    if not all_trades:
        return {"windows": [], "oos": None}
    trades = pd.concat(all_trades).sort_index()
    keys = ["setup", "expiry", "bucket"]

    windows = []
    oos_parts = []
    t = df.index[0] + pd.Timedelta(days=train_days)
    while t + pd.Timedelta(days=test_days) <= df.index[-1]:
        train = trades[(trades.index >= t - pd.Timedelta(days=train_days)) & (trades.index < t)]
        test = trades[(trades.index >= t) & (trades.index < t + pd.Timedelta(days=test_days))]
        decided = train[train["result"] != 0]
        g = decided.groupby(keys)["result"].agg(wins=lambda s: int((s == 1).sum()), n="size")
        g["lo"] = [wilson(w, n, cfg.z)[0] for w, n in zip(g["wins"], g["n"])]
        chosen = g[(g["n"] >= cfg.min_n) & (g["lo"] > be)].reset_index()[keys]
        sel = test.reset_index().merge(chosen, on=keys).set_index("ts")
        s = summarize(sel, cfg.payout, cfg.z) if len(sel) else None
        windows.append({"start": t.isoformat(), "cells_selected": int(len(chosen)),
                        "trades": len(sel), "winrate": s.winrate if s else None})
        if len(sel):
            oos_parts.append(sel)
        t += pd.Timedelta(days=test_days)

    oos = summarize(pd.concat(oos_parts), cfg.payout, cfg.z).to_dict() if oos_parts else None
    return {"windows": windows, "oos": oos, "breakeven": be}
