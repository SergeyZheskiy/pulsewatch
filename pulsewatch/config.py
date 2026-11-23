from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All settings come from environment variables prefixed with PULSEWATCH_ (or a .env file)."""

    model_config = SettingsConfigDict(env_prefix="PULSEWATCH_", env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./pulsewatch.db"
    api_key: str = "change-me"

    scheduler_enabled: bool = True
    tick_seconds: float = 1.0
    max_concurrency: int = 20
    # A monitor goes DOWN only after this many consecutive failures, so one dropped packet doesn't page you.
    failure_threshold: int = 2
    # Refuse targets such as localhost or 10.0.0.0/8 (basic SSRF guard for shared deployments).
    allow_private_targets: bool = False

    webhook_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    status_page_title: str = "Service Status"
    user_agent: str = "pulsewatch/1.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
