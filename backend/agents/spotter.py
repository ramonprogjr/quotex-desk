"""Spotter: detecta setups no fechamento de cada candle.

Cada setup recebe o DataFrame de candles e devolve uma série alinhada com valores
+1 (CALL), -1 (PUT) ou 0 (nada). O valor no candle t usa apenas dados até t
(sem olhar o futuro) — há um teste garantindo isso.
"""
from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from ..indicators import bollinger, ema, infer_tf, rsi
from . import specialists


def _dir(call: pd.Series, put: pd.Series) -> pd.Series:
    out = np.where(call.fillna(False), 1, np.where(put.fillna(False), -1, 0))
    return pd.Series(out.astype(np.int8), index=call.index)


# ---------------- setups clássicos ----------------
def rsi_bb_reversal(df: pd.DataFrame) -> pd.Series:
    """Exaustão: RSI extremo e fechamento fora da banda de Bollinger → aposta na reversão."""
    r = rsi(df["close"])
    lower, _, upper = bollinger(df["close"])
    return _dir((r < 25) & (df["close"] < lower), (r > 75) & (df["close"] > upper))


def engulfing_reversal(df: pd.DataFrame) -> pd.Series:
    """Engolfo depois de 3 candles na mesma direção."""
    o, c = df["open"], df["close"]
    red, green = c < o, c > o
    three_red = red.shift(1, fill_value=False) & red.shift(2, fill_value=False) & red.shift(3, fill_value=False)
    three_green = (green.shift(1, fill_value=False) & green.shift(2, fill_value=False)
                   & green.shift(3, fill_value=False))
    bull = green & three_red & (o <= c.shift(1)) & (c >= o.shift(1))
    bear = red & three_green & (o >= c.shift(1)) & (c <= o.shift(1))
    return _dir(bull, bear)


def breakout_momentum(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """Rompimento da máxima/mínima de n candles com corpo forte → aposta na continuação."""
    body = (df["close"] - df["open"]).abs()
    strong = body > 1.5 * body.rolling(n).mean().shift(1)
    hh = df["high"].shift(1).rolling(n).max()
    ll = df["low"].shift(1).rolling(n).min()
    return _dir(strong & (df["close"] > hh), strong & (df["close"] < ll))


def ema_pullback(df: pd.DataFrame) -> pd.Series:
    """Tendência (EMA21 vs EMA89) + recuo até a EMA21 com rejeição."""
    c, o = df["close"], df["open"]
    fast, slow = ema(c, 21), ema(c, 89)
    up = (fast > slow) & (df["low"] <= fast) & (c > fast) & (c > o)
    down = (fast < slow) & (df["high"] >= fast) & (c < fast) & (c < o)
    return _dir(up, down)


# ---------------- setups dos especialistas ----------------
def sr_rejection(df: pd.DataFrame) -> pd.Series:
    """Mapeador: pavio rejeitando suporte/resistência (topo/fundo fractal ou extremo de 50)."""
    _, vote = specialists.mapper(df)
    return vote.astype(np.int8)


def pinbar_zone(df: pd.DataFrame) -> pd.Series:
    """Pin bar a até 0,5 ATR de uma zona de S/R, a favor do pavio."""
    feats, _ = specialists.mapper(df)
    _, pin = specialists.price_action(df)
    near_sup = feats["map_d_sup"].abs() <= 0.5
    near_res = feats["map_d_res"].abs() <= 0.5
    return _dir((pin > 0) & near_sup, (pin < 0) & near_res)


def mhi(df: pd.DataFrame) -> pd.Series:
    """MHI (só M1): no último candle do quadrante de 5 min, olha as 3 últimas cores e
    entra na MINORIA no 1º candle do próximo quadrante. Sem gale. Doji anula."""
    zero = pd.Series(np.zeros(len(df), dtype=np.int8), index=df.index)
    if infer_tf(df.index) != 1:
        return zero
    color = np.sign(df["close"] - df["open"])
    c3 = pd.concat([color, color.shift(1), color.shift(2)], axis=1)
    has_doji = (c3 == 0).any(axis=1) | c3.isna().any(axis=1)
    majority = np.sign(c3.sum(axis=1))
    last_of_quadrant = pd.Series(df.index.minute % 5 == 4, index=df.index)
    # os 3 candles precisam ser do mesmo quadrante (sem buraco nos dados)
    contiguous = pd.Series((df.index - df.index.to_series().shift(2)) == pd.Timedelta(minutes=2), index=df.index)
    fire = last_of_quadrant & ~has_doji & contiguous
    return _dir(fire & (majority < 0), fire & (majority > 0))


def random_baseline(df: pd.DataFrame, rate: float = 0.05, seed: int = 42) -> pd.Series:
    """Cara ou coroa. Só para validar o motor: deve dar ~50% de acerto e banca caindo."""
    rng = np.random.default_rng(seed)
    fire = rng.random(len(df)) < rate
    side = np.where(rng.random(len(df)) < 0.5, 1, -1)
    return pd.Series(np.where(fire, side, 0).astype(np.int8), index=df.index)


SETUPS: dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "rsi_bb_reversal": rsi_bb_reversal,
    "engulfing_reversal": engulfing_reversal,
    "breakout_momentum": breakout_momentum,
    "ema_pullback": ema_pullback,
    "sr_rejection": sr_rejection,
    "pinbar_zone": pinbar_zone,
    "mhi": mhi,
}
BASELINES = {"random": random_baseline}


def run_all(df: pd.DataFrame, include_baseline: bool = False,
            extra: dict[str, pd.Series] | None = None) -> pd.DataFrame:
    """Matriz candle × setup com as direções (usada no 'espectro de sinais').
    `extra`: séries prontas (ex.: Confluência fora da amostra) somadas à matriz."""
    fns = {**SETUPS, **(BASELINES if include_baseline else {})}
    out = pd.DataFrame({name: fn(df) for name, fn in fns.items()}, index=df.index)
    for k, s in (extra or {}).items():
        out[k] = s.reindex(df.index).fillna(0).astype(np.int8)
    return out
