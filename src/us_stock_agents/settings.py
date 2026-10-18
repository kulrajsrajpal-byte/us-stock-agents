from __future__ import annotations

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def resolve_database_url() -> str:
    """Railway injects DATABASE_URL / DATABASE_PRIVATE_URL from Postgres.

    Never silently ignore a Postgres URL; SQLite is local-only fallback.
    """
    for key in (
        "DATABASE_URL",
        "DATABASE_PRIVATE_URL",
        "POSTGRES_URL",
        "DATABASE_PUBLIC_URL",
    ):
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    return "sqlite:///./state/local.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    # Overridden in get_settings() from resolve_database_url() so Railway env always wins.
    database_url: str = "sqlite:///./state/local.db"
    redis_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    # 24x7 paper + coach (enforced in get_settings if old Railway env still set)
    run_interval_minutes: int = 15
    market_hours_only: bool = False
    skip_sentiment: bool = True
    timezone: str = "America/New_York"
    profit_target_pct: float = 5.0
    max_drawdown_pct: float = 10.0
    champion_min_trades: int = 5
    initial_cash: float = 100_000.0
    api_key: str = ""

    hermes_enabled: bool = False
    hermes_api_url: str = ""
    hermes_api_key: str = ""
    hermes_model: str = "default"
    local_coach_enabled: bool = True
    continue_until_profit: bool = True
    hermes_flat_band_pct: float = 1.0


@lru_cache
def get_settings() -> Settings:
    """Load settings, then enforce the continuous paper-trading loop.

    Old Railway vars (MARKET_HOURS_ONLY=true, RUN_INTERVAL_MINUTES=30) used to
    override defaults and pause overnight. We force the 24x7 coach profile here.
    """
    settings = Settings(database_url=resolve_database_url())
    settings.run_interval_minutes = 15
    settings.market_hours_only = False
    settings.skip_sentiment = True
    settings.continue_until_profit = True
    settings.local_coach_enabled = True
    return settings
