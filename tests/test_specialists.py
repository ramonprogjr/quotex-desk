import numpy as np
import pandas as pd
import pytest

from backend.agents import confluence, specialists
from backend.agents.spotter import mhi
from backend.backtest.binary_bt import simulate, summarize
from backend.data import resample

from .conftest import make_candles


def test_features_and_votes_do_not_look_ahead():
    df = make_candles(1500, seed=3)
    X_full, V_full = specialists.analyze(df)
    for t in (600, 1100, 1499):
        X_p, V_p = specialists.analyze(df.iloc[: t + 1])
        pd.testing.assert_series_equal(V_p.iloc[-1], V_full.iloc[t], check_names=False)
        a, b = X_p.iloc[-1], X_full.iloc[t]
        both = a.notna() & b.notna()
        assert (a.isna() == b.isna()).all(), f"NaN diferente em t={t}"
        np.testing.assert_allclose(a[both].to_numpy(float), b[both].to_numpy(float), rtol=1e-9, atol=1e-9)


def test_fractal_known_only_k_candles_later():
    df = make_candles(60)
    df.iloc[30, df.columns.get_loc("high")] = df["high"].max() + 10  # topo claro no candle 30
    ph, _ = specialists.pivots(df, k=3)
    assert pd.isna(ph.iloc[30]) and pd.isna(ph.iloc[32])
    assert ph.iloc[33] == df["high"].iloc[30]


def test_mhi_enters_minority_on_last_candle_of_quadrant():
    idx = pd.date_range("2026-01-01 00:00", periods=10, freq="1min", tz="UTC", name="ts")
    o = np.full(10, 100.0)
    c = o + np.array([1, 1, 1, 1, -1, 1, 1, 1, 1, 1])  # minutos 2,3,4: verde, verde, vermelho → maioria verde
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) + .1, "low": np.minimum(o, c) - .1,
                       "close": c, "volume": 1.0}, index=idx)
    s = mhi(df)
    assert s.iloc[4] == -1          # minoria = vermelho → PUT
    assert (s.drop(s.index[[4, 9]]) == 0).all()


def test_mhi_silent_on_m5():
    m5 = resample(make_candles(3000), 5)
    assert (mhi(m5) == 0).all()


def test_zones_cluster_and_classify():
    df = make_candles(400, seed=5)
    z = specialists.zones(df)
    assert z and all(k in z[0] for k in ("price", "kind", "touches"))
    last = df["close"].iloc[-1]
    assert all((zz["kind"] == "res") == (zz["price"] >= last) for zz in z)


@pytest.mark.slow
def test_confluence_finds_nothing_in_random_walk():
    df = make_candles(150_000, seed=11)   # ~104 dias de passeio aleatório
    wf = confluence.walkforward(df, expiry_candles=1, min_train_days=40, step_days=20)
    tr = simulate(df, wf.signal, 1)
    s = summarize(tr, payout=0.85)
    assert s.trades > 200
    assert 0.45 < s.winrate < 0.55
    assert not s.edge_proven
