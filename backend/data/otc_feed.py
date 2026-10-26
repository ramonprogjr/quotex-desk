"""Candles OTC a partir dos ticks gravados localmente (backend.otc.recorder).

Não existe fonte externa para OTC: o preço só existe na Quotex. Os ticks chegam pelo Chrome do robô,
um arquivo CSV por par e por dia. Aqui eles são lidos de forma incremental (só o que foi acrescentado
desde a última leitura) e agrupados em candles alinhados ao relógio.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from ..otc import TICKS_DIR

_lock = threading.Lock()
_files: dict[str, tuple[int, np.ndarray, np.ndarray]] = {}  # caminho → (bytes lidos, ts, price)


def _read(path) -> tuple[np.ndarray, np.ndarray]:
    key = str(path)
    off, ts, px = _files.get(key, (0, np.empty(0), np.empty(0)))
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return ts, px
    if size > off:
        with open(path, "rb") as f:
            f.seek(off)
            chunk = f.read(size - off)
        end = chunk.rfind(b"\n") + 1  # a última linha pode estar pela metade
        if end:
            rows = [ln.split(b",") for ln in chunk[:end].splitlines() if ln and not ln.startswith(b"ts")]
            if rows:
                arr = np.array(rows, dtype=float)
                ts, px = np.concatenate([ts, arr[:, 0]]), np.concatenate([px, arr[:, 1]])
            off += end
        _files[key] = (off, ts, px)
    return ts, px


def ticks(symbol: str, start: pd.Timestamp, end: pd.Timestamp | None = None) -> pd.Series:
    """Preço por instante (UTC), sem repetidos, entre start e end."""
    end = end or pd.Timestamp.now(tz="UTC")
    parts_t, parts_p = [], []
    with _lock:
        for day in pd.date_range(start.normalize(), end.normalize(), freq="D"):
            t, p = _read(TICKS_DIR / symbol / f"{day:%Y-%m-%d}.csv")
            parts_t.append(t)
            parts_p.append(p)
    t = np.concatenate(parts_t) if parts_t else np.empty(0)
    p = np.concatenate(parts_p) if parts_p else np.empty(0)
    s = pd.Series(p, index=pd.to_datetime(t, unit="s", utc=True))
    s = s[~s.index.duplicated(keep="last")].sort_index()  # o histórico da reconexão repete ticks
    return s[(s.index >= start) & (s.index < end)]


def _candles(s: pd.Series, rule: str) -> pd.DataFrame:
    g = s.resample(rule, label="left", closed="left")
    out = pd.DataFrame({"open": g.first(), "high": g.max(), "low": g.min(), "close": g.last(),
                        "volume": g.count().astype(float)})
    out = out[out["volume"] > 0]
    out.index.name = "ts"
    return out


def fetch_history(symbol: str, start: pd.Timestamp, end: pd.Timestamp, progress=None) -> pd.DataFrame:
    from . import drop_forming

    return drop_forming(_candles(ticks(symbol, start, end), "1min"))


def fetch_fine(symbol: str, start: pd.Timestamp, end: pd.Timestamp, progress=None) -> pd.DataFrame:
    return _candles(ticks(symbol, start, end), "1s")


def fetch_recent(symbol: str, n: int = 500, tf: int = 1) -> pd.DataFrame:
    from . import drop_forming, resample

    now = pd.Timestamp.now(tz="UTC")
    df = drop_forming(_candles(ticks(symbol, now - pd.Timedelta(minutes=n * tf + tf)), "1min"), now)
    return drop_forming(resample(df, tf), now, tf).tail(n)


def fetch_forming(symbol: str, tf: int = 1) -> pd.DataFrame | None:
    now = pd.Timestamp.now(tz="UTC")
    start = now.floor(f"{tf}min")
    s = ticks(symbol, start, now + pd.Timedelta(seconds=1))
    if s.empty:
        return None
    return pd.DataFrame({"open": [s.iloc[0]], "high": [s.max()], "low": [s.min()], "close": [s.iloc[-1]],
                         "volume": [float(len(s))]}, index=pd.DatetimeIndex([start], name="ts"))


def last_tick_age(symbol: str) -> float | None:
    """Segundos desde o último tick gravado (None se o par nunca foi gravado)."""
    d = TICKS_DIR / symbol
    files = sorted(d.glob("*.csv")) if d.exists() else []
    if not files:
        return None
    return datetime.now(timezone.utc).timestamp() - files[-1].stat().st_mtime
