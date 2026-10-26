"""Modo teste OTC do robô: limites do dia contados no log próprio (fora do diário)."""
import json
from datetime import datetime, timezone

import backend.bot.__main__ as bot
from backend.config import Config


def _write(tmp_path, rows):
    p = tmp_path / "t.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return p


def test_day_totals_uses_last_line_per_entry_and_local_day(tmp_path, monkeypatch):
    rows = [
        {"id": "a", "at": "2026-10-26T15:00:00+00:00", "stake": 100, "result": "pending"},
        {"id": "a", "at": "2026-10-26T15:00:00+00:00", "stake": 100, "result": "win", "pnl": 92.0},
        {"id": "b", "at": "2026-10-26T16:00:00+00:00", "stake": 100, "result": "loss", "pnl": -100.0},
        {"id": "c", "at": "2026-10-26T17:00:00+00:00", "stake": 100, "result": "pending"},  # aberta = perda
        {"id": "d", "at": "2026-10-26T02:00:00+00:00", "stake": 100, "result": "loss", "pnl": -100.0},  # 25/10 em SP
    ]
    monkeypatch.setattr(bot, "TEST_LOG", _write(tmp_path, rows))
    n, pnl = bot.test_day_totals(Config(), at=datetime(2026, 10, 26, 18, tzinfo=timezone.utc))
    assert (n, pnl) == (3, 92.0 - 100.0 - 100.0)


def test_wants_test_otc_only_when_enabled_and_otc():
    b = bot.Bot.__new__(bot.Bot)
    b.cfg, b.enabled, b.busy = Config(bot_test_otc=True), True, False
    import asyncio
    b.queue = asyncio.Queue()
    otc = next(k for k, a in bot.ASSETS.items() if a.source == "otc")
    assert b.wants_test_otc({"asset": otc, "tf": 1})
    assert not b.wants_test_otc({"asset": "ETHUSD", "tf": 1})       # não-OTC nunca entra no teste
    b.cfg.bot_test_otc = False
    assert not b.wants_test_otc({"asset": otc, "tf": 1})
    b.cfg.bot_test_otc, b.enabled = True, False
    assert not b.wants_test_otc({"asset": otc, "tf": 1})
    b.enabled, b.busy = True, True
    assert not b.wants_test_otc({"asset": otc, "tf": 1})            # uma operação por vez


def test_report_scores_last_line_and_breakeven(tmp_path):
    from backend.otc.report import report

    rows = [
        {"id": "a", "at": "2026-10-26T15:00:00+00:00", "asset": "X_OTC", "quotex": "X (OTC)", "tf": 1, "setup": "mhi",
         "payout": 0.8, "stake": 100, "result": "pending"},
        {"id": "a", "at": "2026-10-26T15:00:00+00:00", "asset": "X_OTC", "quotex": "X (OTC)", "tf": 1, "setup": "mhi",
         "payout": 0.8, "stake": 100, "result": "win", "pnl": 80.0},
        {"id": "b", "at": "2026-10-26T15:02:00+00:00", "asset": "X_OTC", "quotex": "X (OTC)", "tf": 5, "setup": "mhi",
         "payout": 0.9, "stake": 100, "result": "loss", "pnl": -100.0},
    ]
    r = report(Config(), path=_write(tmp_path, rows))
    t = r["total"]
    assert (t["n"], t["wins"], t["losses"], t["pnl"]) == (2, 1, 1, -20.0)
    assert abs(t["breakeven"] - 1 / 1.85) < 1e-9 and t["proven"] is False
    assert [x["id"] for x in r["trades"]] == ["b", "a"]               # mais recente primeiro
    assert {g["key"] for g in r["by"]["tf"]} == {"M1", "M5"}
