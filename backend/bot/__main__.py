"""Robô de execução na Quotex — SOMENTE CONTA DEMO.

    .venv/Scripts/python -m backend.bot        (ou pelo botão "Abrir navegador do robô" no painel)

- Abre um Chrome próprio (perfil em data_cache/quotex_profile). VOCÊ faz o login nele.
- Recebe os sinais APROVADOS do painel (mesma esteira Edge → Kelly → Closer → disjuntor).
- Antes de cada ordem confere que a conta é DEMO; se aparecer conta REAL, trava.
- Registra cada ordem no diário e marca o resultado pela variação do saldo da Quotex.
- Liga/desliga e teste mecânico pelo painel (o robô começa DESLIGADO).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import websockets
from playwright.async_api import async_playwright

from ..assets import ASSETS, is_otc, register_otc
from ..config import DATA_DIR, load_config
from ..data import otc_feed
from ..otc import BOT_TEST_LOG, TICKS_DIR, latest_instruments
from .quotex import QuotexPage

API = "http://127.0.0.1:8000"
WS = "ws://127.0.0.1:8000/ws"
URL = "https://quotex.com/pt/trade"
PROFILE = DATA_DIR / "quotex_profile"
CDP_PORT = 9223      # só em 127.0.0.1; dá controle total deste perfil a quem estiver na máquina
CDP = f"http://127.0.0.1:{CDP_PORT}"
TEST_LOG = BOT_TEST_LOG
KEEPALIVE_STALE_S = 90  # par OTC gravado sem tick há mais que isso: o robô reabre no seletor
LOCK_PORT = 9224     # trava de instância única: dois robôs no mesmo Chrome mandariam ordens em dobro
GRACE_S = 3          # tolerância depois de valid_until
PAYOUT_TOL = 0.01    # payout real até 1 p.p. abaixo do configurado ainda vale


def now() -> datetime:
    return datetime.now(timezone.utc)


class Bot:
    def __init__(self, q: QuotexPage, http: httpx.AsyncClient):
        self.q, self.http = q, http
        self.cfg = load_config()
        self.queue: asyncio.Queue = asyncio.Queue()
        self.enabled = False
        self.busy = False
        self.account: dict = {}
        self.limit_logged = False

    async def event(self, text: str, level: str = "info", **fields) -> None:
        try:
            await self.http.post(f"{API}/api/bot/event", json={"text": text, "level": level, **fields})
        except httpx.HTTPError:
            print(f"[robô] {text}", file=sys.stderr)

    # ---------- laços ----------
    async def heartbeat(self) -> None:
        while True:
            try:
                self.account = await self.q.account()
                ctl = (await self.http.get(f"{API}/api/bot/control")).json()
                self.enabled = bool(ctl.get("enabled"))
                if ctl.get("diagnose"):
                    diag = await self.q.self_check()
                    await self.event("autodiagnóstico: " + ("tudo encontrado ✔" if diag["ok"] else "faltam elementos — veja a lista"),
                                     "info" if diag["ok"] else "error", checks=diag["checks"])
                if ctl.get("test_order") and not self.busy:
                    await self.queue.put({"test": True, **ctl["test_order"]})
                acc_t = self.account.get("type")
                status = ("aguardando login" if acc_t is None else "TRAVADO: conta REAL" if acc_t == "REAL"
                          else "operando" if self.busy else "ligado (DEMO)" if self.enabled else "pausado (DEMO)")
                await self.http.post(f"{API}/api/bot/event", json={
                    "heartbeat": True, "status": status, "account": acc_t, "balance": self.account.get("balance")})
            except Exception as e:  # noqa: BLE001 — o robô não pode morrer por uma leitura falha
                print(f"[robô] heartbeat: {e}", file=sys.stderr)
            await asyncio.sleep(4)

    async def listen(self) -> None:
        while True:
            try:
                async with websockets.connect(WS) as ws:
                    async for raw in ws:
                        m = json.loads(raw)
                        if m.get("type") != "signal":
                            continue
                        s = m["data"]
                        if s.get("approved") and self.enabled and s.get("tf") in self.cfg.bot_timeframes:
                            await self.queue.put(s)
                        elif self.wants_test_otc(s):
                            await self.queue.put({**s, "test_otc": True})
            except Exception as e:  # noqa: BLE001
                print(f"[robô] websocket: {e}; reconectando", file=sys.stderr)
                await asyncio.sleep(3)

    async def keep_otc_alive(self) -> None:
        """Reabre no seletor os pares OTC gravados que pararam de receber ticks.

        A Quotex só mantém o preço dos pares já abertos enquanto a conexão da página dura; depois de uma
        reconexão, só o par da tela continua. Abrir o par de novo (como você faz à mão) volta a gravação.
        Só age com o robô livre, e no máximo uma vez a cada 5 min por par.
        """
        tried: dict[str, float] = {}
        failed: dict[str, int] = {}  # mapeamento: falhas no seletor por par
        while True:
            await asyncio.sleep(30)
            try:
                if not self.cfg.bot_test_otc or self.busy or not self.queue.empty():
                    continue
                register_otc()
                t = asyncio.get_running_loop().time()
                for a in [a for a in ASSETS.values() if a.source == "otc"]:
                    age = otc_feed.last_tick_age(a.symbol)
                    # nunca gravado (None) ou parado há mais de 1 dia: não é um par que você abriu hoje
                    if (age is None or age < KEEPALIVE_STALE_S or age > 86400 or t - tried.get(a.key, -1e9) < 300
                            or failed.get(a.symbol, 0) >= 3):
                        continue
                    if self.busy or not self.queue.empty():
                        break
                    tried[a.key] = t
                    self.busy = True
                    try:
                        ok = await self.q.select_asset(a.quotex, otc=True)
                    finally:
                        self.busy = False
                    failed[a.symbol] = 0 if ok else failed.get(a.symbol, 0) + 1
                    gave_up = failed[a.symbol] >= 3
                    await self.event(f"gravação: {a.quotex} estava parado há {age / 60:.0f} min — "
                                     f"{'reaberto no seletor' if ok else 'não consegui reabrir'}"
                                     f"{' (desisti deste par)' if gave_up else ''}", "info" if ok else "error")
                    await asyncio.sleep(3)
                await self.map_new_pair(failed)
            except Exception as e:  # noqa: BLE001
                print(f"[robô] keepalive: {e}", file=sys.stderr)

    async def map_new_pair(self, failed: dict[str, int]) -> None:
        """Mapeamento automático: abre UM par OTC ainda não gravado (maior payout primeiro) por rodada,
        até `bot_otc_max_pairs` gravando. Um par que falha 2 vezes no seletor é deixado de lado."""
        if not self.cfg.bot_otc_autoadd or self.busy or not self.queue.empty():
            return
        recording = {d.name for d in TICKS_DIR.iterdir() if d.is_dir()
                     and (otc_feed.last_tick_age(d.name) or 1e9) < 300} if TICKS_DIR.exists() else set()
        if len(recording) >= self.cfg.bot_otc_max_pairs:
            return
        cands = [r for r in latest_instruments()
                 if r["symbol"].endswith("_otc") and r.get("open") and r["symbol"] not in recording
                 and (r.get("payout") or 0) >= self.cfg.bot_test_min_payout * 100 and failed.get(r["symbol"], 0) < 2]
        if not cands:
            return
        r = max(cands, key=lambda x: x["payout"])
        self.busy = True
        try:
            ok = await self.q.select_asset(r["name"], otc=True)
        finally:
            self.busy = False
        if not ok:
            failed[r["symbol"]] = failed.get(r["symbol"], 0) + 1
        await self.event(f"mapeando pares: {r['name']} ({r['payout']}%) — "
                         f"{'aberto, começou a gravar (sinais em ~2 h)' if ok else 'não consegui abrir no seletor'} · "
                         f"{len(recording) + ok}/{self.cfg.bot_otc_max_pairs} pares gravando", "info" if ok else "error")

    def wants_test_otc(self, s: dict) -> bool:
        """Modo teste OTC: sinal (mesmo vetado) de par OTC gravado, com o robô livre."""
        a = ASSETS.get(s.get("asset", ""))
        return bool(self.enabled and self.cfg.bot_test_otc and a and a.source == "otc" and not self.busy
                    and self.queue.empty() and s.get("tf") in self.cfg.bot_test_timeframes)

    async def worker(self) -> None:
        while True:
            s = await self.queue.get()
            self.busy = True
            try:
                if s.get("test"):
                    await self.execute(s["asset"], s["direction"], int(s["tf"]), int(s["tf"]),
                                       self.cfg.min_stake, None, test=True)
                else:
                    late = (now() - datetime.fromisoformat(s["valid_until"])).total_seconds()
                    if late > GRACE_S:
                        if not s.get("test_otc"):
                            await self.event(f"sinal {s['asset']} {s['direction']} expirou antes de executar ({late:.0f}s)", "error")
                    elif s.get("test_otc"):
                        await self.execute(s["asset"], s["direction"], s["tf"], s["expiry"], self.cfg.bot_test_stake,
                                           s["id"], test_otc=s, deadline=s["valid_until"], candle_ts=s.get("candle_ts"))
                    else:
                        await self.execute(s["asset"], s["direction"], s["tf"], s["expiry"], s["stake"], s["id"],
                                           payout_cfg=s.get("payout"), deadline=s["valid_until"],
                                           candle_ts=s.get("candle_ts"))
            except Exception as e:  # noqa: BLE001
                await self.event(f"erro executando ordem: {e}", "error")
            finally:
                self.busy = False

    # ---------- execução ----------
    async def execute(self, asset: str, direction: str, tf: int, expiry: int, stake: float,
                      signal_id: str | None, payout_cfg: float | None = None, test: bool = False,
                      test_otc: dict | None = None, deadline: str | None = None,
                      candle_ts: str | None = None) -> None:
        q = self.q
        a = ASSETS.get(asset)
        if not a or (is_otc(a.quotex) and not (test_otc and self.cfg.bot_test_otc)):
            return await self.event(f"{asset}: ativo não operável (OTC ou desconhecido)", "error")
        if test_otc:
            n, pnl = test_day_totals(self.cfg)
            if n >= self.cfg.bot_test_max_day or pnl <= -self.cfg.bot_test_day_stop:
                if not self.limit_logged:
                    self.limit_logged = True
                    await self.event(f"teste OTC: limite do dia atingido ({n} entradas, {pnl:+.2f}) — parado até amanhã", "error")
                return
            self.limit_logged = False

        acc = await q.account()
        if acc["type"] != "DEMO":
            return await self.event(f"⛔ conta {acc['type'] or 'não identificada'} — o robô só opera na DEMO. Ordem cancelada.", "error")

        if not await q.select_asset(a.quotex, otc=bool(test_otc)):
            return await self.event(f"{a.quotex}: não encontrei esse par no seletor da Quotex agora — pulei", "error")

        cur = await q.current_asset()
        if cur["payout"] is not None:
            await self.http.put(f"{API}/api/settings", json={"payouts": {asset: {str(tf): cur["payout"]}}})
            if test_otc and cur["payout"] < self.cfg.bot_test_min_payout:
                return await self.event(f"teste OTC: payout {cur['payout']:.0%} de {a.quotex} abaixo de "
                                        f"{self.cfg.bot_test_min_payout:.0%} — pulei", "info")
            if not test and not test_otc and payout_cfg and cur["payout"] < payout_cfg - PAYOUT_TOL:
                return await self.event(f"payout real {cur['payout']:.0%} < {payout_cfg:.0%} usado no sinal — pulei "
                                        "(o painel já foi atualizado)", "error")

        expiry_label, target = f"{expiry} min", None
        if self.cfg.bot_expiry_mode == "horario" and candle_ts:
            # vence no fechamento da vela que o backtest testa: a seguinte à do sinal (+ expiry - tf)
            target = datetime.fromisoformat(candle_ts) + timedelta(minutes=tf + expiry)
            hhmm = target.astimezone(timezone.utc).strftime("%H:%M")
            if not await q.set_expiry_at(hhmm):
                return await self.event(f"não consegui marcar o vencimento {hhmm} UTC (modo horário) — ordem cancelada", "error")
            expiry_label = f"vence {hhmm} UTC"
        elif not await q.set_duration(expiry):
            return await self.event("não consegui ajustar o campo Tempo — ordem cancelada", "error")
        if not await q.set_amount(stake):
            return await self.event("não consegui ajustar o campo Investimento — ordem cancelada", "error")

        # o backtest entra na abertura da vela seguinte: preparar a ordem leva segundos, então o prazo do
        # sinal (10 s no M1, 30 s no M5) é conferido de novo aqui, logo antes do clique
        if deadline:
            late = (now() - datetime.fromisoformat(deadline)).total_seconds()
            if late > GRACE_S:
                return await self.event(f"{a.quotex} {direction}: passou {late:.0f} s do prazo do sinal enquanto preparava "
                                        "a ordem — não entrei (a operação já não seria a testada)", "error")
        acc = await q.account()  # confere de novo, logo antes do clique
        if acc["type"] != "DEMO":
            return await self.event("⛔ conta mudou para REAL — ordem cancelada", "error")
        bal0 = acc["balance"]

        if not await q.click_direction(direction):
            return await self.event(f"não achei o botão {'Comprar' if direction == 'CALL' else 'Vender'} — ordem cancelada", "error")

        if test_otc:  # fora do diário: não mexe na banca, no risco nem na calibração
            rec = {"id": signal_id, "at": now().isoformat(), "asset": asset, "quotex": a.quotex, "tf": tf,
                   "expiry": expiry, "direction": direction, "stake": stake, "payout": cur["payout"],
                   "setup": test_otc.get("setup"), "approved": bool(test_otc.get("approved")),
                   "balance_before": bal0, "result": "pending"}
            append_test(rec)
            await self.event(f"✔ ordem DEMO (teste OTC, sem vantagem comprovada): {a.quotex} {direction} "
                             f"M{tf} {expiry_label} · ${stake:g} · {rec['setup']}", "info")
        else:
            r = await self.http.post(f"{API}/api/journal", json={
                "asset": asset, "direction": direction, "expiry": expiry, "stake": stake, "tf": tf,
                "payout": cur["payout"], "signal_id": signal_id,
                "notes": "robô DEMO · teste mecânico" if test else "robô DEMO"})
            trade = r.json()
            await self.event(f"✔ ordem DEMO: {a.quotex} {direction} {expiry} min · ${stake:g}"
                             f"{' (teste)' if test else ''}", "info")

        # espera o vencimento (+ alguns segundos para a Quotex creditar o saldo)
        ends = target if expiry_label.startswith("vence") else now() + timedelta(minutes=expiry)
        await asyncio.sleep(max(0.0, (ends - now()).total_seconds()) + 4)
        bal1 = (await q.account())["balance"]
        if bal0 is None or bal1 is None:
            if test_otc:
                append_test({**rec, "result": "unknown", "at_result": now().isoformat()})
            return await self.event("não consegui ler o saldo — marque o resultado no diário", "error")
        delta = bal1 - bal0
        result = "win" if delta > 0.005 else "loss" if delta < -0.005 else "push"
        if test_otc:
            append_test({**rec, "result": result, "pnl": round(delta, 2), "at_result": now().isoformat()})
        else:
            await self.http.patch(f"{API}/api/journal/{trade['id']}", json={"result": result})
        await self.event(f"resultado {a.quotex} {direction}: {result} (saldo {delta:+.2f})", "info")


def append_test(rec: dict) -> None:
    with open(TEST_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_tests() -> dict[str, dict]:
    """Última linha de cada entrada do teste OTC (a de resultado substitui a de abertura)."""
    out: dict[str, dict] = {}
    if TEST_LOG.exists():
        for ln in TEST_LOG.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            out[r["id"]] = r
    return out


def test_day_totals(cfg, at: datetime | None = None) -> tuple[int, float]:
    """Entradas e resultado ($) do teste OTC no dia (fuso do config). Entrada em aberto conta como perda."""
    tz = ZoneInfo(cfg.timezone)
    today = (at or now()).astimezone(tz).date()
    n, pnl = 0, 0.0
    for r in load_tests().values():
        if datetime.fromisoformat(r["at"]).astimezone(tz).date() == today:
            n += 1
            pnl += r["pnl"] if r.get("pnl") is not None else (-r["stake"] if r.get("result") == "pending" else 0.0)
    return n, pnl


def find_chrome() -> str | None:
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"), os.environ.get("LOCALAPPDATA")):
        if base and (exe := Path(base) / "Google/Chrome/Application/chrome.exe").exists():
            return str(exe)
    return shutil.which("chrome") or shutil.which("google-chrome")


async def cdp_ready(http: httpx.AsyncClient) -> bool:
    try:
        return (await http.get(f"{CDP}/json/version", timeout=1)).status_code == 200
    except httpx.HTTPError:
        return False


async def open_chrome(http: httpx.AsyncClient) -> None:
    """Abre o Chrome como um processo comum (sem --enable-automation) e com porta de depuração local.

    O Playwright só se conecta depois, via CDP. Assim o Chrome não se apresenta como "controlado por
    automação" e o login com Google funciona. Se o Chrome do robô já estiver aberto, só reconecta.
    """
    if await cdp_ready(http):
        return
    exe = find_chrome()
    if not exe:
        raise RuntimeError("Google Chrome não encontrado — instale o Chrome para usar o robô")
    subprocess.Popen([
        exe, f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={PROFILE}",
        "--no-first-run", "--no-default-browser-check", "--start-maximized",
        # A aba da Quotex fica em segundo plano quando o painel está na frente: sem isso o Chrome
        # congela timers/renderização dela e os cliques do robô atrasam.
        "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        URL, API,
    ])
    for _ in range(60):
        if await cdp_ready(http):
            return
        await asyncio.sleep(0.5)
    raise RuntimeError("o Chrome do robô não abriu a porta de depuração")


def single_instance() -> socket.socket | None:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        s.close()
        return None
    return s  # manter a referência viva segura a trava até o processo terminar


async def main() -> None:
    lock = single_instance()
    if lock is None:
        print("[robô] já existe um robô rodando — este sai sem fazer nada", file=sys.stderr)
        return
    PROFILE.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=15) as http, async_playwright() as p:
        await open_chrome(http)
        browser = await p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        # O robô só controla a aba da Quotex; o painel fica numa segunda aba do mesmo Chrome.
        page = next((pg for pg in ctx.pages if "quotex" in pg.url), None)
        if page is None:
            page = await ctx.new_page()
            await page.goto(URL)
        if not any(pg.url.startswith(API) for pg in ctx.pages):
            await (await ctx.new_page()).goto(API)
        await page.bring_to_front()
        bot = Bot(QuotexPage(page), http)
        await bot.event("navegador do robô aberto — faça login e selecione a CONTA DEMO", "info")
        closed = asyncio.Event()
        browser.on("disconnected", lambda *_: closed.set())
        page.on("close", lambda *_: closed.set())
        tasks = [asyncio.create_task(t) for t in (bot.heartbeat(), bot.listen(), bot.worker(), bot.keep_otc_alive())]
        await closed.wait()
        for t in tasks:
            t.cancel()
        await bot.event("navegador do robô fechado", "info")


if __name__ == "__main__":
    asyncio.run(main())
