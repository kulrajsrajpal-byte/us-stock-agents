from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./state/local.db"
    redis_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    run_interval_minutes: int = 30
    market_hours_only: bool = True
    timezone: str = "America/New_York"
    profit_target_pct: float = 5.0
    max_drawdown_pct: float = 10.0
    champion_min_trades: int = 5
    initial_cash: float = 100_000.0
    api_key: str = ""  # optional protect /run endpoints


@lru_cache
def get_settings() -> Settings:
    return Settings()
