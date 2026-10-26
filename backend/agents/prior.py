"""Prior: taxa base histórica de cada célula ativo × setup × expiração × sessão.

Guarda só contagens (ganhos / operações decididas) separadas em descoberta e
confirmação. O julgamento com o payout do momento fica com o agente Edge.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..backtest.binary_bt import simulate
from ..config import DATA_DIR, Config
from .spotter import run_all

PRIORS_PATH = DATA_DIR / "priors.json"
ALL = -1  # sessão "qualquer horário"


def cell_key(asset: str, setup: str, expiry: int, bucket: int, tf: int) -> str:
    return f"{asset}|M{tf}|{setup}|{expiry}|{bucket}"


def bucket_of(ts: pd.Timestamp | pd.DatetimeIndex, hour_bucket: int):
    return ts.hour // hour_bucket


def _counts(result: np.ndarray) -> tuple[int, int]:
    return int((result == 1).sum()), int((result != 0).sum())


def build(df: pd.DataFrame, asset: str, cfg: Config, tf: int,
          extra: dict[str, pd.Series] | None = None) -> dict[str, dict]:
    """`df` já no timeframe `tf`. `extra`: séries fora da amostra (ex.: Confluência)."""
    split = df.index[int(len(df) * cfg.discovery_frac)]
    signals = run_all(df, extra=extra)
    cells: dict[str, dict] = {}
    for setup in signals.columns:
        for expiry in cfg.expiries_for(tf):
            tr = simulate(df, signals[setup], cfg.expiry_candles(expiry, tf), tf=tf)
            if tr.empty:
                continue
            buckets = np.asarray(bucket_of(tr.index, cfg.hour_bucket))
            disc = np.asarray(tr.index < split)
            res = tr["result"].to_numpy()
            for b in [ALL, *range(24 // cfg.hour_bucket)]:
                m = np.ones(len(tr), bool) if b == ALL else buckets == b
                dw, dn = _counts(res[m & disc])
                cw, cn = _counts(res[m & ~disc])
                cells[cell_key(asset, setup, expiry, b, tf)] = {
                    "asset": asset, "tf": tf, "setup": setup, "expiry": expiry, "bucket": b,
                    "disc_wins": dw, "disc_n": dn, "conf_wins": cw, "conf_n": cn,
                }
    return cells


def save(cells: dict[str, dict], meta: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing = load()
    existing["cells"].update(cells)
    existing["meta"].update(meta)
    existing["meta"]["built_at"] = datetime.now(timezone.utc).isoformat()
    PRIORS_PATH.write_text(json.dumps(existing, indent=1), encoding="utf-8")


def load() -> dict:
    if PRIORS_PATH.exists():
        return json.loads(PRIORS_PATH.read_text(encoding="utf-8"))
    return {"meta": {}, "cells": {}}


def lookup(priors: dict, asset: str, setup: str, expiry: int, ts: pd.Timestamp, cfg: Config,
           tf: int) -> list[dict]:
    """Células candidatas, da mais específica (sessão) para a geral (qualquer horário)."""
    cells = priors.get("cells", {})
    out = []
    for b in (int(bucket_of(ts, cfg.hour_bucket)), ALL):
        c = cells.get(cell_key(asset, setup, expiry, b, tf))
        if c:
            out.append(c)
    return out
