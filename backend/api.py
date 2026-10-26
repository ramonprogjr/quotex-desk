"""API do painel.  Rodar:  .venv/Scripts/uvicorn backend.api:app --port 8000"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agents import edge, risk
from .agents.spotter import run_all
from .assets import ASSETS
from .config import DATA_DIR, ROOT, load_config
from .lab.evolve import load_report
from .journal.models import Journal
from .live import LiveEngine, available_assets

logging.basicConfig(level=logging.INFO)
cfg = load_config()
journal = Journal()
clients: set[WebSocket] = set()


async def broadcast(msg: dict) -> None:
    dead = []
    for ws in list(clients):
        try:
            await ws.send_json(msg)
        except Exception:  # noqa: BLE001
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


engine = LiveEngine(cfg, journal, broadcast)


@asynccontextmanager
async def lifespan(_: FastAPI):
    engine.start()
    yield
    await engine.stop()


app = FastAPI(title="Quotex Desk", lifespan=lifespan)


# ---------------- estado ----------------
def _approved_cells() -> list[dict]:
    out = []
    for c in engine.priors.get("cells", {}).values():
        v = edge.judge(c, engine.payout(c["asset"], c.get("tf")), cfg)
        if v.approved:
            out.append({**c, **v.to_dict()})
    return out


@app.get("/api/state")
def state() -> dict:
    st = engine.risk_state()
    probe = risk.check(st, max(cfg.min_stake, st.last_stake or 0), cfg)
    avail = available_assets()  # antes do laço: pode incluir pares OTC novos em ASSETS
    return {
        "risk_block": None if probe.allowed else "; ".join(probe.reasons),
        "config": cfg.to_dict(),
        "assets": {k: {"quotex": a.quotex, "source": a.source, "unavailable": avail[k],
                       "payout": engine.payout(k), "payouts": engine.payouts(k), "price": engine.prices.get(k)}
                   for k, a in ASSETS.items()},
        "watch": [a.key for a in engine.active_assets()],
        "bankroll_start": journal.get_setting("bankroll_start", 100.0),
        "risk": st.to_dict(),
        "approved_cells": _approved_cells(),
        "priors_meta": engine.priors.get("meta", {}),
        "payouts_updated_at": journal.get_setting("payouts_updated_at"),
        "breakers": engine.suspended(),
    }


class SettingsIn(BaseModel):
    bankroll_start: float | None = Field(None, gt=0)
    # {"ETHUSD": {"1": 0.85, "5": 0.82}} (novo) ou {"ETHUSD": 0.85} (vale para todos os timeframes)
    payouts: dict[str, float | dict[str, float]] | None = None
    watch: list[str] | None = None


@app.put("/api/settings")
async def put_settings(s: SettingsIn) -> dict:
    if s.bankroll_start is not None:
        journal.set_setting("bankroll_start", s.bankroll_start)
    if s.payouts is not None:
        merged = dict(journal.get_setting("payouts", {}))
        for k, v in s.payouts.items():
            per_tf = v if isinstance(v, dict) else {str(tf): v for tf in cfg.timeframes}
            if k not in ASSETS or not all(0 < x <= 1.5 for x in per_tf.values()):
                raise HTTPException(400, f"payout inválido: {k}")
            old = merged.get(k)
            base = old if isinstance(old, dict) else ({str(tf): old for tf in cfg.timeframes} if old else {})
            merged[k] = {**base, **{str(t): float(x) for t, x in per_tf.items()}}
        journal.set_setting("payouts", merged)
        journal.set_setting("payouts_updated_at", datetime.now(timezone.utc).isoformat())
    if s.watch is not None:
        journal.set_setting("watch", [k for k in s.watch if k in ASSETS])
    await broadcast({"type": "state"})
    return state()


# ---------------- mercado ----------------
@app.get("/api/candles/{asset}")
def candles(asset: str, tf: int = 1, n: int = 300) -> list[dict]:
    df = engine.frame(asset.upper(), tf)
    if df is None:
        return []
    df = df.tail(min(n, 1000))
    return [{"ts": ts.isoformat(), **{k: float(r[k]) for k in ("open", "high", "low", "close")}}
            for ts, r in df.iterrows()]


@app.get("/api/spectrum/{asset}")
def spectrum(asset: str, tf: int = 1, n: int = 90) -> dict:
    df = engine.frame(asset.upper(), tf)
    if df is None:
        return {"ts": [], "setups": {}}
    m = run_all(df).tail(n)
    return {"ts": [t.isoformat() for t in m.index], "setups": {c: m[c].astype(int).tolist() for c in m.columns}}


@app.get("/api/map/{asset}")
def market_map(asset: str, tf: int = 1) -> dict:
    """Mapa do gráfico: zonas S/R, votos dos especialistas, Confluência e overlays."""
    return engine.market_map(asset.upper(), tf)


@app.get("/api/catalog/{asset}")
def catalog(asset: str, tf: int = 1) -> dict:
    return engine.catalog(asset.upper(), tf)


@app.get("/api/presignals")
def presignals() -> list[dict]:
    return list(engine.presignals.values())


@app.post("/api/reload")
async def reload_models() -> dict:
    engine.reload()
    await broadcast({"type": "state"})
    return {"ok": True, "cells": len(engine.priors.get("cells", {}))}


@app.get("/api/signals")
def signals(limit: int = 50) -> list[dict]:
    return list(engine.signals)[:limit]


@app.get("/api/activity")
def activity(limit: int = 100) -> list[dict]:
    return list(engine.activity)[:limit]


@app.get("/api/priors/{asset}")
def priors_for(asset: str) -> list[dict]:
    key = asset.upper()
    out = []
    for c in engine.priors.get("cells", {}).values():
        if c["asset"] == key:
            out.append({**c, **edge.judge(c, engine.payout(key, c.get("tf")), cfg).to_dict()})
    return sorted(out, key=lambda r: r["lo"], reverse=True)


# ---------------- diário ----------------
class TradeIn(BaseModel):
    asset: str
    direction: Literal["CALL", "PUT"]
    expiry: int = Field(gt=0, le=240)
    stake: float = Field(gt=0)
    payout: float | None = Field(None, gt=0, le=1.5)
    signal_id: str | None = None
    tf: int | None = None
    quotex_price: float | None = Field(None, gt=0)
    notes: str | None = None


class ResultIn(BaseModel):
    result: Literal["win", "loss", "push", "pending"]
    quotex_price: float | None = Field(None, gt=0)
    quotex_exit: float | None = Field(None, gt=0)


@app.get("/api/journal")
def journal_list() -> list[dict]:
    return list(reversed(journal.all()))


@app.post("/api/journal")
async def journal_add(t: TradeIn) -> dict:
    key = t.asset.upper()
    if key not in ASSETS:
        raise HTTPException(400, "ativo desconhecido")
    sig = next((s for s in engine.signals if s["id"] == t.signal_id), None) if t.signal_id else None
    row = journal.add(asset=key, direction=t.direction, expiry=t.expiry, stake=t.stake,
                      payout=t.payout or engine.payout(key, sig["tf"] if sig else (t.tf or t.expiry)),
                      setup=f"M{sig['tf']} {sig['setup']}" if sig else None,
                      signal_id=t.signal_id, p_pred=sig["p_hat"] if sig else None,
                      feed_price=engine.prices.get(key) or (sig["price"] if sig else None),
                      quotex_price=t.quotex_price, notes=t.notes)
    await broadcast({"type": "journal"})
    return row


@app.patch("/api/journal/{trade_id}")
async def journal_result(trade_id: int, r: ResultIn) -> dict:
    try:
        row = journal.set_result(trade_id, r.result, r.quotex_price, r.quotex_exit)
    except KeyError:
        raise HTTPException(404, "operação não encontrada") from None
    await broadcast({"type": "journal"})
    return row


@app.delete("/api/journal/{trade_id}")
async def journal_delete(trade_id: int) -> dict:
    journal.delete(trade_id)
    await broadcast({"type": "journal"})
    return {"ok": True}


@app.get("/api/stats")
def stats() -> dict:
    return {"journal": journal.stats(cfg.z), "forward": engine.forward_stats(),
            "feed_vs_quotex": engine.feed_vs_quotex(journal.all())}


# ---------------- laboratório evolutivo ----------------
_lab_runs: dict[str, subprocess.Popen] = {}


@app.get("/api/lab/{asset}")
def lab(asset: str, tf: int = 5) -> dict:
    key = f"{asset.upper()}|{tf}"
    proc = _lab_runs.get(key)
    running = proc is not None and proc.poll() is None
    if proc is not None and not running:   # terminou: carrega as promovidas no motor
        _lab_runs.pop(key, None)
        engine.reload()
    rep = load_report(asset, tf) or {}
    return {"running": running, "report": rep or None,
            "live": [e["id"] for e in engine.evolved(asset.upper(), tf)]}


@app.post("/api/lab/run")
async def lab_run(asset: str, tf: int = 5, gens: int = 40) -> dict:
    key = asset.upper()
    if key not in ASSETS or tf not in cfg.timeframes or not 1 <= gens <= 300:
        raise HTTPException(400, "ativo, timeframe ou número de gerações inválido")
    k = f"{key}|{tf}"
    if k in _lab_runs and _lab_runs[k].poll() is None:
        return {"ok": True, "already": True}
    log = open(DATA_DIR / f"lab_{key}_M{tf}.log", "w", encoding="utf-8")  # noqa: SIM115 — fica aberto com o processo
    _lab_runs[k] = subprocess.Popen(
        [sys.executable, "-m", "backend.cli", "evolve", key, "--tf", str(tf), "--gens", str(int(gens))],
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    await engine._log("lab", f"evolução iniciada: {key} M{tf}, {gens} gerações", key, tf=tf)
    return {"ok": True}


# ---------------- robô (somente DEMO) ----------------
_bot_proc: subprocess.Popen | None = None
_bot = {"enabled": False, "status": "desligado", "account": None, "balance": None, "seen": None,
        "checks": None, "test_order": None, "diagnose": False}


def _bot_running() -> bool:
    return _bot_proc is not None and _bot_proc.poll() is None


@app.get("/api/bot")
def bot_state() -> dict:
    stale = not _bot["seen"] or (datetime.now(timezone.utc) - datetime.fromisoformat(_bot["seen"])).total_seconds() > 15
    # robô aberto por uma instância anterior do painel não é filho deste processo: vale o heartbeat
    alive = _bot_running() or not stale
    return {**{k: v for k, v in _bot.items() if k not in ("test_order", "diagnose")},
            "process": alive, "status": _bot["status"] if alive and not stale else "desligado",
            "timeframes": cfg.bot_timeframes}


@app.post("/api/bot/launch")
async def bot_launch() -> dict:
    global _bot_proc
    if _bot_running():
        return {"ok": True, "already": True}
    log = open(DATA_DIR / "bot.log", "w", encoding="utf-8")  # noqa: SIM115
    _bot_proc = subprocess.Popen([sys.executable, "-m", "backend.bot"], cwd=ROOT, stdout=log,
                                 stderr=subprocess.STDOUT, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    _bot["enabled"] = False  # sempre começa desligado
    return {"ok": True}


@app.post("/api/bot/stop")
async def bot_stop() -> dict:
    _bot["enabled"] = False
    if _bot_running():
        _bot_proc.terminate()
    _bot["status"] = "desligado"
    await broadcast({"type": "bot", "data": bot_state()})
    return {"ok": True}


@app.post("/api/bot/enable")
async def bot_enable(on: bool) -> dict:
    if on and _bot.get("account") != "DEMO":
        raise HTTPException(400, "o robô só liga com a CONTA DEMO aberta no navegador dele")
    _bot["enabled"] = on
    await engine._log("robô", "LIGADO — vai operar os sinais aprovados na DEMO" if on else "pausado", None)
    await broadcast({"type": "bot", "data": bot_state()})
    return bot_state()


@app.post("/api/bot/test")
async def bot_test(asset: str, direction: Literal["CALL", "PUT"], tf: int = 5) -> dict:
    if _bot.get("account") != "DEMO":
        raise HTTPException(400, "abra a CONTA DEMO no navegador do robô antes do teste")
    _bot["test_order"] = {"asset": asset.upper(), "direction": direction, "tf": tf}
    return {"ok": True}


@app.post("/api/bot/diagnose")
async def bot_diagnose() -> dict:
    _bot["diagnose"] = True
    return {"ok": True}


@app.get("/api/bot/control")
def bot_control() -> dict:
    """Lido pelo robô a cada poucos segundos. Pedidos de teste/diagnóstico são entregues uma vez."""
    out = {"enabled": _bot["enabled"], "test_order": _bot["test_order"], "diagnose": _bot["diagnose"]}
    _bot["test_order"], _bot["diagnose"] = None, False
    return out


class BotEvent(BaseModel):
    text: str | None = None
    level: str = "info"
    heartbeat: bool = False
    status: str | None = None
    account: str | None = None
    balance: float | None = None
    checks: dict | None = None


@app.post("/api/bot/event")
async def bot_event(e: BotEvent) -> dict:
    _bot["seen"] = datetime.now(timezone.utc).isoformat()
    if e.heartbeat:
        _bot.update(status=e.status, account=e.account, balance=e.balance)
        if e.account != "DEMO" and _bot["enabled"]:
            _bot["enabled"] = False  # trava: saiu da DEMO
            await engine._log("robô", "⛔ conta não é DEMO — robô desligado automaticamente", None, level="error")
    if e.checks is not None:
        _bot["checks"] = e.checks
    if e.text:
        await engine._log("robô", e.text, None, level=e.level)
    await broadcast({"type": "bot", "data": bot_state()})
    return {"ok": True}


# ---------------- disjuntor ----------------
@app.get("/api/breakers")
def breakers() -> dict:
    return engine.breakers


@app.post("/api/breakers/reset")
async def breakers_reset(key: str) -> dict:
    await engine.reset_breaker(key)
    await broadcast({"type": "state"})
    return engine.breakers


# ---------------- tempo real ----------------
@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    await websocket.accept()
    clients.add(websocket)
    try:
        while True:
            await asyncio.wait_for(websocket.receive_text(), timeout=3600)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        clients.discard(websocket)


# ---------------- frontend compilado ----------------
DIST = ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str) -> FileResponse:
        f = DIST / path
        return FileResponse(f if path and f.is_file() else DIST / "index.html")
