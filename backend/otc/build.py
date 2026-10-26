"""Monta candles de 1 min a partir dos ticks OTC gravados.

    .venv/Scripts/python -m backend.otc.build            (todos os pares gravados)
    .venv/Scripts/python -m backend.otc.build EURNZD_otc

Saída no formato padrão do projeto (índice `ts` UTC = início do candle; open/high/low/close/volume,
volume = número de ticks). Minutos sem nenhum tick ficam de fora — o `resample` já descarta
candles de M5 incompletos. O minuto em andamento não entra.
"""
from __future__ import annotations

import sys

import pandas as pd

from . import OTC_DIR, TICKS_DIR


def load_ticks(asset: str) -> pd.DataFrame:
    files = sorted((TICKS_DIR / asset).glob("*.csv"))
    if not files:
        return pd.DataFrame(columns=["ts", "price"])
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    # histórico recebido na reconexão repete ticks que já tínhamos
    return df.drop_duplicates("ts").sort_values("ts", kind="stable").reset_index(drop=True)


def ticks_to_1m(ticks: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    if ticks.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    s = pd.Series(ticks["price"].to_numpy(), index=pd.to_datetime(ticks["ts"], unit="s", utc=True))
    g = s.resample("1min", label="left", closed="left")
    out = pd.DataFrame({"open": g.first(), "high": g.max(), "low": g.min(), "close": g.last(),
                        "volume": g.count().astype(float)})
    out = out[out["volume"] > 0]
    now = now or pd.Timestamp.now(tz="UTC")
    out = out[out.index + pd.Timedelta(minutes=1) <= now]
    out.index.name = "ts"
    return out


def build(asset: str) -> pd.DataFrame:
    df = ticks_to_1m(load_ticks(asset))
    df.to_parquet(OTC_DIR / f"{asset}_1m.parquet")
    return df


def coverage(df: pd.DataFrame) -> str:
    if df.empty:
        return "sem dados"
    span = (df.index[-1] - df.index[0]).total_seconds() / 60 + 1
    return (f"{len(df)} candles de {df.index[0]:%d/%m %H:%M} a {df.index[-1]:%d/%m %H:%M} UTC "
            f"({len(df) / span:.0%} dos minutos do período)")


if __name__ == "__main__":
    assets = sys.argv[1:] or sorted(p.name for p in TICKS_DIR.iterdir() if p.is_dir())
    for a in assets:
        print(f"{a}: {coverage(build(a))}")
