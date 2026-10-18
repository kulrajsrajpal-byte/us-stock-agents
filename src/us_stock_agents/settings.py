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
    # Run often on Railway; paper + coach keep iterating 24x7.
    run_interval_minutes: int = 15
    # False = keep scanning/evolving overnight (paper only).
    market_hours_only: bool = False
    # Skip RSS sentiment (use neutral) so cycles finish under wall timeout.
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
    # Built-in Postgres learner (no external Hermes login required)
    local_coach_enabled: bool = True
    # Keep mutating/consulting until a champion hits PROFIT_TARGET_PCT
    continue_until_profit: bool = True
    # Also consult when return is near-flat after enough trades
    hermes_flat_band_pct: float = 1.0


@lru_cache
def get_settings() -> Settings:
    return Settings(database_url=resolve_database_url())
