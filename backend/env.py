"""Carrega variáveis do arquivo .env da raiz (sem dependência externa)."""
from __future__ import annotations

import os

from .config import ROOT

_loaded = False


def load_env() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get(name: str) -> str | None:
    load_env()
    return os.environ.get(name) or None
