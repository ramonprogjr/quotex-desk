"""Especialistas: cada um olha o gráfico por um ângulo e devolve features + voto.

Tudo é causal (o valor em t usa só candles até t). Os votos vão de −1 (PUT) a +1 (CALL).
As features alimentam o agente Confluência; os votos aparecem no painel.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind

FRACTAL_K = 3


# ---------------- Mapeador (suporte e resistência) ----------------
def pivots(df: pd.DataFrame, k: int = FRACTAL_K) -> tuple[pd.Series, pd.Series]:
    """Topos/fundos fractais. O pivô do candle i só é conhecido em i+k: a série fica
    no índice de confirmação (causal) com o PREÇO do pivô; NaN onde não houve pivô."""
    w = 2 * k + 1
    hk, lk = df["high"].shift(k), df["low"].shift(k)
    ph = hk.where(hk == df["high"].rolling(w).max())
    pl = lk.where(lk == df["low"].rolling(w).min())
    return ph, pl


def _round_step(price: pd.Series) -> pd.Series:
    """Passo de "número redondo" de cada candle: BTC 81.403 → 100, ETH 2.398 → 10."""
    return 10 ** (np.floor(np.log10(price.where(price > 0))) - 2)


def mapper(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    a = ind.atr(df).replace(0, np.nan)
    c, h, low, o = df["close"], df["high"], df["low"], df["open"]
    ph, pl = pivots(df)
    res, sup = ph.ffill(), pl.ffill()
    max50 = h.rolling(50).max().shift(1)
    min50 = low.rolling(50).min().shift(1)
    step = _round_step(c)
    rem = np.mod(c, step)
    near_res = ((h - res).abs() <= 0.2 * a).astype(float)
    near_sup = ((low - sup).abs() <= 0.2 * a).astype(float)
    feats = pd.DataFrame({
        "map_d_res": (res - c) / a,
        "map_d_sup": (c - sup) / a,
        "map_d_max50": (max50 - c) / a,
        "map_d_min50": (c - min50) / a,
        "map_d_round": np.minimum(rem, step - rem) / a,
        "map_touch_res": near_res.rolling(100, min_periods=1).sum(),
        "map_touch_sup": near_sup.rolling(100, min_periods=1).sum(),
    }, index=df.index)

    body = (c - o).abs()
    up_wick = h - np.maximum(c, o)
    lo_wick = np.minimum(c, o) - low
    # rejeição: o pavio encosta na zona (topo/fundo fractal ou extremo de 50) e o candle fecha de volta
    hit_res = ((h >= res - 0.2 * a) & (c < res)) | ((h >= max50 - 0.2 * a) & (c < max50))
    hit_sup = ((low <= sup + 0.2 * a) & (c > sup)) | ((low <= min50 + 0.2 * a) & (c > min50))
    put = hit_res & (up_wick > body) & (up_wick > lo_wick)
    call = hit_sup & (lo_wick > body) & (lo_wick > up_wick)
    vote = pd.Series(np.where(call, 1.0, np.where(put, -1.0, 0.0)), index=df.index)
    return feats, vote


def zones(df: pd.DataFrame, n: int = 300, max_zones: int = 6) -> list[dict]:
    """Zonas de S/R recentes: pivôs agrupados com tolerância de 1 ATR."""
    if len(df) < 30:
        return []
    tail = df.tail(n)
    ph, pl = pivots(tail)
    levels = sorted([(float(p), "high") for p in ph.dropna()] + [(float(p), "low") for p in pl.dropna()])
    if not levels:
        return []
    a = float(ind.atr(df).iloc[-1] or 0) or float((tail["high"] - tail["low"]).mean())
    tol = 1.0 * a
    groups: list[list[tuple[float, str]]] = [[levels[0]]]
    for lv in levels[1:]:
        if lv[0] - groups[-1][-1][0] <= tol:
            groups[-1].append(lv)
        else:
            groups.append([lv])
    last = float(df["close"].iloc[-1])
    out = []
    for g in groups:
        price = float(np.mean([p for p, _ in g]))
        out.append({"price": price, "kind": "res" if price >= last else "sup", "touches": len(g),
                    "dist_atr": abs(price - last) / a if a else None})
    out.sort(key=lambda z: (-z["touches"], z["dist_atr"] or 0))
    return sorted(out[:max_zones], key=lambda z: z["price"])


# ---------------- Tendência ----------------
def trend(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    c = df["close"]
    a = ind.atr(df).replace(0, np.nan)
    e9, e21, e50 = ind.ema(c, 9), ind.ema(c, 21), ind.ema(c, 50)
    adx, pdi, mdi = ind.adx(df)
    m, ms = ind.macd(c)
    feats = pd.DataFrame({
        "tr_e9_21": (e9 - e21) / a,
        "tr_c_e50": (c - e50) / a,
        "tr_slope50": (e50 - e50.shift(5)) / a,
        "tr_adx": adx,
        "tr_di": (pdi - mdi) / 100,
        "tr_macd_h": (m - ms) / a,
    }, index=df.index)
    s = np.sign(feats["tr_e9_21"])
    vote = (s * ((adx > 20) & (np.sign(feats["tr_slope50"]) == s))).fillna(0.0)
    return feats, vote.astype(float)


# ---------------- Exaustão (osciladores) ----------------
def exhaustion(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    c = df["close"]
    r = ind.rsi(c)
    k, d = ind.stochastic(df)
    wr = ind.williams_r(df)
    cc = ind.cci(df)
    lower, _, upper = ind.bollinger(c)
    pctb = (c - lower) / (upper - lower).replace(0, np.nan)
    feats = pd.DataFrame({"ex_rsi": r, "ex_stoch_k": k, "ex_stoch_d": d, "ex_willr": wr,
                          "ex_cci": cc, "ex_pctb": pctb}, index=df.index)
    ob = (r > 70).astype(int) + (k > 80) + (wr > -20) + (cc > 100) + (pctb > 1)
    os_ = (r < 30).astype(int) + (k < 20) + (wr < -80) + (cc < -100) + (pctb < 0)
    vote = np.where((ob >= 2) & (ob > os_), -ob / 5, np.where((os_ >= 2) & (os_ > ob), os_ / 5, 0.0))
    return feats, pd.Series(vote, index=df.index, dtype=float)


# ---------------- Price action ----------------
def price_action(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    o, h, low, c = df["open"], df["high"], df["low"], df["close"]
    a = ind.atr(df).replace(0, np.nan)
    rng = (h - low).replace(0, np.nan)
    body = (c - o).abs()
    up_wick = h - np.maximum(c, o)
    lo_wick = np.minimum(c, o) - low
    color = np.sign(c - o)
    groups = (color != color.shift()).cumsum()
    streak = (color.groupby(groups).cumcount() + 1) * color
    feats = pd.DataFrame({
        "pa_body_atr": body / a,
        "pa_upwick": up_wick / rng,
        "pa_lowick": lo_wick / rng,
        "pa_color": color,
        "pa_streak": streak.clip(-8, 8),
        "pa_ret1": (c - c.shift(1)) / a,
        "pa_ret3": (c - c.shift(3)) / a,
        "pa_ret5": (c - c.shift(5)) / a,
    }, index=df.index)
    bull_pin = (lo_wick / rng >= 0.6) & (body / rng <= 0.3)
    bear_pin = (up_wick / rng >= 0.6) & (body / rng <= 0.3)
    vote = pd.Series(np.where(bull_pin, 1.0, np.where(bear_pin, -1.0, 0.0)), index=df.index)
    return feats, vote


# ---------------- Volatilidade, horário, volume (filtros, sem direção) ----------------
def context(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    a = ind.atr(df)
    lower, mid, upper = ind.bollinger(c)
    bbw = (upper - lower) / mid
    idx = df.index
    tf = ind.infer_tf(idx)
    hour = idx.hour + idx.minute / 60
    vol = df["volume"]
    return pd.DataFrame({
        "vo_atr_pct": ind.rolling_rank(a / c, 500),
        "vo_bbw_pct": ind.rolling_rank(bbw, 500),
        "hr_sin": np.sin(2 * np.pi * hour / 24),
        "hr_cos": np.cos(2 * np.pi * hour / 24),
        "hr_quad": (idx.minute % 5) if tf == 1 else ((idx.minute // 5) % 6),
        "hr_dow": idx.dayofweek,
        "vl_ratio": vol / vol.rolling(20).mean().shift(1).replace(0, np.nan),
    }, index=idx)


SPECIALISTS = {
    "mapeador": mapper,
    "tendencia": trend,
    "exaustao": exhaustion,
    "price_action": price_action,
}


def analyze(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Retorna (features, votos) para todo o df."""
    feats, votes = [], {}
    for name, fn in SPECIALISTS.items():
        f, v = fn(df)
        feats.append(f)
        votes[name] = v
    feats.append(context(df))
    votes_df = pd.DataFrame(votes, index=df.index)
    X = pd.concat([*feats, votes_df.add_prefix("vote_")], axis=1)
    return X.replace([np.inf, -np.inf], np.nan), votes_df


def snapshot(df: pd.DataFrame) -> dict:
    """Estado atual dos especialistas para o painel."""
    X, votes = analyze(df)
    last_x, last_v = X.iloc[-1], votes.iloc[-1]

    def f(k):
        v = last_x.get(k)
        return None if v is None or pd.isna(v) else float(v)

    return {
        "votes": {k: float(v) for k, v in last_v.items()},
        "volatility_pct": f("vo_atr_pct"),
        "adx": f("tr_adx"),
        "rsi": f("ex_rsi"),
        "stoch_k": f("ex_stoch_k"),
        "streak": f("pa_streak"),
    }
