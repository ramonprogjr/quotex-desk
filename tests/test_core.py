import pandas as pd
import pytest

from backend.agents import edge, kelly, risk
from backend.agents.spotter import SETUPS, random_baseline
from backend.assets import get_asset, is_otc
from backend.backtest.binary_bt import equity_curve, simulate, summarize
from backend.config import Config
from backend.stats import breakeven, wilson

from .conftest import make_candles


def test_breakeven():
    assert breakeven(0.85) == pytest.approx(0.5405, abs=1e-4)
    assert breakeven(1.0) == 0.5


def test_wilson_contains_p():
    lo, hi = wilson(60, 100)
    assert lo < 0.6 < hi
    assert wilson(0, 0) == (0.0, 1.0)


# ---------------- Spotter ----------------
@pytest.mark.parametrize("name", list(SETUPS))
def test_setups_do_not_look_ahead(candles, name):
    fn = SETUPS[name]
    full = fn(candles)
    for t in (300, 1200, 3000, 4999):
        partial = fn(candles.iloc[: t + 1])
        assert partial.iloc[-1] == full.iloc[t], f"{name} mudou no candle {t} ao ver o futuro"


# ---------------- Backtest ----------------
def test_simulate_execution_model():
    df = make_candles(50)
    d = pd.Series(0, index=df.index)
    d.iloc[10] = 1
    tr = simulate(df, d, expiry=3)
    assert len(tr) == 1
    assert tr["entry"].iloc[0] == df["open"].iloc[11]
    assert tr["exit"].iloc[0] == df["close"].iloc[13]


def test_one_at_a_time_blocks_overlap():
    df = make_candles(50)
    d = pd.Series(0, index=df.index)
    d.iloc[[10, 11, 12, 13]] = 1
    assert list(simulate(df, d, expiry=3).index) == [df.index[10], df.index[13]]


def test_skips_windows_with_gaps():
    df = make_candles(50)
    df = df.drop(df.index[12])  # buraco nos dados
    d = pd.Series(0, index=df.index)
    d.iloc[10] = 1
    assert simulate(df, d, expiry=3).empty


def test_random_baseline_has_no_edge():
    df = make_candles(60_000, seed=7)
    tr = simulate(df, random_baseline(df, rate=0.2), expiry=1)
    s = summarize(tr, payout=0.85)
    assert 0.48 < s.winrate < 0.52
    assert not s.edge_proven
    assert s.ev_per_unit < 0
    assert equity_curve(tr, 0.85).iloc[-1] < 100


# ---------------- Edge / Kelly ----------------
def _cell(dw, dn, cw, cn):
    return {"disc_wins": dw, "disc_n": dn, "conf_wins": cw, "conf_n": cn}


def test_edge_rejects_coin_flip_and_accepts_real_edge():
    cfg = Config()
    assert not edge.judge(_cell(350, 700, 150, 300), 0.85, cfg).approved
    assert edge.judge(_cell(455, 700, 195, 300), 0.85, cfg).approved      # 65%
    assert not edge.judge(_cell(455, 700, 150, 300), 0.85, cfg).approved  # não confirmou
    assert not edge.judge(None, 0.85, cfg).approved


def test_kelly_zero_without_edge():
    cfg = Config()
    assert kelly.size(0.54, 0.85, 1000, cfg).stake == 0
    assert kelly.size(0.50, 0.85, 1000, cfg).stake == 0


def test_kelly_capped():
    cfg = Config()
    s = kelly.size(0.70, 0.85, 1000, cfg)
    assert 0 < s.stake <= 1000 * cfg.max_stake_frac


# ---------------- Closer (risco) ----------------
NOW = pd.Timestamp("2026-03-10 15:00", tz="UTC")


def _trade(minutes_ago, result, stake=10.0, payout=0.85):
    pnl = {"win": stake * payout, "loss": -stake, "push": 0.0, "pending": None}[result]
    return {"created_at": (NOW - pd.Timedelta(minutes=minutes_ago)).isoformat(),
            "result": result, "stake": stake, "pnl": pnl}


