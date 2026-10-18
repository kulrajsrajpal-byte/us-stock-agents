from __future__ import annotations

import logging

import httpx

from us_stock_agents.settings import get_settings

log = logging.getLogger(__name__)


def send_telegram(text: str) -> bool:
    settings = get_settings()
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        log.info("Telegram not configured; skipping notify.")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = httpx.post(
            url,
            json={"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True},
            timeout=8,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Telegram notify failed: %s", exc)
        return False
