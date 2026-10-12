from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from us_stock_agents.settings import get_settings


def is_us_regular_session(now: datetime | None = None) -> bool:
    """True Mon–Fri 09:30–16:00 America/New_York (ignores exchange holidays)."""
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    now = now.astimezone(tz) if now else datetime.now(tz)
    if now.weekday() >= 5:
        return False
    open_t = time(9, 30)
    close_t = time(16, 0)
    return open_t <= now.time() <= close_t
