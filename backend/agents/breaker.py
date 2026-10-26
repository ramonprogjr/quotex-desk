"""Disjuntor: suspende uma estratégia aprovada quando ela piora AO VIVO.

O histórico aprova; o forward test (sinais resolvidos pelo feed) pode desaprovar.
Regra: nos últimos `window` sinais APROVADOS de (ativo, timeframe, setup), com pelo
menos `min_n` decididos, se até o limite SUPERIOR de Wilson ficar abaixo do empate,
a estratégia está comprovadamente pior que o empate → suspensa até reativação manual.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from ..config import DATA_DIR
from ..stats import breakeven, wilson

BREAKERS_PATH = DATA_DIR / "breakers.json"


def key(asset: str, tf: int, setup: str) -> str:
    return f"{asset}|M{tf}|{setup}"


def evaluate(resolved: list[dict], resets: dict[str, str], z: float = 1.645,
             window: int = 60, min_n: int = 40) -> dict[str, dict]:
    """Devolve {chave: info} das estratégias que devem ficar suspensas.
    `resets`: chave → ISO do momento da reativação (só conta sinais depois dela)."""
    groups: dict[str, list[dict]] = {}
    for r in resolved:
        # conta o que o Edge aprovou, mesmo se o risco bloqueou na hora (stop, pausa…)
        if not r.get("edge_ok", r.get("approved")) or r.get("outcome") == "push":
            continue
        k = key(r["asset"], r.get("tf", 1), r["setup"])
        if k in resets and r.get("candle_ts", "") <= resets[k]:
            continue
        groups.setdefault(k, []).append(r)
    out = {}
    for k, rows in groups.items():
        rows = rows[-window:]
        n = len(rows)
        if n < min_n:
            continue
        w = sum(r["outcome"] == "win" for r in rows)
        be = breakeven(sum(r["payout"] for r in rows) / n)
        _, hi = wilson(w, n, z)
        if hi < be:
            out[k] = {"n": n, "wins": w, "winrate": w / n, "hi": hi, "breakeven": be,
                      "reason": f"suspensa: ao vivo {w / n:.0%} em {n} sinais (máx. provável {hi:.0%} < empate {be:.0%})"}
    return out


def load() -> dict:
    if BREAKERS_PATH.exists():
        return json.loads(BREAKERS_PATH.read_text(encoding="utf-8"))
    return {"suspended": {}, "resets": {}}


def save(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BREAKERS_PATH.write_text(json.dumps(state, indent=1), encoding="utf-8")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
