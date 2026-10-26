"""Alertas opcionais no Telegram (TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID no .env)."""
from __future__ import annotations

import logging

import httpx

from . import env

log = logging.getLogger(__name__)


async def telegram(text: str) -> None:
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(f"https://api.telegram.org/bot{token}/sendMessage",
                         json={"chat_id": chat, "text": text})
    except httpx.HTTPError as e:
        log.warning("telegram falhou: %s", e)
