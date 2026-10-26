"""Fontes de candles.

Formato padrão: DataFrame indexado por `ts` (UTC, início do candle) com colunas
open, high, low, close, volume — apenas candles já fechados.
O histórico é guardado em 1 min e reagrupado (`resample`) para o timeframe de análise.
"""
from __future__ import annotations

import pandas as pd

from ..assets import Asset
from . import crypto_feed, fx_feed, otc_feed

COLUMNS = ["open", "high", "low", "close", "volume"]


def _feed(asset: Asset):
    return {"crypto": crypto_feed, "otc": otc_feed}.get(asset.source, fx_feed)


def fetch_history(asset: Asset, start: pd.Timestamp, end: pd.Timestamp, progress=None) -> pd.DataFrame:
    feed = _feed(asset)
    return feed.fetch_history(asset.symbol, start, end, progress=progress)


def fetch_recent(asset: Asset, n: int = 500, tf: int = 1) -> pd.DataFrame:
    feed = _feed(asset)
    return feed.fetch_recent(asset.symbol, n, tf)


def fetch_forming(asset: Asset, tf: int = 1) -> pd.DataFrame | None:
    feed = _feed(asset)
    return feed.fetch_forming(asset.symbol, tf)


def fetch_fine(asset: Asset, start: pd.Timestamp, end: pd.Timestamp, progress=None) -> pd.DataFrame:
    """Dados de poucos segundos (cripto 1 s, forex 5 s) para o teste de atraso."""
    feed = _feed(asset)
    return feed.fetch_fine(asset.symbol, start, end, progress=progress)


def drop_forming(df: pd.DataFrame, now: pd.Timestamp | None = None, tf: int = 1) -> pd.DataFrame:
    """Remove o candle que ainda está se formando."""
    now = now or pd.Timestamp.now(tz="UTC")
    return df[df.index + pd.Timedelta(minutes=tf) <= now]


def resample(df: pd.DataFrame, tf: int) -> pd.DataFrame:
    """Agrupa candles de 1 min em candles de `tf` min (alinhados ao relógio, como na Quotex).
    Descarta grupos incompletos (buracos nos dados)."""
    if tf == 1:
        return df
    g = df.resample(f"{tf}min", label="left", closed="left")
    out = g.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    out = out[g["close"].count() == tf]
    out.index.name = "ts"
    return out
