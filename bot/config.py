"""Centralised settings — loaded once from .env via pydantic-settings."""
from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Cloud.ru Foundation Models
    cloudru_api_key: str = Field(..., alias="CLOUDRU_API_KEY")
    cloudru_base_url: str = Field("https://foundation-models.api.cloud.ru/v1", alias="CLOUDRU_BASE_URL")
    cloudru_rps_limit: int = Field(18, alias="CLOUDRU_RPS_LIMIT")

    # Telegram
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    telegram_whitelist_raw: str = Field("", alias="TELEGRAM_WHITELIST")

    # Redis
    redis_url: str = Field("redis://redis:6379", alias="REDIS_URL")
    redis_password: str = Field("", alias="REDIS_PASSWORD")

    # S3 (Cloud.ru Object Storage)
    s3_endpoint_url: str = Field("https://s3.cloud.ru", alias="S3_ENDPOINT_URL")
    s3_region: str = Field("ru-central-1", alias="S3_REGION")
    s3_access_key: str = Field("", alias="S3_ACCESS_KEY")
    s3_secret_key: str = Field("", alias="S3_SECRET_KEY")
    s3_bucket: str = Field("slides-bot", alias="S3_BUCKET")
    s3_template_prefix: str = Field("templates/v5/", alias="S3_TEMPLATE_PREFIX")
    s3_session_prefix: str = Field("sessions/", alias="S3_SESSION_PREFIX")

    # Runtime
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    sentry_dsn: str = Field("", alias="SENTRY_DSN")
    active_template_version: int = Field(5, alias="ACTIVE_TEMPLATE_VERSION")
    max_concurrent_decks: int = Field(3, alias="MAX_CONCURRENT_DECKS")
    halt_timeout_seconds: int = Field(3600, alias="HALT_TIMEOUT_SECONDS")
    session_ttl_days: int = Field(7, alias="SESSION_TTL_DAYS")

    @property
    def telegram_whitelist(self) -> set[int]:
        return {int(x.strip()) for x in self.telegram_whitelist_raw.split(",") if x.strip()}

    @field_validator("cloudru_api_key")
    @classmethod
    def _strip_key(cls, v: str) -> str:
        # Tolerate the YAML-ish format observed in the source .env (`apiKey: 'xxx'`)
        return v.strip().strip("'\"")


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
