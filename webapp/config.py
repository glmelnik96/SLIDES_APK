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

    # Раздаваемые файлы вне репозитория (крупные бинарники вроде .skill-архива).
    # Прод кладёт их в /var/lib/app2/downloads (обновление — scp, без деплоя).
    downloads_dir: Path = Field(ROOT / "downloads", alias="DOWNLOADS_DIR")

    # Queue capacity. Goal: tasks WAIT in the queue rather than 429. These caps are
    # safety valves (memory/abuse), set high enough to never trip in normal use.
    # max_active = total jobs in the system (running + waiting); per-user = how many
    # one user may have queued at once. Real parallelism is build_workers (how many
    # builds run at once); the rest wait. Cloud.ru concurrency is bounded separately
    # by CLOUDRU_MAX_INFLIGHT in the pipeline client, so workers can be raised safely.
    max_active: int = Field(60, alias="QUEUE_MAX_ACTIVE")
    user_queue_limit: int = Field(15, alias="USER_QUEUE_LIMIT")
    build_workers: int = Field(3, alias="BUILD_WORKERS")

    # Per-build watchdog: a build exceeding this is force-failed and its worker
    # freed, so a hung/zombie job can never hold a slot forever. ~2x the real
    # build time (heavy decks ran ~10-17 min) → 40 min default.
    build_timeout_sec: int = Field(2400, alias="BUILD_TIMEOUT_SEC")

    # Result retention (sessions + Job rows older than this are purged).
    retention_hours: int = Field(24, alias="RETENTION_HOURS")

    # Общий с платформой секрет для машинного чтения GET /internal/stats
    # (заголовок X-Ingest-Token). Имя переменной историческое — раньше тем же
    # секретом подписывался пуш на шлюз; App1 попросил переиспользовать её, а не
    # заводить второй секрет и рестарты у всех. Пусто = эндпоинт выключен (404),
    # это нормальное состояние локалки.
    stats_token: str = Field("", alias="USAGE_INGEST_TOKEN")

    # Local-dev only: when no gateway is present, treat requests as this user id.
    # Leave empty in production so a missing X-User-Id is a hard 401.
    dev_user_id: str = Field("", alias="DEV_USER_ID")

    def normalized_prefix(self) -> str:
        p = self.app_prefix.strip()
        if p and not p.startswith("/"):
            p = "/" + p
        return p.rstrip("/")


settings = Settings()
