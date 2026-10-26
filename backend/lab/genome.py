"""Genoma de uma estratégia binária para o Laboratório Evolutivo.

Cada estratégia = 1 família (espécie) + genes do gatilho + genes de filtro.
O sinal é vetorizado e causal (valor em t usa só candles até t).
Expiração fixa = 1 candle do timeframe (o que o Ramon opera: M1 → 1 min, M5 → 5 min).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind

FAMILIES = ["reversao", "tendencia", "rompimento", "price_action"]

# nome → (mín, máx, tipo)
COMMON: dict[str, tuple] = {
    "atr_min": (0.0, 0.6, float),     # percentil mínimo de volatilidade
    "atr_span": (0.2, 1.0, float),    # largura da faixa (atr_max = min(1, atr_min + span))
    "adx_min": (0, 30, int),
    "adx_span": (10, 100, int),       # adx_max = adx_min + span
}
FAMILY_GENES: dict[str, dict[str, tuple]] = {
    "reversao": {"rsi_p": (5, 30, int), "rsi_lo": (10.0, 40.0, float), "bb_n": (10, 40, int), "bb_k": (1.5, 3.0, float)},
    "tendencia": {"ema_f": (5, 30, int), "ema_s": (30, 150, int), "touch": (0.0, 1.0, float)},
    "rompimento": {"lb": (10, 60, int), "body_k": (0.5, 3.0, float), "fade": (0, 1, int)},
    "price_action": {"wick": (0.5, 0.85, float), "streak": (0, 5, int)},
}
N_SESSIONS = 6  # blocos de 4 h UTC


def specs(family: str) -> dict[str, tuple]:
    return {**FAMILY_GENES[family], **COMMON}


def random_genome(rng: np.random.Generator, family: str | None = None) -> dict:
    fam = family or FAMILIES[int(rng.integers(len(FAMILIES)))]
    g: dict = {"family": fam}
    for k, (lo, hi, typ) in specs(fam).items():
        g[k] = int(rng.integers(lo, hi + 1)) if typ is int else float(rng.uniform(lo, hi))
    mask = rng.random(N_SESSIONS) < 0.7
    if not mask.any():
        mask[int(rng.integers(N_SESSIONS))] = True
    g["sess"] = [bool(x) for x in mask]
    return clip(g)


def clip(g: dict) -> dict:
    out = dict(g)
    for k, (lo, hi, typ) in specs(g["family"]).items():
        v = min(max(out[k], lo), hi)
        out[k] = int(round(v)) if typ is int else round(float(v), 4)
    if not any(out["sess"]):
        out["sess"] = [True] * N_SESSIONS
    return out


def key(g: dict) -> tuple:
    """Identidade do genoma (para cache e deduplicação)."""
    return (g["family"],) + tuple(g[k] for k in sorted(specs(g["family"]))) + tuple(g["sess"])


def describe(g: dict) -> list[dict]:
    """Genes normalizados 0..1 para a visão de DNA no painel."""
    out = []
    for k, (lo, hi, _typ) in specs(g["family"]).items():
        out.append({"gene": k, "value": g[k], "min": lo, "max": hi,
                    "norm": (g[k] - lo) / (hi - lo) if hi > lo else 0.0})
    out.append({"gene": "sessões", "value": "".join("■" if s else "□" for s in g["sess"]),
                "min": 0, "max": N_SESSIONS, "norm": sum(g["sess"]) / N_SESSIONS})
    return out


class Features:
    """Cache de indicadores: cada parâmetro é calculado uma única vez por DataFrame."""

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self._c: dict = {}
        o, h, low, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        self.o, self.h, self.l, self.c = o, h, low, c
        rng = np.where(h - low > 0, h - low, np.nan)
        self.body = np.abs(c - o)
        self.body_r = self.body / rng
        self.upwick_r = (h - np.maximum(c, o)) / rng
        self.lowick_r = (np.minimum(c, o) - low) / rng
        self.color = np.sign(c - o)
        color = pd.Series(self.color, index=df.index)
        groups = (color != color.shift()).cumsum()
        self.streak = ((color.groupby(groups).cumcount() + 1) * color).to_numpy()
        self.atr = ind.atr(df).to_numpy()
        self.atr_pct = ind.rolling_rank(ind.atr(df) / df["close"], 500).to_numpy()
        self.adx = ind.adx(df)[0].to_numpy()
        self.bucket = (df.index.hour // 4).to_numpy()

    def _get(self, name, fn):
        if name not in self._c:
            self._c[name] = fn()
        return self._c[name]

    def rsi(self, p: int):
        return self._get(("rsi", p), lambda: ind.rsi(self.df["close"], p).to_numpy())

    def bb(self, n: int):
        def f():
            c = self.df["close"]
            return c.rolling(n).mean().to_numpy(), c.rolling(n).std(ddof=0).to_numpy()
        return self._get(("bb", n), f)

    def ema(self, n: int):
        return self._get(("ema", n), lambda: ind.ema(self.df["close"], n).to_numpy())

    def hh(self, lb: int):
        return self._get(("hh", lb), lambda: self.df["high"].shift(1).rolling(lb).max().to_numpy())

    def ll(self, lb: int):
        return self._get(("ll", lb), lambda: self.df["low"].shift(1).rolling(lb).min().to_numpy())

    def bodymean(self, lb: int):
        return self._get(("bm", lb), lambda: pd.Series(self.body).rolling(lb).mean().shift(1).to_numpy())


def signal(g: dict, F: Features) -> np.ndarray:
    """−1 / 0 / +1 por candle."""
    fam = g["family"]
    c, o, h, low = F.c, F.o, F.h, F.l
    with np.errstate(invalid="ignore"):
        if fam == "reversao":
            r = F.rsi(g["rsi_p"])
            m, sd = F.bb(g["bb_n"])
            lower, upper = m - g["bb_k"] * sd, m + g["bb_k"] * sd
            call = (r < g["rsi_lo"]) & (c < lower)
            put = (r > 100 - g["rsi_lo"]) & (c > upper)
        elif fam == "tendencia":
            f, s = F.ema(g["ema_f"]), F.ema(g["ema_s"])
            tol = g["touch"] * F.atr
            call = (f > s) & (low <= f + tol) & (c > f) & (c > o)
            put = (f < s) & (h >= f - tol) & (c < f) & (c < o)
        elif fam == "rompimento":
            strong = F.body > g["body_k"] * F.bodymean(g["lb"])
            up = strong & (c > F.hh(g["lb"]))
            down = strong & (c < F.ll(g["lb"]))
            call, put = (down, up) if g["fade"] else (up, down)
        else:  # price_action
            prev = np.concatenate([[0], F.streak[:-1]])
            small = F.body_r <= 0.3
            call = (F.lowick_r >= g["wick"]) & small & (prev <= -g["streak"])
            put = (F.upwick_r >= g["wick"]) & small & (prev >= g["streak"])

        atr_max = min(1.0, g["atr_min"] + g["atr_span"])
        adx_max = g["adx_min"] + g["adx_span"]
        ok = (F.atr_pct >= g["atr_min"]) & (F.atr_pct <= atr_max) & (F.adx >= g["adx_min"]) & (F.adx <= adx_max)
        ok &= np.asarray(g["sess"])[F.bucket]
    call = np.nan_to_num(call & ok, nan=False).astype(bool)
    put = np.nan_to_num(put & ok, nan=False).astype(bool)
    return np.where(call, 1, np.where(put, -1, 0)).astype(np.int8)


def series(g: dict, df: pd.DataFrame, F: Features | None = None) -> pd.Series:
    return pd.Series(signal(g, F or Features(df)), index=df.index)
