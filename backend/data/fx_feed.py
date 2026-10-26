"""Candles de forex.

Provedores (em ordem de preferência, conforme o .env):
- OANDA_TOKEN       → conta demo (practice) da OANDA. Sem limite prático de requisições.
- TWELVEDATA_API_KEY → plano grátis: 8 req/min e 800 req/dia. Serve para histórico;
                       ao vivo aguenta só ~1 par acompanhado por vez.
"""
from __future__ import annotations

import time

import httpx
import pandas as pd

from .. import env

OANDA_URL = "https://api-fxpractice.oanda.com/v3"
TD_URL = "https://api.twelvedata.com/time_series"


def provider() -> str:
    if env.get("OANDA_TOKEN"):
        return "oanda"
    if env.get("TWELVEDATA_API_KEY"):
        return "twelvedata"
    raise RuntimeError(
        "Forex sem fonte de dados: defina OANDA_TOKEN (conta demo OANDA) ou "
        "TWELVEDATA_API_KEY no arquivo .env"
    )


def _frame(records: list[dict]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                            index=pd.DatetimeIndex([], tz="UTC", name="ts"))
    df = pd.DataFrame(records)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts")[["open", "high", "low", "close", "volume"]].astype(float)
    return df[~df.index.duplicated(keep="last")].sort_index()


# ---------------- OANDA ----------------
def _oanda_get(instrument: str, params: dict, include_incomplete: bool = False) -> list[dict]:
    headers = {"Authorization": f"Bearer {env.get('OANDA_TOKEN')}"}
    r = httpx.get(f"{OANDA_URL}/instruments/{instrument}/candles", params=params,
                  headers=headers, timeout=30)
    r.raise_for_status()
    out = []
    for c in r.json().get("candles", []):
        if not c.get("complete") and not include_incomplete:
            continue
        m = c["mid"]
        out.append({"ts": c["time"], "open": m["o"], "high": m["h"], "low": m["l"],
                    "close": m["c"], "volume": c.get("volume", 0)})
    return out


def _oanda_history(symbol: str, start: pd.Timestamp, end: pd.Timestamp, progress,
                   granularity: str = "M1", step: pd.Timedelta = pd.Timedelta(minutes=1)) -> pd.DataFrame:
    instrument = symbol.replace("/", "_")
    cursor = start
    rows: list[dict] = []
    while cursor < end:
        batch = _oanda_get(instrument, {"granularity": granularity, "price": "M", "count": 5000,
                                        "from": cursor.isoformat()})
        if not batch:
            break
        rows.extend(batch)
        last = pd.Timestamp(batch[-1]["ts"])
        if last <= cursor:
            break
        cursor = last + step
        if progress:
            progress(last)
    df = _frame(rows)
    return df[df.index < end]


# ---------------- Twelve Data ----------------
def _td_get(symbol: str, params: dict) -> list[dict]:
    p = {"symbol": symbol, "interval": "1min", "timezone": "UTC", "format": "JSON",
         "apikey": env.get("TWELVEDATA_API_KEY"), **params}
    r = httpx.get(TD_URL, params=p, timeout=30)
    r.raise_for_status()
    data = r.json()
    if data.get("status") == "error":
        if "no data" in str(data.get("message", "")).lower():
            return []
        raise RuntimeError(f"Twelve Data: {data.get('message')}")
    return [{"ts": v["datetime"], "open": v["open"], "high": v["high"], "low": v["low"],
             "close": v["close"], "volume": v.get("volume", 0)} for v in data.get("values", [])]


def _td_history(symbol: str, start: pd.Timestamp, end: pd.Timestamp, progress) -> pd.DataFrame:
    # Pagina de trás para frente: cada chamada traz os 5000 candles mais recentes antes de `cursor`.
    cursor = end
    rows: list[dict] = []
    while cursor > start:
        batch = _td_get(symbol, {"outputsize": 5000,
                                 "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
                                 "end_date": cursor.strftime("%Y-%m-%d %H:%M:%S")})
        if not batch:
            break
        rows.extend(batch)
        earliest = min(pd.Timestamp(b["ts"], tz="UTC") for b in batch)
        if earliest >= cursor:
            break
        cursor = earliest - pd.Timedelta(minutes=1)
        if progress:
            progress(earliest)
        time.sleep(8)  # plano grátis: 8 requisições por minuto
    df = _frame(rows)
    return df[(df.index >= start) & (df.index < end)]


# ---------------- interface ----------------
def fetch_history(symbol: str, start: pd.Timestamp, end: pd.Timestamp, progress=None) -> pd.DataFrame:
    from . import drop_forming

    if provider() == "oanda":
        return drop_forming(_oanda_history(symbol, start, end, progress))
    return drop_forming(_td_history(symbol, start, end, progress))


def fetch_forming(symbol: str, tf: int = 1) -> pd.DataFrame | None:
    """Candle em formação (só OANDA; o plano grátis da Twelve Data não aguenta consultas a cada segundos)."""
    if provider() != "oanda":
        return None
    rows = _oanda_get(symbol.replace("/", "_"), {"granularity": f"M{tf}", "price": "M", "count": 2},
                      include_incomplete=True)
    df = _frame(rows)
    if df.empty:
        return None
    last = df.iloc[[-1]]
    if last.index[-1] + pd.Timedelta(minutes=tf) > pd.Timestamp.now(tz="UTC"):
        return last
    return None


def fetch_fine(symbol: str, start: pd.Timestamp, end: pd.Timestamp, progress=None) -> pd.DataFrame:
    """Candles de 5 segundos (OANDA S5) — para medir o efeito de entrar atrasado."""
    if provider() != "oanda":
        raise RuntimeError("teste de atraso no forex precisa de OANDA_TOKEN (dados de 5 s)")
    df = _oanda_history(symbol, start, end, progress, granularity="S5", step=pd.Timedelta(seconds=5))
    return df


def fetch_recent(symbol: str, n: int = 500, tf: int = 1) -> pd.DataFrame:
    from . import drop_forming

    if provider() == "oanda":
        rows = _oanda_get(symbol.replace("/", "_"), {"granularity": f"M{tf}", "price": "M",
                                                     "count": min(n, 5000)})
    else:
        rows = _td_get(symbol, {"interval": f"{tf}min", "outputsize": min(n, 5000)})
    return drop_forming(_frame(rows), tf=tf)
