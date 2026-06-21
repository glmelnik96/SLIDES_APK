"""App2 configuration (pydantic-settings).

Auth is owned by the gateway; App2 trusts the X-User-* headers it injects. All
values have local-dev-friendly defaults so `python -m webapp` still works without
a gateway; production overrides them via the systemd EnvironmentFile (.env).
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8",
        extra="ignore", populate_by_name=True,
    )

    # URL prefix the gateway mounts this app under ("/slides" in prod). Empty for
    # standalone local dev. The frontend prepends it to every API/asset/nav URL.
    app_prefix: str = Field("", alias="APP_PREFIX")

    # Bind address. The contract requires the upstream to listen on loopback only
    # (the gateway is the sole public entrypoint); never expose 0.0.0.0 in prod.
    host: str = Field("127.0.0.1", alias="HOST")
    # Bind port (gateway upstream). 8012 for App2 per the integration contract.
    port: int = Field(8000, alias="PORT")

    db_url: str = Field("sqlite+aiosqlite:///./data/app2.db", alias="DB_URL")
    data_dir: Path = Field(ROOT / "data", alias="DATA_DIR")

    # Per-user queue limit (how many builds one user may have in the system at
    # once). Global concurrency stays 1 worker — the engine is RPS-bound.
    user_queue_limit: int = Field(5, alias="USER_QUEUE_LIMIT")

    # Result retention (sessions + Job rows older than this are purged).
    retention_hours: int = Field(24, alias="RETENTION_HOURS")

    # Local-dev only: when no gateway is present, treat requests as this user id.
    # Leave empty in production so a missing X-User-Id is a hard 401.
    dev_user_id: str = Field("", alias="DEV_USER_ID")

    def normalized_prefix(self) -> str:
        p = self.app_prefix.strip()
        if p and not p.startswith("/"):
            p = "/" + p
        return p.rstrip("/")


settings = Settings()
