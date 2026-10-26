"""Mapa entre os ativos da Quotex e as fontes de dados externas."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Asset:
    key: str        # identificador interno
    quotex: str     # como aparece na Quotex
    source: str     # "crypto" (CCXT/Binance), "fx" (OANDA/Twelve Data) ou "otc" (ticks gravados da Quotex)
    symbol: str     # símbolo na fonte


ASSETS: dict[str, Asset] = {
    a.key: a
    for a in [
        Asset("BTCUSD", "Bitcoin", "crypto", "BTC/USDT"),
        Asset("ETHUSD", "Ethereum", "crypto", "ETH/USDT"),
        Asset("EURUSD", "EUR/USD", "fx", "EUR/USD"),
        Asset("GBPUSD", "GBP/USD", "fx", "GBP/USD"),
        Asset("USDJPY", "USD/JPY", "fx", "USD/JPY"),
    ]
}

# Pares OTC: só existem dentro da Quotex. Os dados vêm do gravador (backend.otc.recorder), que grava
# o par aberto no Chrome do robô. Além destes, entra qualquer par que já tenha ticks gravados.
OTC_DEFAULT = ["EURNZD_otc", "NZDUSD_otc"]


def otc_asset(symbol: str, name: str | None = None) -> Asset:
    base = symbol.removesuffix("_otc")
    name = name or (f"{base[:3]}/{base[3:]} (OTC)" if len(base) == 6 else f"{base} (OTC)")
    return Asset(symbol.upper(), name, "otc", symbol)


def _quotex_names() -> dict[str, str]:
    """Nome que a Quotex mostra para cada par, da última lista gravada (ex.: BRLUSD_otc → USD/BRL (OTC))."""
    import json

    from .otc import OTC_DIR

    p = OTC_DIR / "instruments.jsonl"
    try:
        with open(p, "rb") as f:
            f.seek(max(0, p.stat().st_size - 200_000))
            last = f.read().splitlines()[-1]
        return {r["symbol"]: r["name"] for r in json.loads(last)["rows"]}
    except (OSError, ValueError, IndexError, KeyError):
        return {}


def register_otc() -> None:
    """Inclui os pares OTC já gravados (chamado de novo pelo painel para achar pares novos)."""
    from .otc import TICKS_DIR

    found = sorted(p.name for p in TICKS_DIR.iterdir() if p.is_dir()) if TICKS_DIR.exists() else []
    new = [s for s in dict.fromkeys(OTC_DEFAULT + found) if s.upper() not in ASSETS]
    if not new:
        return
    names = _quotex_names()
    for sym in new:
        a = otc_asset(sym, names.get(sym))
        ASSETS[a.key] = a

register_otc()


def is_otc(quotex_name: str) -> bool:
    """Ativos OTC têm preço gerado pela própria corretora: não há fonte externa confiável."""
    n = quotex_name.upper().replace(" ", "")
    return "OTC" in n


def get_asset(key: str) -> Asset:
    try:
        return ASSETS[key.upper()]
    except KeyError:
        raise ValueError(f"Ativo desconhecido: {key}. Disponíveis: {', '.join(ASSETS)}") from None
