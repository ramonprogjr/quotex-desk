"""Indicadores técnicos vetorizados. Todos são causais: o valor em t usa só dados até t."""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    """Média de Wilder."""
    return s.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = rma(delta.clip(lower=0), n)
    down = rma(-delta.clip(upper=0), n)
    rs = up / down.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    ma = close.rolling(n).mean()
    sd = close.rolling(n).std(ddof=0)
    return ma - k * sd, ma, ma + k * sd


def true_range(df: pd.DataFrame) -> pd.Series:
    pc = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return rma(true_range(df), n)


def stochastic(df: pd.DataFrame, n: int = 14, d: int = 3) -> tuple[pd.Series, pd.Series]:
    ll = df["low"].rolling(n).min()
    hh = df["high"].rolling(n).max()
    k = 100 * (df["close"] - ll) / (hh - ll).replace(0, np.nan)
    return k, k.rolling(d).mean()


def williams_r(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hh = df["high"].rolling(n).max()
    ll = df["low"].rolling(n).min()
    return -100 * (hh - df["close"]) / (hh - ll).replace(0, np.nan)


def cci(df: pd.DataFrame, n: int = 20) -> pd.Series:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    ma = tp.rolling(n).mean()
    md = np.full(len(tp), np.nan)
    if len(tp) >= n:
        win = np.lib.stride_tricks.sliding_window_view(tp.to_numpy(), n)
        md[n - 1:] = np.abs(win - win.mean(axis=1, keepdims=True)).mean(axis=1)
    md = pd.Series(md, index=tp.index)
    return (tp - ma) / (0.015 * md.replace(0, np.nan))


def adx(df: pd.DataFrame, n: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Retorna (ADX, +DI, −DI)."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    tr = rma(true_range(df), n).replace(0, np.nan)
    pdi = 100 * rma(plus_dm, n) / tr
    mdi = 100 * rma(minus_dm, n) / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return rma(dx, n), pdi, mdi


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series]:
    line = ema(close, fast) - ema(close, slow)
    return line, ema(line, signal)


def rolling_rank(s: pd.Series, n: int) -> pd.Series:
    """Percentil do valor atual dentro dos últimos n (0..1)."""
    return s.rolling(n, min_periods=max(20, n // 5)).rank(pct=True)


def infer_tf(index: pd.DatetimeIndex) -> int:
    """Minutos por candle, deduzido do índice."""
    if len(index) < 3:
        return 1
    return max(1, int(pd.Series(index).diff().dropna().median() / pd.Timedelta(minutes=1)))
