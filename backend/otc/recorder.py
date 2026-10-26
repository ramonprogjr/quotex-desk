"""Gravador de ticks OTC — SÓ LEITURA.

    .venv/Scripts/python -m backend.otc.recorder

Conecta no Chrome do robô (porta CDP 127.0.0.1:9223, aberto pelo botão "Abrir navegador do robô")
e escuta o websocket que a própria página da Quotex já usa. Não envia nada para a Quotex e não
clica em nada. A Quotex só transmite o par aberto no gráfico, então é ele que fica gravado.

Única ação na página: se ficar 3 min sem nenhum tick com a sala de operações aberta, recarrega a aba.
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from datetime import datetime, timezone

from playwright.async_api import async_playwright

from . import OTC_DIR, TICKS_DIR

CDP = "http://127.0.0.1:9223"
STALL_S = 180


def decode_payload(resp: dict) -> str:
    """Texto do frame; frames binários do socket.io vêm em base64 com um byte de tipo na frente."""
    data = resp["payloadData"]
    if resp.get("opcode") == 2:
        raw = base64.b64decode(data)
        i = next((k for k, b in enumerate(raw[:4]) if b in b"[{"), 0)
        data = raw[i:].decode("utf-8", "replace")
    return data


class SocketIO:
    """Remonta eventos socket.io (EIO3): `451-["evento",{_placeholder}]` + frame binário com o corpo."""

    def __init__(self):
        self.pending: dict[str, str] = {}

    def feed(self, sock: str, text: str) -> tuple[str, object] | None:
        if text.startswith("451-"):
            try:
                self.pending[sock] = json.loads(text[4:])[0]
            except (ValueError, IndexError):
                pass
            return None
        if text.startswith("42"):
            try:
                ev = json.loads(text[2:])
                return (ev[0], ev[1] if len(ev) > 1 else None)
            except (ValueError, IndexError):
                return None
        if text[:1] in "[{" and sock in self.pending:
            name = self.pending.pop(sock)
            try:
                return (name, json.loads(text))
            except ValueError:
                return None
        return None


def parse_instrument(row: list) -> dict | None:
    if not isinstance(row, list) or len(row) < 15:
        return None
    return {"symbol": row[1], "name": row[2], "type": row[3], "payout": row[5], "open": row[14]}


class TickWriter:
    def __init__(self):
        self.files: dict[tuple[str, str], object] = {}
        self.count = 0
        self.last: dict[str, float] = {}
        self.flushed = 0.0

    def write(self, asset: str, ts: float, price: float) -> None:
        day = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
        key = (asset, day)
        f = self.files.get(key)
        if f is None:
            for k in [k for k in self.files if k[0] == asset]:  # virou o dia: fecha o arquivo antigo
                self.files.pop(k).close()
            d = TICKS_DIR / asset
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{day}.csv"
            new = not p.exists()
            f = self.files[key] = open(p, "a", encoding="utf-8", newline="")  # noqa: SIM115
            if new:
                f.write("ts,price\n")
        f.write(f"{ts:.3f},{price}\n")
        self.count += 1
        self.last[asset] = max(ts, self.last.get(asset, 0))
        if time.monotonic() - self.flushed > 1:  # o painel lê o arquivo ao vivo
            self.flush()

    def flush(self) -> None:
        for f in self.files.values():
            f.flush()
        self.flushed = time.monotonic()


class Recorder:
    def __init__(self):
        self.sio = SocketIO()
        self.ticks = TickWriter()
        self.last_tick_wall = time.time()
        self.last_instruments: list[dict] = []
        self.page_url = ""
        self.pages: dict = {}  # aba → último tick (relógio local)
        self.started = datetime.now(timezone.utc).isoformat()

    def log(self, msg: str) -> None:
        print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)

    def on_frame(self, ev: dict) -> bool:
        """Processa um frame; True se trouxe tick."""
        out = self.sio.feed(ev["requestId"], decode_payload(ev["response"]))
        if not out:
            return False
        name, body = out
        if name == "quotes/stream" and isinstance(body, list):
            got = False
            for q in body:
                if isinstance(q, list) and len(q) >= 3:
                    self.ticks.write(str(q[0]), float(q[1]), float(q[2]))
                    got = True
            return got
        elif name == "history/list/v2" and isinstance(body, dict):
            asset = str(body.get("asset"))
            for h in body.get("history") or []:
                if isinstance(h, list) and len(h) >= 2:
                    self.ticks.write(asset, float(h[0]), float(h[1]))
            self.log(f"histórico recebido: {asset}, {len(body.get('history') or [])} ticks")
        elif name == "instruments/list" and isinstance(body, list):
            rows = [r for r in (parse_instrument(x) for x in body) if r]
            if rows == self.last_instruments:  # a Quotex reenvia a lista a cada ~2 min; só grava mudanças
                return
            self.last_instruments = rows
            OTC_DIR.mkdir(parents=True, exist_ok=True)
            with open(OTC_DIR / "instruments.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(), "rows": rows}, ensure_ascii=False) + "\n")
            self.log(f"lista de pares mudou: {len(rows)} pares")
        return False

    def status(self) -> dict:
        return {"updated": datetime.now(timezone.utc).isoformat(), "started": self.started,
                "page": self.page_url, "ticks_written": self.ticks.count,
                "last_tick": {a: datetime.fromtimestamp(t, timezone.utc).isoformat() for a, t in self.ticks.last.items()},
                "stalled_s": round(time.time() - self.last_tick_wall)}

    async def attach(self, ctx, page, reload: bool) -> None:
        """Escuta o websocket de uma aba. Cada aba da Quotex tem a própria conexão e o próprio par."""
        cdp = await ctx.new_cdp_session(page)
        self.pages[page] = time.time()

        def on_frame(ev: dict) -> None:
            if self.on_frame(ev):
                self.pages[page] = time.time()

        cdp.on("Network.webSocketFrameReceived", on_frame)
        await cdp.send("Network.enable")
        if reload:  # pega a lista de pares e o histórico curto que só vêm na conexão
            await page.reload()
        self.log(f"escutando aba {len(self.pages)}: {page.url}")

    async def session(self, p) -> None:
        browser = await p.chromium.connect_over_cdp(CDP)
        ctx = browser.contexts[0]
        self.pages = {}
        for pg in [pg for pg in ctx.pages if "quotex" in pg.url]:
            await self.attach(ctx, pg, reload=True)
        if not self.pages:
            raise RuntimeError("aba da Quotex não encontrada no Chrome do robô")
        # abas novas da Quotex abertas depois também entram
        ctx.on("page", lambda pg: asyncio.ensure_future(self._maybe_attach(ctx, pg)))
        self.log("conectado ao Chrome do robô — gravando")
        while browser.is_connected():
            await asyncio.sleep(10)
            for pg in [pg for pg in self.pages if pg.is_closed()]:
                self.pages.pop(pg)
                self.log("aba da Quotex fechada")
            self.page_url = ", ".join(pg.url for pg in self.pages)
            self.last_tick_wall = max(self.pages.values(), default=self.last_tick_wall)
            self.ticks.flush()
            st = self.status()
            (OTC_DIR / "status.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
            for pg, last in list(self.pages.items()):
                if time.time() - last > STALL_S and "/trade" in pg.url:
                    self.log(f"aba {pg.url} {time.time() - last:.0f} s sem ticks — recarregando")
                    self.pages[pg] = time.time()
                    await pg.reload()

    async def _maybe_attach(self, ctx, page) -> None:
        for _ in range(30):  # a aba nova nasce em branco; espera ela ir para a Quotex
            if "quotex" in page.url:
                return await self.attach(ctx, page, reload=False)
            await asyncio.sleep(1)

    async def run(self) -> None:
        OTC_DIR.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as p:
            while True:
                try:
                    await self.session(p)
                    self.log("Chrome do robô fechado ou aba perdida — tentando de novo em 10 s")
                except Exception as e:  # noqa: BLE001 — o gravador não pode morrer
                    self.log(f"sem conexão ({e}); tentando de novo em 10 s")
                self.ticks.flush()
                await asyncio.sleep(10)


if __name__ == "__main__":
    try:
        asyncio.run(Recorder().run())
    except KeyboardInterrupt:
        sys.exit(0)