def test_daily_stop_blocks():
    cfg = Config()
    st = risk.state_from_journal([_trade(m, "loss") for m in (50, 40, 30, 20, 10, 5)], 1000, cfg, NOW)
    # 6 perdas de 10 = -60 = 6% → stop
    assert not risk.check(st, 10, cfg, NOW).allowed


def test_cooldown_after_consecutive_losses():
    cfg = Config()
    st = risk.state_from_journal([_trade(10, "loss"), _trade(5, "loss")], 1000, cfg, NOW)
    d = risk.check(st, 10, cfg, NOW)
    assert not d.allowed and "pausa" in d.reasons[0]
    later = NOW + pd.Timedelta(minutes=cfg.cooldown_minutes + 1)
    assert risk.check(st, 10, cfg, later).allowed


def test_no_martingale_after_loss():
    cfg = Config()
    st = risk.state_from_journal([_trade(5, "loss", stake=10)], 1000, cfg, NOW)
    d = risk.check(st, 25, cfg, NOW)
    assert d.allowed and d.stake == 10


def test_sizing_uses_lower_bankroll():
    cfg = Config()
    st = risk.state_from_journal([_trade(5, "loss", stake=10)], 1000, cfg, NOW)
    assert risk.base_bankroll(st) == 990


def test_open_trade_blocks():
    cfg = Config()
    st = risk.state_from_journal([_trade(1, "pending")], 1000, cfg, NOW)
    assert not risk.check(st, 10, cfg, NOW).allowed


def test_max_trades_per_day():
    cfg = Config(max_trades_day=3)
    st = risk.state_from_journal([_trade(m, "win") for m in (30, 20, 10)], 1000, cfg, NOW)
    assert not risk.check(st, 10, cfg, NOW).allowed


def test_yesterday_does_not_count_today():
    cfg = Config()
    old = [_trade(60 * 24 + m, "loss") for m in (1, 2, 3, 4, 5, 6, 7)]
    st = risk.state_from_journal(old, 1000, cfg, NOW)
    assert st.trades_today == 0 and st.day_start_bankroll == 930
    assert risk.check(st, 10, cfg, NOW).allowed


# ---------------- M5 ----------------
def test_resample_m5_aligned_and_complete():
    from backend.data import resample

    df = make_candles(21, start="2026-01-01 00:02")  # 00:02 → 00:22: blocos 00:00 e 00:20 incompletos
    m5 = resample(df, 5)
    assert all(ts.minute % 5 == 0 for ts in m5.index)
    first = m5.index[0]
    src = df[(df.index >= first) & (df.index < first + pd.Timedelta(minutes=5))]
    assert m5["open"].iloc[0] == src["open"].iloc[0] and m5["close"].iloc[0] == src["close"].iloc[-1]
    assert m5["high"].iloc[0] == src["high"].max()
    assert list(m5.index.minute) == [5, 10, 15]


def test_m5_expiry_is_next_candle():
    from backend.data import resample

    m5 = resample(make_candles(600), 5)
    d = pd.Series(0, index=m5.index)
    d.iloc[10] = -1
    tr = simulate(m5, d, expiry=Config.expiry_candles(5, 5), tf=5)
    assert tr["entry"].iloc[0] == m5["open"].iloc[11] and tr["exit"].iloc[0] == m5["close"].iloc[11]


def test_expiry_must_be_multiple_of_tf():
    with pytest.raises(ValueError):
        Config.expiry_candles(3, 5)


# ---------------- Ativos ----------------
def test_otc_is_rejected():
    assert is_otc("EUR/USD (OTC)")
    with pytest.raises(ValueError):
        get_asset("EURUSD-OTC")
    assert get_asset("btcusd").symbol == "BTC/USDT"
