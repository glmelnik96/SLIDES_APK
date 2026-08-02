"""Обогащение usage_events.meta разрезами (webapp.usage.log_render).

`usage_events` — единственное долгоживущее хранилище метрик: `jobs` чистится
ретеншеном через 24 ч. Всё, что не попало в `meta` на терминальном событии,
теряется безвозвратно — поэтому разрезы пинятся тестами.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from webapp import usage
from webapp.db import models
from webapp.db.database import init_db, make_engine, make_sessionmaker


@pytest.fixture()
def sm(tmp_path):
    async def _mk():
        engine = make_engine(f"sqlite+aiosqlite:///{tmp_path / 'u.db'}")
        await init_db(engine)
        return make_sessionmaker(engine)
    loop = asyncio.new_event_loop()
    try:
        yield loop, loop.run_until_complete(_mk())
    finally:
        loop.close()


def _run(sm, coro_fn):
    loop, maker = sm
    return loop.run_until_complete(coro_fn(maker))


async def _seed_job(maker, **job_kwargs):
    async with maker() as s:
        user = models.User(gateway_user_id="gw1", email="a@b.c")
        s.add(user)
        await s.flush()
        s.add(models.Job(session_id="s1", user_id=user.id, mode="rebrand",
                         kind="pptx", status="done", **job_kwargs))
        await s.commit()
        return user.id


async def _meta(maker):
    async with maker() as s:
        ev = (await s.execute(select(models.UsageEvent))).scalars().one()
        return ev.meta


def test_meta_carries_job_slices(sm):
    """mode/kind/токены/₽ живут в jobs (24 ч) — копируем их в долгую таблицу."""
    async def go(maker):
        uid = await _seed_job(maker, in_tokens=1200, out_tokens=340,
                              cost_rub=1.75)
        async with maker() as s:
            await usage.log_render(
                s, session_id="s1", owner_user_id=uid, status="done",
                workflow="rebrand",
                started_at=datetime.now(timezone.utc), result_path=None,
                section_count=12, truncated=False, error_code=None)
            await s.commit()
        return await _meta(maker)

    meta = _run(sm, go)
    assert meta["mode"] == "rebrand"
    assert meta["kind"] == "pptx"
    assert meta["in_tokens"] == 1200
    assert meta["out_tokens"] == 340
    assert meta["cost_rub"] == 1.75
    assert meta["section_count"] == 12


def test_empty_slices_are_omitted_not_nulled(sm):
    """Пустые ключи опускаем: старые строки без них валидны, а агрегатор
    трактует отсутствие как «неизвестно» (а не как ноль)."""
    async def go(maker):
        uid = await _seed_job(maker)
        async with maker() as s:
            await usage.log_render(
                s, session_id="s1", owner_user_id=uid, status="done",
                workflow="rebrand",
                started_at=datetime.now(timezone.utc), result_path=None)
            await s.commit()
        return await _meta(maker)

    meta = _run(sm, go)
    assert "in_tokens" not in meta
    assert "cost_rub" not in meta
    assert "section_count" not in meta
    assert "error_code" not in meta
    assert "truncated" not in meta      # False = «не обрезали», ключ не нужен


def test_truncated_flag_recorded(sm):
    """«done с оговоркой» честнее чистого done — флаг обрезки по потолку."""
    async def go(maker):
        uid = await _seed_job(maker)
        async with maker() as s:
            await usage.log_render(
                s, session_id="s1", owner_user_id=uid, status="done",
                workflow="htmlnew",
                started_at=datetime.now(timezone.utc), result_path=None,
                truncated=True)
            await s.commit()
        return await _meta(maker)

    assert _run(sm, go)["truncated"] is True


def test_error_code_recorded_without_text(sm):
    """Наружу уходит только код класса — текст ошибки может нести фрагменты
    клиентского документа."""
    async def go(maker):
        uid = await _seed_job(maker)
        async with maker() as s:
            await usage.log_render(
                s, session_id="s1", owner_user_id=uid, status="failed",
                workflow="htmlnew",
                started_at=datetime.now(timezone.utc), result_path=None,
                error_code="provider_timeout")
            await s.commit()
        return await _meta(maker)

    meta = _run(sm, go)
    assert meta["error_code"] == "provider_timeout"
    assert "error" not in meta


def test_missing_job_row_does_not_break_logging(sm):
    """Строки jobs может не быть (отмена из очереди до записи) — событие всё
    равно должно записаться, просто без job-разрезов."""
    async def go(maker):
        async with maker() as s:
            user = models.User(gateway_user_id="gw2", email="x@y.z")
            s.add(user)
            await s.commit()
            uid = user.id
        async with maker() as s:
            await usage.log_render(
                s, session_id="nope", owner_user_id=uid, status="cancelled",
                workflow="htmlnew", started_at=None, result_path=None)
            await s.commit()
        return await _meta(maker)

    assert _run(sm, go) == {}
