"""Gravador OTC: remontagem dos eventos socket.io da Quotex e candles a partir de ticks."""
import base64
import json

import pandas as pd

from backend.otc.build import ticks_to_1m
from backend.otc.recorder import SocketIO, decode_payload, parse_instrument


def _bin(obj) -> dict:
    return {"opcode": 2, "payloadData": base64.b64encode(b"\x04" + json.dumps(obj).encode()).decode()}


def test_placeholder_then_binary_body():
    sio = SocketIO()
    assert sio.feed("s", decode_payload({"opcode": 1, "payloadData": '451-["quotes/stream",{"_placeholder":true,"num":0}]'})) is None
    name, body = sio.feed("s", decode_payload(_bin([["EURNZD_otc", 1793026349.748, 1.9782, 1]])))
    assert name == "quotes/stream" and body[0][2] == 1.9782


def test_binary_without_placeholder_is_ignored():
    assert SocketIO().feed("s", '[["X",1,2,0]]') is None


def test_placeholders_are_per_socket():
    sio = SocketIO()
    sio.feed("a", '451-["quotes/stream",{"_placeholder":true,"num":0}]')
    assert sio.feed("b", "[[1]]") is None
    assert sio.feed("a", "[[1]]")[0] == "quotes/stream"


def test_parse_instrument():
    row = [368, "ATOUSD_otc", "Cosmos (OTC)", "cryptocurrency", 4, 48, 60, 30, 3, 1, 0, 0, [], 1793059200, True]
    assert parse_instrument(row) == {"symbol": "ATOUSD_otc", "name": "Cosmos (OTC)",
                                     "type": "cryptocurrency", "payout": 48, "open": True}


def test_ticks_to_1m_ohlc_and_drops_forming_minute():
    t0 = pd.Timestamp("2026-10-26 12:00", tz="UTC").timestamp()
    ticks = pd.DataFrame({"ts": [t0 + 1, t0 + 20, t0 + 40, t0 + 59.9, t0 + 61, t0 + 130],
                          "price": [1.0, 1.3, 0.9, 1.1, 1.2, 1.5]})
    out = ticks_to_1m(ticks, now=pd.Timestamp("2026-10-26 12:02:30", tz="UTC"))
    assert list(out.index.strftime("%H:%M")) == ["12:00", "12:01"]  # 12:02 ainda em formação
    assert out.iloc[0].to_dict() == {"open": 1.0, "high": 1.3, "low": 0.9, "close": 1.1, "volume": 4.0}
