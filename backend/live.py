"""Motor ao vivo: acompanha os candles, gera sinais e faz o forward test automático.

Forward test: todo sinal (aprovado ou vetado) é resolvido pelo próprio feed quando
expira. Assim dá para ver se os aprovados acertam mais que os vetados, sem operar.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import notify
from .agents import breaker, confluence, orchestrator, prior, risk, specialists
from .agents.spotter import run_all
from .backtest.binary_bt import simulate
from .indicators import bollinger, ema
from .lab.evolve import load_evolved, signals_for
from .assets import ASSETS, Asset, register_otc
from .config import DATA_DIR, Config
from .data import fetch_forming, fetch_recent, fx_feed, otc_feed
from .journal.models import Journal
from .stats import breakeven, wilson

log = logging.getLogger(__name__)
SIGNALS_LOG = DATA_DIR / "signals.jsonl"
OTC_STALE_S = 120  # par OTC sem tick novo há mais que isso = não está sendo gravado

Broadcast = Callable[[dict], Awaitable[None]]


def available_assets() -> dict[str, str | None]:
    """Ativo → motivo de indisponibilidade (None = disponível)."""
    register_otc()  # par OTC aberto agora no Chrome do robô aparece sem reiniciar o painel
    out: dict[str, str | None] = {}
    try:
        fx_feed.provider()
        fx_ok = None
    except RuntimeError as e:
        fx_ok = str(e)
    for k, a in ASSETS.items():
        if a.source == "otc":
            age = otc_feed.last_tick_age(a.symbol)
            out[k] = (None if age is not None and age < OTC_STALE_S else
                      f"não está sendo gravado — abra {a.quotex} no Chrome do robô com o gravador ligado")
        else:
            out[k] = None if a.source == "crypto" else fx_ok
    return out


class LiveEngine:
    def __init__(self, cfg: Config, journal: Journal, broadcast: Broadcast):
        self.cfg = cfg
        self.journal = journal
        self.broadcast = broadcast
        self.frames: dict[str, pd.DataFrame] = {}
        self.signals: deque[dict] = deque(maxlen=300)
        self.activity: deque[dict] = deque(maxlen=300)
        self.pending: list[dict] = []
        self.resolved: list[dict] = self._load_resolved()
        self.priors = prior.load()
        self.prices: dict[str, float] = {}
        self.forming: dict[str, pd.DataFrame] = {}          # "ATIVO|tf" → candle em formação (1 linha)
        self.presignals: dict[str, dict] = {}               # "ATIVO|tf" → último pré-sinal
        self.pre_stats = {"announced": 0, "confirmed": 0, "canceled": 0, "surprise": 0}
        self.models: dict[str, dict | None] = {}
        self._pre_eval_at: dict[str, float] = {}
        self.breakers = breaker.load()
        self._evolved: dict[str, list[dict]] = {}
        self._task: asyncio.Task | None = None

    # ---------- disjuntor ----------
    def suspended(self) -> dict[str, dict]:
        return self.breakers.get("suspended", {})

    async def _update_breakers(self) -> None:
        found = breaker.evaluate(self.resolved, self.breakers.get("resets", {}), self.cfg.z)
        before = set(self.suspended())
        new = {**self.suspended(), **found}   # suspensão é "grudenta": só sai com reativação manual
        self.breakers["suspended"] = new
        breaker.save(self.breakers)
        for k in set(new) - before:
            await self._log("closer", f"⛔ DISJUNTOR {k}: {new[k]['reason']}", k.split("|")[0], level="error")
            await self.broadcast({"type": "state"})

    async def reset_breaker(self, k: str) -> None:
        self.breakers.setdefault("resets", {})[k] = breaker.now_iso()
        self.breakers.get("suspended", {}).pop(k, None)
        breaker.save(self.breakers)
        await self._log("closer", f"disjuntor {k} reativado manualmente — volta a contar do zero", k.split("|")[0])

    def reload(self) -> None:
        """Recarrega priors e modelos (depois de rodar train/priors na linha de comando)."""
        self.priors = prior.load()
        self.models.clear()
        self._evolved.clear()

    def evolved(self, key: str, tf: int) -> list[dict]:
        """Estratégias promovidas pelo Laboratório Evolutivo para este ativo/timeframe."""
        k = f"{key}|{tf}"
        if k not in self._evolved:
            self._evolved[k] = load_evolved(key, tf)
        return self._evolved[k]

    def model(self, key: str, tf: int) -> dict | None:
        k = f"{key}|{tf}"
        if k not in self.models:
            try:
                self.models[k] = confluence.load(key, tf)
            except Exception:  # noqa: BLE001 — modelo corrompido não derruba o motor
                log.exception("falha ao carregar modelo %s", k)
                self.models[k] = None
        return self.models[k]

    def live_frame(self, key: str, tf: int) -> pd.DataFrame | None:
        """Candles fechados + o candle em formação (se houver)."""
        df = self.frame(key, tf)
        f = self.forming.get(f"{key}|{tf}")
        if df is None:
            return None
        if f is not None and f.index[-1] > df.index[-1]:
            return pd.concat([df, f])
        return df

    def seconds_left(self, tf: int, now: pd.Timestamp | None = None) -> float:
        now = now or pd.Timestamp.now(tz="UTC")
        step = tf * 60
        return step - (now.timestamp() % step)

    # ---------- helpers ----------
    def _load_resolved(self) -> list[dict]:
        if not SIGNALS_LOG.exists():
            return []
        return [json.loads(line) for line in SIGNALS_LOG.read_text(encoding="utf-8").splitlines() if line]

    def payout(self, asset: str, tf: int | None = None) -> float:
        """Payout informado na Quotex para o ativo e timeframe.
        Aceita o formato antigo {ativo: 0.85} e o novo {ativo: {"1": 0.85, "5": 0.82}}."""
        p = self.journal.get_setting("payouts", {}).get(asset)
        if isinstance(p, dict):
            v = p.get(str(tf)) if tf is not None else None
            if v is None and p:
                v = next(iter(p.values()))
        else:
            v = p
        return float(v if v is not None else self.cfg.payout)

    def payouts(self, asset: str) -> dict[str, float]:
        return {str(tf): self.payout(asset, tf) for tf in self.cfg.timeframes}

    def risk_state(self) -> risk.RiskState:
        start = float(self.journal.get_setting("bankroll_start", 100.0))
        return risk.state_from_journal(self.journal.all(), start, self.cfg)

    def active_assets(self) -> list[Asset]:
        avail = available_assets()
        chosen = self.journal.get_setting("watch", [k for k, v in avail.items() if v is None])
        return [ASSETS[k] for k in chosen if k in ASSETS and avail.get(k) is None]

    async def _log(self, agent: str, text: str, asset: str | None = None, level: str = "info",
                   tf: int | None = None) -> None:
        ev = {"at": datetime.now(timezone.utc).isoformat(), "agent": agent, "asset": asset, "tf": tf,
              "text": text, "level": level}
        self.activity.appendleft(ev)
        await self.broadcast({"type": "log", "data": ev})

    # ---------- ciclo ----------
    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        await self._log("system", "motor ao vivo iniciado")
        while True:
            try:
                for asset in self.active_assets():
                    await self._tick(asset)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 — o motor não pode morrer por erro de rede
                log.exception("erro no ciclo")
                await self._log("system", f"erro: {e}", level="error")
            await asyncio.sleep(self.cfg.poll_seconds)

    def frame(self, key: str, tf: int) -> pd.DataFrame | None:
        return self.frames.get(f"{key}|{tf}")

    async def _tick(self, asset: Asset) -> None:
        now = pd.Timestamp.now(tz="UTC")
        for tf in self.cfg.timeframes:
            if asset.source in ("crypto", "otc") or fx_feed.provider() == "oanda":
                f = await asyncio.to_thread(fetch_forming, asset, tf)
                if f is not None:
                    self.forming[f"{asset.key}|{tf}"] = f
                    if tf == min(self.cfg.timeframes):
                        self.prices[asset.key] = float(f["close"].iloc[-1])
                        await self.broadcast({"type": "price", "data": {
                            "asset": asset.key, "price": self.prices[asset.key], "at": now.isoformat()}})
            await self._tick_tf(asset, tf, now)
            await self._presignal(asset, tf, now)

    async def _presignal(self, asset: Asset, tf: int, now: pd.Timestamp) -> None:
        """Nos segundos finais do candle, avalia o candle em formação e avisa o que PODE sair."""
        key = f"{asset.key}|{tf}"
        left = self.seconds_left(tf, now)
        window = 25 if tf == 1 else 60
        if left > window or left < 1:
            return
        loop = asyncio.get_running_loop().time()
        if loop - self._pre_eval_at.get(key, 0) < 4:
            return
        self._pre_eval_at[key] = loop
        df = self.live_frame(asset.key, tf)
        if df is None or key not in self.forming:
            return
        sigs = await asyncio.to_thread(orchestrator.evaluate, df, asset, self.priors, self.risk_state(),
                                       self.cfg, tf, self.payout(asset.key, tf), self.model(asset.key, tf), True,
                                       self.suspended(), self.evolved(asset.key, tf))
        candle_ts = df.index[-1].isoformat()
        prev = self.presignals.get(key)
        data = {"asset": asset.key, "quotex": asset.quotex, "tf": tf, "candle_ts": candle_ts,
                "closes_at": (df.index[-1] + pd.Timedelta(minutes=tf)).isoformat(),
                "seconds_left": round(left), "signals": [s.to_dict() for s in sigs]}
        if prev is None or prev["candle_ts"] != candle_ts:
            data["seen"] = {}
        else:
            data["seen"] = prev.get("seen", {})
        for s in sigs:
            k = f"{s.setup}|{s.direction}"
            if k not in data["seen"]:
                data["seen"][k] = s.approved
                if s.approved:
                    await self._log("pre", f"M{tf} possível {s.direction} ({s.setup}) — fecha em {round(left)} s, "
                                    "prepare a ordem", asset.key, tf=tf)
        self.presignals[key] = data
        await self.broadcast({"type": "presignal", "data": data})

    async def _settle_presignal(self, key: str, tf: int, candle_ts: str, official: list) -> None:
        """No fechamento: confirma ou cancela o que foi anunciado no pré-sinal."""
        pre = self.presignals.pop(f"{key}|{tf}", None)
        if not pre or pre["candle_ts"] != candle_ts:
            pre = {"seen": {}}
        announced = {k for k, ok in pre["seen"].items() if ok}
        final = {f"{s.setup}|{s.direction}" for s in official if s.approved}
        result = []
        for k in announced:
            st = "confirmed" if k in final else "canceled"
            self.pre_stats[st] += 1
            result.append({"key": k, "status": st})
        for k in final - announced:
            self.pre_stats["surprise"] += 1
            result.append({"key": k, "status": "surprise"})
        self.pre_stats["announced"] += len(announced)
        if result:
            await self.broadcast({"type": "presignal_result", "data": {"asset": key, "tf": tf,
                                                                       "candle_ts": candle_ts, "items": result}})
            for r in result:
                txt = {"confirmed": "✔ pré-sinal CONFIRMADO", "canceled": "✘ pré-sinal CANCELADO — não entre",
                       "surprise": "sinal sem pré-aviso"}[r["status"]]
                await self._log("pre", f"M{tf} {r['key'].replace('|', ' ')}: {txt}", key, tf=tf)

    async def _tick_tf(self, asset: Asset, tf: int, now: pd.Timestamp) -> None:
        df = self.frame(asset.key, tf)
        # o próximo candle fecha em último início + 2·tf; espera 2 s para a fonte consolidar
        if df is not None and now < df.index[-1] + pd.Timedelta(minutes=2 * tf, seconds=2):
            return

        new = await asyncio.to_thread(fetch_recent, asset, self.cfg.live_window, tf)
        if new.empty or (df is not None and new.index[-1] <= df.index[-1]):
            return
        self.frames[f"{asset.key}|{tf}"] = new
        last = new.iloc[-1]
        await self.broadcast({"type": "candle", "data": {"asset": asset.key, "tf": tf, "ts": new.index[-1].isoformat(),
                                                         **{k: float(last[k]) for k in ("open", "high", "low", "close")}}})
        if df is None:
            await self._log("system", f"M{tf}: {len(new)} candles carregados", asset.key, tf=tf)
            return  # primeira carga: só avalia a partir do próximo candle fechado

        await self._resolve(asset.key, tf, new)
        sigs = await asyncio.to_thread(orchestrator.evaluate, new, asset, self.priors, self.risk_state(),
                                       self.cfg, tf, self.payout(asset.key, tf), self.model(asset.key, tf), False,
                                       self.suspended(), self.evolved(asset.key, tf))
        await self._settle_presignal(asset.key, tf, new.index[-1].isoformat(), sigs)
        for s in sigs:
            d = s.to_dict()
            self.signals.appendleft(d)
            self.pending.append(d)
            await self.broadcast({"type": "signal", "data": d})
            for agent, text in d["agents"].items():
                await self._log(agent, text, asset.key, tf=tf)
            if s.approved:
                await notify.telegram(f"✅ M{tf} {s.quotex} {s.direction} {s.expiry}min · R$ {s.stake:.2f} · "
                                      f"entrar até {pd.Timestamp(s.valid_until).tz_convert(self.cfg.timezone):%H:%M:%S}")

    async def _resolve(self, key: str, tf: int, df: pd.DataFrame) -> None:
        """Resolve sinais vencidos com os candles do feed (mesmo timeframe do sinal)."""
        still = []
        for s in self.pending:
            if s["asset"] != key or s["tf"] != tf:
                still.append(s)
                continue
            entry_ts = pd.Timestamp(s["entry_at"])
            exit_ts = entry_ts + pd.Timedelta(minutes=s["expiry"] - tf)
            if exit_ts not in df.index:
                if df.index[-1] < exit_ts:
                    still.append(s)
                continue
            if entry_ts not in df.index:
                continue
            entry, exit_ = float(df.at[entry_ts, "open"]), float(df.at[exit_ts, "close"])
            move = (exit_ - entry) * (1 if s["direction"] == "CALL" else -1)
            outcome = "win" if move > 0 else "loss" if move < 0 else "push"
            rec = {k: s[k] for k in ("id", "asset", "tf", "setup", "direction", "expiry", "approved",
                                     "p_hat", "lo", "breakeven", "payout", "candle_ts")}
            rec.update(entry=entry, exit=exit_, outcome=outcome, edge_ok=s.get("edge_ok", s["approved"]))
            self.resolved.append(rec)
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with SIGNALS_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            await self.broadcast({"type": "resolved", "data": rec})
            if rec["edge_ok"]:
                await self._update_breakers()
            tag = "✔" if outcome == "win" else "✘" if outcome == "loss" else "="
            await self._log("forward", f"{tag} M{tf} {s['setup']} {s['direction']} {s['expiry']}min "
                            f"({'aprovado' if s['approved'] else 'vetado'}) → {outcome}", key, tf=tf)
        self.pending = still

    # ---------- estatística do forward test ----------
    def forward_stats(self) -> dict:
        def block(rows: list[dict]) -> dict:
            dec = [r for r in rows if r["outcome"] != "push"]
            w = sum(r["outcome"] == "win" for r in dec)
            n = len(dec)
            lo, hi = wilson(w, n, self.cfg.z)
            be = breakeven(sum(r["payout"] for r in rows) / len(rows)) if rows else breakeven(self.cfg.payout)
            return {"n": n, "wins": w, "winrate": w / n if n else None, "lo": lo, "hi": hi, "breakeven": be}

        by_setup: dict[str, list] = {}
        for r in self.resolved:
            by_setup.setdefault(f"M{r.get('tf', 1)} {r['setup']}", []).append(r)
        return {"approved": block([r for r in self.resolved if r["approved"]]),
                "vetoed": block([r for r in self.resolved if not r["approved"]]),
                "by_setup": {k: block(v) for k, v in by_setup.items()},
                "presignals": self.pre_stats}

    # ---------- feed (Binance/OANDA) × Quotex ----------
    def feed_vs_quotex(self, trades: list[dict]) -> dict:
        """Para operações feitas a partir de um sinal: o resultado no feed bate com o da Quotex?
        E quanto o preço da Quotex difere do feed na entrada e na expiração?"""
        by_id = {r["id"]: r for r in self.resolved}
        n = agree = 0
        entry_bps, exit_bps = [], []
        mismatches = []
        for t in trades:
            rec = by_id.get(t.get("signal_id") or "")
            if not rec:
                continue
            if t["result"] in ("win", "loss", "push"):
                n += 1
                if t["result"] == rec["outcome"]:
                    agree += 1
                else:
                    mismatches.append({"id": t["id"], "quotex": t["result"], "feed": rec["outcome"]})
            if t.get("quotex_price") and rec.get("entry"):
                entry_bps.append(abs(t["quotex_price"] - rec["entry"]) / rec["entry"] * 1e4)
            if t.get("quotex_exit") and rec.get("exit"):
                exit_bps.append(abs(t["quotex_exit"] - rec["exit"]) / rec["exit"] * 1e4)

        def med(x):
            return float(np.median(x)) if x else None

        return {"n": n, "agree": agree, "rate": agree / n if n else None,
                "entry_bps": med(entry_bps), "exit_bps": med(exit_bps),
                "n_prices": len(entry_bps) + len(exit_bps), "mismatches": mismatches[-10:]}

    # ---------- catalogação e mapa ----------
    def catalog(self, key: str, tf: int, last_n: int = 30) -> dict:
        """Catalogação: como cada estratégia se saiu nos candles em memória (informativo)."""
        df = self.frame(key, tf)
        if df is None or len(df) < 150:
            return {"window": None, "rows": []}
        expiry = self.cfg.expiries_for(tf)[0]
        ec = self.cfg.expiry_candles(expiry, tf)
        signals = run_all(df)
        m = self.model(key, tf)
        if m is not None:
            X, _ = specialists.analyze(df)
            p = m["model"].predict_proba(X.reindex(columns=m["columns"]))[:, 1]
            signals[confluence.NAME] = confluence._signal(p, m["thr"])
        for name, sg in signals_for(df, self.evolved(key, tf)).items():
            signals[name] = sg
        be = breakeven(self.payout(key, tf))
        rows = []
        for setup in signals.columns:
            tr = simulate(df, signals[setup], ec, tf=tf)
            dec = tr[tr["result"] != 0].tail(last_n)
            w, n = int((dec["result"] == 1).sum()), len(dec)
            rows.append({"setup": setup, "n": n, "wins": w, "winrate": w / n if n else None,
                         "breakeven": be, "last": [int(x) for x in dec["result"].tail(10)]})
        rows.sort(key=lambda r: (r["winrate"] or 0), reverse=True)
        return {"window": [df.index[0].isoformat(), df.index[-1].isoformat()], "expiry": expiry, "rows": rows}

    def market_map(self, key: str, tf: int, n: int = 300) -> dict:
        df = self.live_frame(key, tf)
        if df is None or len(df) < 120:
            return {"zones": [], "snapshot": None, "p_model": None, "overlays": {}}
        snap = specialists.snapshot(df)
        m = self.model(key, tf)
        p_model = None
        if m is not None:
            p_model, _ = confluence.predict_last(df, m)
        c = df["close"]
        lower, mid, upper = bollinger(c)
        tail = df.index[-n:]

        def line(s: pd.Series) -> list[dict]:
            s = s.reindex(tail).dropna()
            return [{"time": int(t.timestamp()), "value": float(v)} for t, v in s.items()]

        return {
            "zones": specialists.zones(df.iloc[:-1] if key + f"|{tf}" in self.forming else df),
            "snapshot": snap, "p_model": p_model,
            "model": {"trained_at": m["trained_at"], "thr": m["thr"]} if m else None,
            "forming": key + f"|{tf}" in self.forming,
            "overlays": {"ema9": line(ema(c, 9)), "ema21": line(ema(c, 21)), "ema50": line(ema(c, 50)),
                         "bb_upper": line(upper), "bb_mid": line(mid), "bb_lower": line(lower)},
        }
