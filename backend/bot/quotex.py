"""Piloto da página da Quotex (Playwright). Só clica e lê — nunca digita login/senha.

O site não tem API oficial e muda de vez em quando, por isso cada ação procura o
elemento por TEXTO visível (ex.: "Comprar", "Investimento", "CONTA DEMO") e há um
autodiagnóstico (`self_check`) que diz o que foi encontrado.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

from playwright.async_api import Page

ACCOUNT_RE = re.compile(r"(CONTA\s+(DEMO|REAL)|DEMO\s+ACCOUNT|LIVE\s+ACCOUNT|REAL\s+ACCOUNT)", re.I)
MONEY_RE = re.compile(r"[\$R]\$?\s*([\d.,]+)")
PCT_RE = re.compile(r"(\d{2,3})\s*%")

# JS auxiliar: acha o menor elemento visível cujo texto bate com a regex
_FIND_JS = """
([pattern, flags, region]) => {
  const re = new RegExp(pattern, flags);
  const W = window.innerWidth, H = window.innerHeight;
  const inRegion = (r) => !region || (
    r.left >= region[0] * W && r.right <= region[2] * W && r.top >= region[1] * H && r.bottom <= region[3] * H);
  let best = null, bestLen = 1e9;
  for (const el of document.querySelectorAll('body *')) {
    if (!el.offsetParent && el.tagName !== 'BODY') continue;
    const t = (el.innerText || '').trim();
    if (!t || t.length > 400 || !re.test(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0 || !inRegion(r)) continue;
    if (t.length < bestLen) { best = el; bestLen = t.length; }
  }
  if (!best) return null;
  best.setAttribute('data-qd', 'hit');
  const box = best.getBoundingClientRect();
  return { text: best.innerText.trim(), x: box.left + box.width / 2, y: box.top + box.height / 2,
           parent: (best.parentElement?.innerText || '').trim().slice(0, 300) };
}
"""

# JS: a partir de um rótulo ("Investimento", "Tempo"), sobe até achar um <input>
_INPUT_NEAR_JS = """
([label]) => {
  const re = new RegExp('^\\\\s*(' + label + ')\\\\s*$', 'i');
  for (const el of document.querySelectorAll('body *')) {
    if (!re.test((el.innerText || '').trim()) || el.children.length > 2) continue;
    let p = el;
    for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
      // a Quotex tem cópias escondidas/desabilitadas (versão mobile) e um checkbox junto do rótulo
      const inp = [...p.querySelectorAll('input')].find(
        (x) => x.offsetParent && !x.disabled && x.type !== 'checkbox' && x.type !== 'hidden');
      if (inp) { document.querySelectorAll('[data-qd-input]').forEach(e => e.removeAttribute('data-qd-input'));
                 inp.setAttribute('data-qd-input', label); return inp.value; }
    }
  }
  return null;
}
"""


def parse_money(text: str) -> float | None:
    m = MONEY_RE.search(text)
    if not m:
        return None
    s = m.group(1)
    s = s.replace(",", "") if s.count(".") == 1 and s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def url_account(url: str) -> str | None:
    """Tipo de conta pela URL da Quotex: /demo-trade = DEMO, /trade = REAL, outra página = 'NONE'.
    None quando não é a Quotex (ex.: página falsa dos testes)."""
    host = urlparse(url).hostname or ""
    if not re.search(r"(^|\.)(quotex\.com|qxbroker\.com)$", host):
        return None
    if "/demo-trade" in url:
        return "DEMO"
    return "REAL" if re.search(r"/trade(\b|$)", url) else "NONE"


def reconcile_account(text_type: str | None, url: str) -> str | None:
    """Só confirma a conta quando texto e URL concordam; na dúvida, None (o robô não opera)."""
    by_url = url_account(url)
    if by_url is None:
        return text_type
    if by_url == "NONE":
        return None
    return by_url if text_type == by_url else None


class QuotexPage:
    def __init__(self, page: Page):
        self.page = page
        self._cdp = None

    async def _find(self, pattern: str, region: tuple | None = None, flags: str = "i") -> dict | None:
        return await self.page.evaluate(_FIND_JS, [pattern, flags, list(region) if region else None])

    async def _click_text(self, pattern: str, region: tuple | None = None) -> bool:
        hit = await self._find(pattern, region)
        if not hit:
            return False
        await self.page.mouse.click(hit["x"], hit["y"])
        return True

    async def _usermenu_texts(self) -> list[str]:
        """Textos da caixa de conta. Ela fica num shadow DOM FECHADO (<qx-usermenu-trigger>), invisível
        para o JS da página; o protocolo do navegador (DOM.getDocument com pierce) enxerga."""
        try:
            if self._cdp is None:
                self._cdp = await self.page.context.new_cdp_session(self.page)
            doc = await self._cdp.send("DOM.getDocument", {"depth": -1, "pierce": True})
        except Exception:  # noqa: BLE001 — sem CDP (ex.: teste com página falsa): cai na busca por texto
            self._cdp = None
            return []
        out: list[str] = []
        stack = [(doc["root"], False)]
        while stack:
            n, inside = stack.pop()
            inside = inside or n.get("localName") == "qx-usermenu-trigger"
            if inside and n.get("nodeType") == 3 and n.get("nodeValue", "").strip():
                out.append(n["nodeValue"].strip())
            kids = (n.get("children") or []) + (n.get("shadowRoots") or [])
            if n.get("contentDocument"):
                kids.append(n["contentDocument"])
            stack.extend((c, inside) for c in reversed(kids))
        return out

    # ---------- leitura ----------
    async def account(self) -> dict:
        """{'type': 'DEMO'|'REAL'|None, 'balance': float|None, 'raw': str}"""
        texts = await self._usermenu_texts()
        if texts:
            raw = " ".join(texts)
            typ = "DEMO" if re.search(r"\bdemo\b", raw, re.I) else "REAL" if re.search(r"\b(real|live)\b", raw, re.I) else None
            bal = parse_money(raw)
        else:
            hit = await self._find(ACCOUNT_RE.pattern)
            if not hit:
                return {"type": None, "balance": None, "raw": ""}
            raw = f"{hit['text']} {hit['parent']}"
            typ = "DEMO" if re.search(r"DEMO", hit["text"], re.I) else "REAL"
            bal = parse_money(hit["parent"]) or parse_money(hit["text"])
        return {"type": reconcile_account(typ, self.page.url), "balance": bal, "raw": raw[:200]}

    async def current_asset(self) -> dict:
        """Par aberto e payout no painel da direita (ex.: 'EUR/USD' 82%)."""
        hit = await self._find(r"^[A-Za-z]{3}/[A-Za-z]{3}( \(OTC\))?\s*\d{2,3}\s*%$|^[A-Za-z][A-Za-z .]+( \(OTC\))?\s*\d{2,3}\s*%$",
                               region=(0.7, 0.0, 1.0, 0.4))
        if not hit:
            return {"name": None, "payout": None}
        txt = hit["text"].replace("\n", " ")
        pm = PCT_RE.search(txt)
        name = PCT_RE.sub("", txt).strip()
        return {"name": name, "payout": int(pm.group(1)) / 100 if pm else None}

    async def input_value(self, label: str) -> str | None:
        return await self.page.evaluate(_INPUT_NEAR_JS, [label])

    # ---------- ações ----------
    async def select_asset(self, name: str, otc: bool = False) -> bool:
        """Abre o seletor de par, procura `name` e clica na linha com esse nome exato.
        Sem `otc`, só aceita a linha SEM '(OTC)'; com `otc` (modo teste), `name` já é 'AUD/NZD (OTC)'."""
        cur = await self.current_asset()
        if cur["name"] == name:
            return True
        # botão do par atual no canto superior esquerdo
        if not await self._click_text(r"^[A-Za-z][A-Za-z /.]+(\(OTC\))?\s*\n?\s*\d{2,3}\s*%$", region=(0.0, 0.05, 0.3, 0.3)):
            return False
        await self.page.wait_for_timeout(600)
        box = self.page.get_by_placeholder(re.compile("Procurar|Search", re.I))
        if await box.count() == 0:
            return False
        await box.first.fill(name.replace("(OTC)", "").strip())
        await self.page.wait_for_timeout(800)
        # linha cujo nome é EXATAMENTE o par (fora do modo teste, nunca "(OTC)")
        pattern = rf"^{re.escape(name)}(\s|$)" if otc else rf"^{re.escape(name)}(\s|$)(?!.*OTC)"
        row = await self._find(pattern, region=(0.0, 0.2, 0.4, 1.0))
        if not row or ("OTC" in row["text"]) != otc:
            await self.page.keyboard.press("Escape")
            return False
        await self.page.mouse.click(row["x"], row["y"])
        await self.page.wait_for_timeout(1200)
        return (await self.current_asset())["name"] == name

    async def _set_input(self, label: str, value: str) -> bool:
        if await self.input_value(label) is None:
            return False
        inp = self.page.locator(f'[data-qd-input="{label}"]')
        await inp.click()
        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.type(value, delay=40)
        await self.page.keyboard.press("Tab")
        await self.page.wait_for_timeout(300)
        return True

    async def set_amount(self, amount: float) -> bool:
        return await self._set_input("Investimento|Investment|Amount", f"{amount:g}")

    async def set_duration(self, minutes: int) -> bool:
        return await self._set_input("Tempo|Time", f"00:{minutes:02d}:00")

    async def click_direction(self, direction: str) -> bool:
        if direction == "CALL":
            return await self._click_text(r"^(Comprar|Up|Para cima|Call)$", region=(0.7, 0.2, 1.0, 0.8))
        return await self._click_text(r"^(Vender|Down|Para baixo|Put)$", region=(0.7, 0.2, 1.0, 0.8))

    # ---------- diagnóstico ----------
    async def self_check(self) -> dict:
        acc = await self.account()
        cur = await self.current_asset()
        checks = {
            "conta": f"{acc['type']} · saldo {acc['balance']}" if acc["type"] else None,
            "par aberto": f"{cur['name']} · payout {cur['payout']}" if cur["name"] else None,
            "campo Tempo": await self.input_value("Tempo|Time"),
            "campo Investimento": await self.input_value("Investimento|Investment|Amount"),
            "botão Comprar": (await self._find(r"^(Comprar|Up|Para cima|Call)$", (0.7, 0.2, 1.0, 0.8)) or {}).get("text"),
            "botão Vender": (await self._find(r"^(Vender|Down|Para baixo|Put)$", (0.7, 0.2, 1.0, 0.8)) or {}).get("text"),
            "seletor de par": (await self._find(r"^[A-Za-z][A-Za-z /.]+(\(OTC\))?\s*\n?\s*\d{2,3}\s*%$",
                                                (0.0, 0.05, 0.3, 0.3)) or {}).get("text"),
        }
        return {"ok": all(v not in (None, "") for v in checks.values()), "checks": checks, "account": acc}
