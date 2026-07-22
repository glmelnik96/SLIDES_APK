"""mark_terminal сохраняет расход прогона (токены/стоимость/время) в Job,
оставаясь совместимым со старыми вызовами (без полей расхода)."""
import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from webapp import jobs_repo
from webapp.db import models
from webapp.db.database import init_db


async def _sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _job(s, sid):
    s.add(models.User(gateway_user_id="u1", email=""))
    await s.flush()
    await jobs_repo.create(s, session_id=sid, user_id=1, mode="htmlnew",
                           kind="html", source_filename="x.pptx")


async def test_mark_terminal_stores_usage():
    Session = await _sm()
    async with Session() as s:
        await _job(s, "s1")
        await jobs_repo.mark_terminal(
            s, "s1", status="done", result_path="/tmp/x.html", error=None,
            in_tokens=12_400, out_tokens=8_900, cost_rub=11.96, duration_ms=167_000)
        await s.commit()
    async with Session() as s:
        job = await jobs_repo.get_owned(s, "s1", 1)
        assert job.in_tokens == 12_400 and job.out_tokens == 8_900
        assert job.cost_rub == 11.96 and job.duration_ms == 167_000


async def test_mark_terminal_backward_compatible_defaults():
    # Старые вызовы без полей расхода не падают; расход остаётся None.
    Session = await _sm()
    async with Session() as s:
        await _job(s, "s2")
        await jobs_repo.mark_terminal(
            s, "s2", status="failed", result_path=None, error="boom")
        await s.commit()
    async with Session() as s:
        job = await jobs_repo.get_owned(s, "s2", 1)
        assert job.status == "failed" and job.error == "boom"
        assert job.in_tokens is None and job.out_tokens is None
        assert job.cost_rub is None and job.duration_ms is None
