"""Cache local de candles em Parquet (data_cache/<ATIVO>_1m.parquet)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..assets import get_asset
from ..config import DATA_DIR
from . import fetch_history


def path_for(key: str) -> Path:
    return DATA_DIR / f"{key.upper()}_1m.parquet"


def load(key: str) -> pd.DataFrame | None:
    p = path_for(key)
    if not p.exists():
        return None
    return pd.read_parquet(p)


def save(key: str, df: pd.DataFrame) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path_for(key))


def update_fine(key: str, days: int = 14, progress=None) -> pd.DataFrame:
    """Dados finos (1 s cripto / 5 s forex) dos últimos `days` dias, com cache incremental."""
    from . import fetch_fine

    asset = get_asset(key)
    p = DATA_DIR / f"{key.upper()}_fine.parquet"
    now = pd.Timestamp.now(tz="UTC").floor("min")
    start = now - pd.Timedelta(days=days)
    cached = pd.read_parquet(p) if p.exists() else None
    if cached is not None and len(cached) and cached.index[0] <= start + pd.Timedelta(hours=1):
        new = fetch_fine(asset, cached.index[-1] + pd.Timedelta(seconds=1), now, progress)
        df = pd.concat([cached, new])
    else:
        df = fetch_fine(asset, start, now, progress)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df = df[df.index >= start]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p)
    return df


def update(key: str, days: int = 90, progress=None) -> pd.DataFrame:
    """Baixa `days` dias de histórico (ou só o que falta, se já existir cache)."""
    asset = get_asset(key)
    now = pd.Timestamp.now(tz="UTC").floor("min")
    cached = load(key)
    wanted_start = now - pd.Timedelta(days=days)

    parts = []
    if cached is not None and len(cached):
        if cached.index[0] > wanted_start + pd.Timedelta(hours=1):
            parts.append(fetch_history(asset, wanted_start, cached.index[0], progress))
        parts.append(cached)
        parts.append(fetch_history(asset, cached.index[-1] + pd.Timedelta(minutes=1), now, progress))
    else:
        parts.append(fetch_history(asset, wanted_start, now, progress))

    parts = [p for p in parts if p is not None and len(p)]
    df = pd.concat(parts).sort_index() if parts else pd.DataFrame()
    df = df[~df.index.duplicated(keep="last")]
    save(key, df)
    return df
