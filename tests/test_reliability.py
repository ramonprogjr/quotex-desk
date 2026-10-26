import asyncio

import numpy as np
import pandas as pd

from backend.agents import breaker
from backend.backtest.binary_bt import simulate
from backend.backtest.delay import delay_table, entry_prices
from backend.config import Config
from backend.journal.models import Journal
from backend.live import LiveEngine

from .conftest import make_candles


# ---------------- disjuntor ----------------
def _rec(outcome, i, setup="rsi_bb_reversal", edge_ok=True):
    return {"id": f"s{i}", "asset": "ETHUSD", "tf": 5, "setup": setup, "outcome": outcome, "approved": edge_ok,
            "edge_ok": edge_ok, "payout": 0.85, "candle_ts": f"2026-03-10T{10 + i // 60:02d}:{i % 60:02d}:00+00:00"}


def test_breaker_suspends_clearly_bad_strategy():
    recs = [_rec("loss" if i % 10 < 7 else "win", i) for i in range(50)]  # 30% de acerto
    out = breaker.evaluate(recs, {})
    k = breaker.key("ETHUSD", 5, "rsi_bb_reversal")
    assert k in out and out[k]["hi"] < out[k]["breakeven"]


def test_breaker_needs_minimum_sample():
    recs = [_rec("loss", i) for i in range(20)]
    assert breaker.evaluate(recs, {}) == {}


def test_breaker_ignores_mediocre_but_uncertain():
    recs = [_rec("win" if i % 2 else "loss", i) for i in range(50)]  # 50%: pior que o empate, mas não comprovado
    assert breaker.evaluate(recs, {}) == {}


def test_breaker_reset_counts_only_after():
    recs = [_rec("loss", i) for i in range(50)]
    k = breaker.key("ETHUSD", 5, "rsi_bb_reversal")
    assert breaker.evaluate(recs, {k: "2026-03-10T23:59:00+00:00"}) == {}


def test_breaker_counts_risk_blocked_signals():
    recs = [{**_rec("loss", i), "approved": False, "edge_ok": True} for i in range(50)]
    assert breaker.evaluate(recs, {})


def test_orchestrator_vetoes_suspended(monkeypatch):
    from backend.agents import orchestrator, risk
    from backend.assets import ASSETS

    df = make_candles(400)
    monkeypatch.setattr(orchestrator, "run_all",
                        lambda d: pd.DataFrame({"rsi_bb_reversal": [0] * (len(d) - 1) + [1]}, index=d.index))
    cell = {"disc_wins": 450, "disc_n": 700, "conf_wins": 195, "conf_n": 300}
    monkeypatch.setattr(orchestrator.prior, "lookup", lambda *a, **k: [cell])
    st = risk.RiskState(1000, 1000)
    cfg = Config()
    ok = orchestrator.evaluate(df, ASSETS["ETHUSD"], {}, st, cfg, 1)[0]
    assert ok.approved and ok.edge_ok
    k = breaker.key("ETHUSD", 1, "rsi_bb_reversal")
    sus = orchestrator.evaluate(df, ASSETS["ETHUSD"], {}, st, cfg, 1, suspended={k: {"reason": "suspensa: teste"}})[0]
    assert not sus.approved and sus.edge_ok and sus.agents["edge"].startswith("suspensa")


# ---------------- atraso ----------------
def _fine_from(df1m: pd.DataFrame) -> pd.DataFrame:
    """Dados de 1 s sintéticos coerentes com os candles de 1 min (abre no open, anda até o close)."""
    rows = []
    for ts, r in df1m.iterrows():
        path = np.linspace(r["open"], r["close"], 60)
        for s in range(60):
            rows.append((ts + pd.Timedelta(seconds=s), path[s]))
    f = pd.DataFrame(rows, columns=["ts", "open"]).set_index("ts")
    return f.assign(high=f["open"], low=f["open"], close=f["open"], volume=1.0)


def test_delay_zero_equals_normal_backtest():
    df = make_candles(400, seed=4)
    fine = _fine_from(df)
    rng = np.random.default_rng(1)
    sig = pd.Series(np.where(rng.random(len(df)) < 0.1, rng.choice([-1, 1], len(df)), 0), index=df.index)
    t = delay_table(df, fine, pd.DataFrame({"x": sig}), tf=1, expiry_candles=1, delays=[0, 30])
    base = simulate(df, sig, 1)
    dec = base[base["result"] != 0]
    row0 = t[t["delay_s"] == 0].iloc[0]
    assert row0["n"] == len(dec)
    assert abs(row0["winrate"] - (dec["result"] == 1).mean()) < 1e-9


