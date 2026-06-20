"""Reconcile-on-restart + retention purge."""
import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from webapp import retention
from webapp.db.database import init_db
from webapp.db import models


async def _sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _user(s):
    u = models.User(gateway_user_id="u1", email="")
    s.add(u)
    await s.flush()
    return u


async def test_reconcile_flips_nonterminal_to_failed():
    Session = await _sm()
    async with Session() as s:
        u = await _user(s)
        s.add(models.Job(session_id="a", user_id=u.id, mode="htmlnew",
                         kind="html", status="running"))
        s.add(models.Job(session_id="b", user_id=u.id, mode="htmlnew",
                         kind="html", status="done"))
        await s.commit()
    n = await retention.reconcile_interrupted(Session)
    assert n == 1
    async with Session() as s:
        rows = {j.session_id: j.status for j in
                (await s.execute(select(models.Job))).scalars()}
    assert rows == {"a": "failed", "b": "done"}


async def test_purge_removes_old_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    Session = await _sm()
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    async with Session() as s:
        u = await _user(s)
        s.add(models.Job(session_id="old", user_id=u.id, mode="htmlnew",
                         kind="html", status="done", created_at=old))
        s.add(models.Job(session_id="new", user_id=u.id, mode="htmlnew",
                         kind="html", status="done"))
        await s.commit()
    removed = await retention.purge_once(Session, ttl_hours=24)
    assert removed == 1
    async with Session() as s:
        ids = {j.session_id for j in (await s.execute(select(models.Job))).scalars()}
    assert ids == {"new"}
