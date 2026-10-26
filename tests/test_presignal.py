import asyncio

import pandas as pd

from backend.agents.orchestrator import Signal
from backend.config import Config
from backend.live import LiveEngine

from .conftest import make_candles


class FakeJournal:
    def get_setting(self, key, default=None):
        return default

    def all(self):
        return []


def _engine():
    events = []

    async def bc(msg):
        events.append(msg)

    return LiveEngine(Config(), FakeJournal(), bc), events


def _sig(setup="rsi_bb_reversal", direction="PUT", approved=True):
    return Signal(id="x", asset="ETHUSD", quotex="Ethereum", tf=1, setup=setup, direction=direction,
                  candle_ts="", entry_at="", valid_until="", expiry=1, expires_at="", price=1.0,
                  approved=approved, stake=1.0, p_hat=.6, lo=.56, breakeven=.54, n=500, payout=.85)


def test_live_frame_appends_forming_candle():
    eng, _ = _engine()
    df = make_candles(200)
    eng.frames["ETHUSD|1"] = df
    forming = make_candles(1, start=str(df.index[-1] + pd.Timedelta(minutes=1)))
    eng.forming["ETHUSD|1"] = forming
    lf = eng.live_frame("ETHUSD", 1)
    assert len(lf) == 201 and lf.index[-1] == forming.index[0]
    eng.forming["ETHUSD|1"] = df.iloc[[-1]]  # candle velho não é anexado
    assert len(eng.live_frame("ETHUSD", 1)) == 200


def test_presignal_confirmed_canceled_and_surprise():
    eng, events = _engine()
    ts = "2026-03-10T15:00:00+00:00"
    eng.presignals["ETHUSD|1"] = {"candle_ts": ts, "seen": {"rsi_bb_reversal|PUT": True, "mhi|CALL": True,
                                                             "ema_pullback|CALL": False}}
    official = [_sig("rsi_bb_reversal", "PUT"), _sig("confluencia", "CALL")]
    asyncio.run(eng._settle_presignal("ETHUSD", 1, ts, official))
    res = next(e for e in events if e["type"] == "presignal_result")["data"]["items"]
    status = {r["key"]: r["status"] for r in res}
    assert status == {"rsi_bb_reversal|PUT": "confirmed", "mhi|CALL": "canceled", "confluencia|CALL": "surprise"}
    assert eng.pre_stats == {"announced": 2, "confirmed": 1, "canceled": 1, "surprise": 1}
    assert "ETHUSD|1" not in eng.presignals


def test_seconds_left_aligned_to_clock():
    eng, _ = _engine()
    assert eng.seconds_left(5, pd.Timestamp("2026-03-10 15:03:30", tz="UTC")) == 90
    assert eng.seconds_left(1, pd.Timestamp("2026-03-10 15:03:50", tz="UTC")) == 10