def test_entry_prices_respects_gap():
    idx = pd.date_range("2026-01-01", periods=5, freq="1s", tz="UTC")
    fine = pd.DataFrame({"open": [1, 2, 3, 4, 5.0]}, index=idx)
    at = pd.DatetimeIndex([idx[1], idx[4] + pd.Timedelta(seconds=30)])
    p = entry_prices(fine, at)
    assert p[0] == 2 and np.isnan(p[1])


# ---------------- payout por timeframe e diário ----------------
class _J:
    def __init__(self, payouts):
        self._p = payouts

    def get_setting(self, k, d=None):
        return self._p if k == "payouts" else d

    def all(self):
        return []


async def _noop(_):
    pass


def test_payout_per_timeframe_and_legacy_format():
    cfg = Config()
    e = LiveEngine(cfg, _J({"ETHUSD": {"1": 0.80, "5": 0.88}, "BTCUSD": 0.75}), _noop)
    assert e.payout("ETHUSD", 1) == 0.80 and e.payout("ETHUSD", 5) == 0.88
    assert e.payout("BTCUSD", 5) == 0.75                      # formato antigo vale para todos
    assert e.payout("GBPUSD", 1) == cfg.payout               # sem informação → padrão


def test_journal_migration_and_quotex_exit(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as c:  # banco no formato antigo, sem quotex_exit
        c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, asset TEXT NOT NULL,"
                  " direction TEXT NOT NULL, expiry INTEGER NOT NULL, stake REAL NOT NULL, payout REAL NOT NULL,"
                  " result TEXT NOT NULL DEFAULT 'pending', pnl REAL, setup TEXT, signal_id TEXT,"
                  " followed_signal INTEGER NOT NULL DEFAULT 0, p_pred REAL, feed_price REAL, quotex_price REAL, notes TEXT)")
    j = Journal(db)
    t = j.add(asset="ETHUSD", direction="CALL", expiry=5, stake=2, payout=0.85, signal_id="abc", quotex_price=2400.5)
    t = j.set_result(t["id"], "win", quotex_exit=2402.0)
    assert t["quotex_exit"] == 2402.0 and t["quotex_price"] == 2400.5
    Journal(db)  # rodar de novo não quebra


def test_feed_vs_quotex_agreement():
    e = LiveEngine(Config(), _J({}), _noop)
    e.resolved = [{"id": "a", "outcome": "win", "entry": 100.0, "exit": 101.0},
                  {"id": "b", "outcome": "loss", "entry": 100.0, "exit": 99.0}]
    trades = [{"id": 1, "signal_id": "a", "result": "win", "quotex_price": 100.02, "quotex_exit": 101.0},
              {"id": 2, "signal_id": "b", "result": "win", "quotex_price": None, "quotex_exit": None},
              {"id": 3, "signal_id": None, "result": "loss"}]
    r = e.feed_vs_quotex(trades)
    assert r["n"] == 2 and r["agree"] == 1 and r["rate"] == 0.5
    assert abs(r["entry_bps"] - 2.0) < 1e-6 and r["exit_bps"] == 0.0
    assert r["mismatches"] == [{"id": 2, "quotex": "win", "feed": "loss"}]


def test_engine_breaker_is_sticky_and_resettable(tmp_path, monkeypatch):
    monkeypatch.setattr(breaker, "BREAKERS_PATH", tmp_path / "b.json")
    e = LiveEngine(Config(), _J({}), _noop)
    e.breakers = {"suspended": {}, "resets": {}}
    e.resolved = [_rec("loss", i) for i in range(45)]
    asyncio.run(e._update_breakers())
    k = breaker.key("ETHUSD", 5, "rsi_bb_reversal")
    assert k in e.suspended()
    e.resolved = []  # mesmo sem dados novos, continua suspensa
    asyncio.run(e._update_breakers())
    assert k in e.suspended()
    asyncio.run(e.reset_breaker(k))
    assert k not in e.suspended()
