"""init_db идемпотентно доращивает таблицу jobs новыми колонками расхода.

create_all создаёт недостающие ТАБЛИЦЫ, но не добавляет новые КОЛОНКИ в уже
существующие — поэтому для старых SQLite-БД нужен ручной ALTER TABLE.
"""
import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from webapp.db import models
from webapp.db.database import init_db

_NEW = {"in_tokens", "out_tokens", "cost_rub", "duration_ms"}

# Ровно доchange-схема jobs (без новых колонок) — имитация старой БД.
_OLD_JOBS_DDL = """
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    user_id INTEGER NOT NULL,
    mode VARCHAR(16) NOT NULL,
    kind VARCHAR(8) NOT NULL,
    source_filename TEXT,
    status VARCHAR(16),
    result_path TEXT,
    error TEXT,
    created_at DATETIME,
    finished_at DATETIME
)
"""


async def _columns(engine) -> set[str]:
    async with engine.connect() as conn:
        return await conn.run_sync(
            lambda c: {col["name"] for col in inspect(c).get_columns("jobs")})


async def test_fresh_db_has_usage_columns(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'fresh.db'}")
    await init_db(engine)
    assert _NEW <= await _columns(engine)


async def test_migration_adds_columns_to_old_jobs_table(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    async with engine.begin() as conn:
        await conn.execute(text(_OLD_JOBS_DDL))
    assert not (_NEW & await _columns(engine))     # новых колонок ещё нет

    await init_db(engine)                          # доращиваем
    assert _NEW <= await _columns(engine)
    await init_db(engine)                          # идемпотентно: второй раз без ошибок
    assert _NEW <= await _columns(engine)

    # запись с расходом реально пишется и читается
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as s:
        s.add(models.User(gateway_user_id="u1", email=""))
        await s.flush()
        s.add(models.Job(session_id="x", user_id=1, mode="htmlnew", kind="html",
                         status="done", in_tokens=12_400, out_tokens=8_900,
                         cost_rub=11.96, duration_ms=167_000))
        await s.commit()
    async with Session() as s:
        job = (await s.execute(
            select(models.Job).where(models.Job.session_id == "x"))).scalar_one()
        assert job.in_tokens == 12_400 and job.out_tokens == 8_900
        assert job.cost_rub == 11.96 and job.duration_ms == 167_000
