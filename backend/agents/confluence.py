"""Confluência: aprende com o histórico qual combinação dos especialistas antecipa a direção.

Regras para não se enganar:
- treino SEMPRE no passado e previsão no período seguinte (walk-forward expanding);
- o limiar de confiança vem de um trecho de validação dentro do treino, não do teste;
- a série gerada fora da amostra vira o setup "confluencia" e passa pelo mesmo Edge.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from ..config import DATA_DIR
from . import specialists

NAME = "confluencia"


def model_path(asset: str, tf: int):
    return DATA_DIR / f"model_{asset.upper()}_M{tf}.joblib"


def oos_path(asset: str, tf: int):
    return DATA_DIR / f"oos_{asset.upper()}_M{tf}.parquet"


def target(df: pd.DataFrame, expiry_candles: int) -> pd.Series:
    """+1 se o preço da expiração ficou acima da entrada (abertura do próximo candle), −1 abaixo, 0 empate."""
    move = df["close"].shift(-expiry_candles) - df["open"].shift(-1)
    return np.sign(move)


def _model() -> HistGradientBoostingClassifier:
    return HistGradientBoostingClassifier(max_iter=150, learning_rate=0.05, max_leaf_nodes=15,
                                          min_samples_leaf=300, l2_regularization=1.0,
                                          early_stopping=False, random_state=7)


def _fit(X: pd.DataFrame, y: pd.Series, top_q: float) -> tuple[HistGradientBoostingClassifier, float]:
    """Treina nos primeiros 80% e calibra o limiar (top_q mais confiantes) nos 20% finais."""
    cut = int(len(X) * 0.8)
    m = _model().fit(X.iloc[:cut], (y.iloc[:cut] > 0).astype(int))
    conf = np.abs(m.predict_proba(X.iloc[cut:])[:, 1] - 0.5)
    thr = float(np.quantile(conf, 1 - top_q)) if len(conf) else 0.5
    return m, thr


def _signal(p: np.ndarray, thr: float) -> np.ndarray:
    return np.where(np.abs(p - 0.5) >= thr, np.sign(p - 0.5), 0).astype(np.int8)


@dataclass
class WFResult:
    signal: pd.Series      # −1/0/+1 fora da amostra
    proba: pd.Series       # probabilidade de alta fora da amostra
    folds: list[dict]


def walkforward(df: pd.DataFrame, expiry_candles: int, min_train_days: int = 60,
                step_days: int = 30, top_q: float = 0.05, progress=None) -> WFResult:
    X, _ = specialists.analyze(df)
    y = target(df, expiry_candles)
    valid = y.notna() & (y != 0)
    proba = pd.Series(np.nan, index=df.index)
    sig = pd.Series(np.zeros(len(df), dtype=np.int8), index=df.index)
    folds = []
    start = df.index[0] + pd.Timedelta(days=min_train_days)
    gap = pd.Timedelta(minutes=(expiry_candles + 1) * max(1, int((df.index[1] - df.index[0]).total_seconds() // 60)))
    while start < df.index[-1]:
        end = start + pd.Timedelta(days=step_days)
        tr = valid & (df.index < start - gap)          # o alvo do treino não pode invadir o teste
        te = (df.index >= start) & (df.index < end)
        if tr.sum() < 5000 or te.sum() == 0:
            start = end
            continue
        m, thr = _fit(X[tr], y[tr], top_q)
        p = m.predict_proba(X[te])[:, 1]
        proba[te] = p
        sig[te] = _signal(p, thr)
        folds.append({"start": str(start.date()), "train": int(tr.sum()), "test": int(te.sum()),
                      "thr": thr, "fired": int((sig[te] != 0).sum())})
        if progress:
            progress(folds[-1])
        start = end
    return WFResult(sig, proba, folds)


def train_final(df: pd.DataFrame, asset: str, tf: int, expiry: int, expiry_candles: int,
                top_q: float = 0.05) -> dict:
    X, _ = specialists.analyze(df)
    y = target(df, expiry_candles)
    valid = y.notna() & (y != 0)
    m, thr = _fit(X[valid], y[valid], top_q)
    bundle = {"model": m, "thr": thr, "columns": list(X.columns), "asset": asset.upper(), "tf": tf,
              "expiry": expiry, "trained_at": datetime.now(timezone.utc).isoformat(),
              "range": [str(df.index[0]), str(df.index[-1])]}
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, model_path(asset, tf))
    return bundle


def load(asset: str, tf: int) -> dict | None:
    p = model_path(asset, tf)
    return joblib.load(p) if p.exists() else None


def load_oos(asset: str, tf: int) -> pd.Series | None:
    p = oos_path(asset, tf)
    if not p.exists():
        return None
    return pd.read_parquet(p)["signal"]


def save_oos(asset: str, tf: int, wf: WFResult) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"signal": wf.signal, "proba": wf.proba}).to_parquet(oos_path(asset, tf))


def predict_last(df: pd.DataFrame, bundle: dict) -> tuple[float, int]:
    """(probabilidade de alta, direção −1/0/+1) para o último candle de df."""
    X, _ = specialists.analyze(df)
    x = X.iloc[[-1]].reindex(columns=bundle["columns"])
    p = float(bundle["model"].predict_proba(x)[:, 1][0])
    return p, int(_signal(np.array([p]), bundle["thr"])[0])
