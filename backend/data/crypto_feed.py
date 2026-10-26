"""Candles de cripto via CCXT (Binance, dados públicos — sem chave)."""
from __future__ import annotations

import ccxt
import pandas as pd

_exchange: ccxt.Exchange | None = None
MINUTE_MS = 60_000


def exchange() -> ccxt.Exchange:
    global _exchange
    if _exchange is None:
        _exchange = ccxt.binance({"enableRateLimit": True})
    return _exchange


def _to_df(rows: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts").astype(float)
    return df[~df.index.duplicated(keep="last")].sort_index()


def fetch_history(symbol: str, start: pd.Timestamp, end: pd.Timestamp, progress=None) -> pd.DataFrame:
    ex = exchange()
    since = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows: list[list] = []
    while since < end_ms:
        batch = ex.fetch_ohlcv(symbol, "1m", since=since, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        since = batch[-1][0] + MINUTE_MS
        if progress:
            progress(pd.Timestamp(batch[-1][0], unit="ms", tz="UTC"))
    from . import drop_forming

    df = _to_df(rows)
    return drop_forming(df[df.index < end])


def fetch_fine(symbol: str, start: pd.Timestamp, end: pd.Timestamp, progress=None) -> pd.DataFrame:
    """Candles de 1 SEGUNDO (Binance spot) — para medir o efeito de entrar atrasado."""
    ex = exchange()
    since = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    rows: list[list] = []
    while since < end_ms:
        batch = ex.fetch_ohlcv(symbol, "1s", since=since, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        since = batch[-1][0] + 1000
        if progress:
            progress(pd.Timestamp(batch[-1][0], unit="ms", tz="UTC"))
    df = _to_df(rows)
    return df[df.index < end]


def fetch_forming(symbol: str, tf: int = 1) -> pd.DataFrame | None:
    """O candle que ainda está se formando (1 linha), ou None."""
    df = _to_df(exchange().fetch_ohlcv(symbol, f"{tf}m", limit=2))
    if df.empty:
        return None
    last = df.iloc[[-1]]
    if last.index[-1] + pd.Timedelta(minutes=tf) > pd.Timestamp.now(tz="UTC"):
        return last
    return None


def fetch_recent(symbol: str, n: int = 500, tf: int = 1) -> pd.DataFrame:
    from . import drop_forming

    rows = exchange().fetch_ohlcv(symbol, f"{tf}m", limit=min(n, 1000))
    return drop_forming(_to_df(rows), tf=tf)
