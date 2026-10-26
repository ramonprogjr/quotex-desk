"""Diário de operações (SQLite). Cada entrada feita manualmente na Quotex vira uma linha."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from ..config import DB_PATH
from ..stats import breakeven, wilson

RESULTS = {"pending", "win", "loss", "push"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    asset TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('CALL','PUT')),
    expiry INTEGER NOT NULL,
    stake REAL NOT NULL CHECK (stake > 0),
    payout REAL NOT NULL,
    result TEXT NOT NULL DEFAULT 'pending',
    pnl REAL,
    setup TEXT,
    signal_id TEXT,
    followed_signal INTEGER NOT NULL DEFAULT 0,
    p_pred REAL,
    feed_price REAL,
    quotex_price REAL,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Journal:
    def __init__(self, path: Path = DB_PATH):
        self.path = path
        with self._conn() as c:
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(trades)")}
            if "quotex_exit" not in cols:  # migração: preço de expiração visto na Quotex
                c.execute("ALTER TABLE trades ADD COLUMN quotex_exit REAL")

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------- settings ----------
    def get_setting(self, key: str, default=None):
        with self._conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                      "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    # ---------- trades ----------
    def add(self, *, asset: str, direction: str, expiry: int, stake: float, payout: float,
            setup: str | None = None, signal_id: str | None = None, p_pred: float | None = None,
            feed_price: float | None = None, quotex_price: float | None = None,
            notes: str | None = None) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO trades(created_at,asset,direction,expiry,stake,payout,setup,signal_id,"
                "followed_signal,p_pred,feed_price,quotex_price,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now, asset.upper(), direction.upper(), int(expiry), float(stake), float(payout), setup,
                 signal_id, int(bool(signal_id)), p_pred, feed_price, quotex_price, notes))
            return self.get(cur.lastrowid, c)

    def get(self, trade_id: int, conn=None) -> dict | None:
        if conn is None:
            with self._conn() as c:
                return self.get(trade_id, c)
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        return dict(row) if row else None

    def set_result(self, trade_id: int, result: str, quotex_price: float | None = None,
                   quotex_exit: float | None = None) -> dict:
        if result not in RESULTS:
            raise ValueError(f"resultado inválido: {result}")
        with self._conn() as c:
            t = self.get(trade_id, c)
            if not t:
                raise KeyError(trade_id)
            pnl = {"win": t["stake"] * t["payout"], "loss": -t["stake"], "push": 0.0, "pending": None}[result]
            c.execute("UPDATE trades SET result=?, pnl=?, quotex_price=COALESCE(?, quotex_price), "
                      "quotex_exit=COALESCE(?, quotex_exit) WHERE id=?",
                      (result, pnl, quotex_price, quotex_exit, trade_id))
            return self.get(trade_id, c)

    def delete(self, trade_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM trades WHERE id=?", (trade_id,))

    def all(self) -> list[dict]:
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM trades ORDER BY created_at")]

    # ---------- calibração ----------
    def stats(self, z: float = 1.645) -> dict:
        trades = [t for t in self.all() if t["result"] in ("win", "loss")]

        def block(rows: list[dict]) -> dict:
            w = sum(r["result"] == "win" for r in rows)
            n = len(rows)
            lo, hi = wilson(w, n, z)
            avg_payout = sum(r["payout"] for r in rows) / n if n else 0.85
            preds = [r["p_pred"] for r in rows if r["p_pred"] is not None]
            return {"n": n, "wins": w, "winrate": w / n if n else None, "lo": lo, "hi": hi,
                    "breakeven": breakeven(avg_payout), "pnl": round(sum(r["pnl"] or 0 for r in rows), 2),
                    "predicted": sum(preds) / len(preds) if preds else None,
                    "edge_proven": n > 0 and lo > breakeven(avg_payout)}

        groups: dict[str, dict[str, list]] = {"setup": {}, "asset": {}, "hour": {}, "followed": {}}
        for t in trades:
            groups["setup"].setdefault(t["setup"] or "manual", []).append(t)
            groups["asset"].setdefault(t["asset"], []).append(t)
            groups["hour"].setdefault(t["created_at"][11:13] + "h UTC", []).append(t)
            groups["followed"].setdefault("seguiu sinal" if t["followed_signal"] else "por conta própria", []).append(t)
        diffs = [abs(t["quotex_price"] - t["feed_price"]) / t["feed_price"]
                 for t in self.all() if t["quotex_price"] and t["feed_price"]]
        return {
            "overall": block(trades),
            **{g: {k: block(v) for k, v in d.items()} for g, d in groups.items()},
            "price_divergence_bps": (sorted(diffs)[len(diffs) // 2] * 1e4) if diffs else None,
            "validation": {"target_n": 100, "n": len(trades),
                           "passed": len(trades) >= 100 and block(trades)["edge_proven"]},
        }
