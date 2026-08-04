"""Async SQLAlchemy engine/session helpers (SQLite via aiosqlite)."""
from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from webapp.db.models import Base

# Колонки, добавленные к jobs позже (расход прогона + флаг точного переноса).
# create_all не доращивает существующие таблицы — для старых БД добавляем их
# вручную (см. _ensure_job_columns).
_JOB_ADDED_COLUMNS: dict[str, str] = {
    "in_tokens": "INTEGER",
    "out_tokens": "INTEGER",
    "cost_rub": "FLOAT",
    "duration_ms": "INTEGER",
    "exact_transfer": "INTEGER",   # SQLite-представление Boolean
}


def make_engine(url: str) -> AsyncEngine:
    return create_async_engine(url, echo=False, future=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _ensure_job_columns(sync_conn) -> None:
    """Идемпотентная мини-миграция: доращиваем jobs недостающими колонками.
    Имена колонок — фиксированные константы (не пользовательский ввод),
    поэтому прямой ALTER TABLE безопасен. Все колонки nullable → у старых строк
    значения останутся NULL."""
    existing = {c["name"] for c in inspect(sync_conn).get_columns("jobs")}
    for name, sql_type in _JOB_ADDED_COLUMNS.items():
        if name not in existing:
            sync_conn.exec_driver_sql(
                f"ALTER TABLE jobs ADD COLUMN {name} {sql_type}")


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_job_columns)
