import numpy as np
import pandas as pd
import pytest


def make_candles(n: int = 5000, seed: int = 0, start: str = "2026-01-01") -> pd.DataFrame:
    """Passeio aleatório: não há vantagem possível nesses dados."""
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.0008, n)))
    open_ = np.concatenate([[100.0], close[:-1]])
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.0004, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.0004, n))
    idx = pd.date_range(start, periods=n, freq="1min", tz="UTC", name="ts")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close,
                         "volume": rng.uniform(1, 10, n)}, index=idx)


@pytest.fixture
def candles() -> pd.DataFrame:
    return make_candles()
