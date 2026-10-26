"""Relatório do modo teste OTC do robô (lido de data_cache/bot_test_otc.jsonl, fora do diário).

Cada entrada tem duas linhas no log (abertura e resultado); vale a última. O placar mostra o acerto
com o intervalo de Wilson contra o empate do payout médio: é o mesmo critério do resto do projeto,
e com poucas entradas o intervalo é largo de propósito.
"""
from __future__ import annotations

import json
from collections import defaultdict

from ..config import DATA_DIR, Config
from ..stats import breakeven, wilson
from . import BOT_TEST_LOG


def load(path=BOT_TEST_LOG) -> list[dict]:
    out: dict[str, dict] = {}
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            out[r["id"]] = r
    return sorted(out.values(), key=lambda r: r["at"], reverse=True)


def score(rows: list[dict], z: float) -> dict:
    wins = sum(r["result"] == "win" for r in rows)
    losses = sum(r["result"] == "loss" for r in rows)
    decided = wins + losses
    pays = [r["payout"] for r in rows if r.get("payout")]
    pay = sum(pays) / len(pays) if pays else None
    lo, hi = wilson(wins, decided, z) if decided else (None, None)
    be = breakeven(pay) if pay else None
    return {
        "n": len(rows), "wins": wins, "losses": losses,
        "pushes": sum(r["result"] == "push" for r in rows),
        "pending": sum(r["result"] == "pending" for r in rows),
        "unknown": sum(r["result"] == "unknown" for r in rows),
        "rate": wins / decided if decided else None, "lo": lo, "hi": hi,
        "payout": pay, "breakeven": be,
        # só há vantagem comprovada quando até o pior cenário do intervalo passa do empate
        "proven": bool(lo is not None and be is not None and lo > be),
        "pnl": round(sum(r.get("pnl") or 0.0 for r in rows), 2),
    }


def forward(cfg: Config, path=DATA_DIR / "signals.jsonl") -> dict:
    """Forward test de TODO sinal OTC (o painel resolve cada sinal quando vence, com ou sem entrada do
    robô): muito mais volume que as entradas do robô, sem atraso de execução."""
    rows = []
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                s = json.loads(ln)
            except ValueError:
                continue
            if s.get("asset", "").endswith("_OTC") and s.get("outcome") in ("win", "loss", "push"):
                rows.append({"result": s["outcome"], "payout": s.get("payout"), "setup": s.get("setup") or "?",
                             "asset": s["asset"], "tf": s.get("tf")})
    by: dict[str, dict[str, list]] = {"setup": defaultdict(list), "asset": defaultdict(list), "tf": defaultdict(list)}
    for r in rows:
        by["setup"][r["setup"]].append(r)
        by["asset"][r["asset"].removesuffix("_OTC")].append(r)
        by["tf"][f"M{r['tf']}"].append(r)
    return {"total": score(rows, cfg.z),
            "by": {g: sorted(({"key": k, **score(v, cfg.z)} for k, v in d.items()), key=lambda x: -x["n"])
                   for g, d in by.items()}}


def report(cfg: Config, path=BOT_TEST_LOG, last: int = 50) -> dict:
    rows = load(path)
    groups: dict[str, dict[str, list]] = {"setup": defaultdict(list), "asset": defaultdict(list), "tf": defaultdict(list)}
    for r in rows:
        groups["setup"][r.get("setup") or "?"].append(r)
        groups["asset"][r.get("quotex") or r["asset"]].append(r)
        groups["tf"][f"M{r['tf']}"].append(r)
    return {
        "total": score(rows, cfg.z),
        "by": {g: sorted(({"key": k, **score(v, cfg.z)} for k, v in d.items()), key=lambda x: -x["n"])
               for g, d in groups.items()},
        "trades": rows[:last],
        "forward": forward(cfg),
    }
