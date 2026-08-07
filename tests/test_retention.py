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


async def test_purge_archives_session_before_deleting(monkeypatch, tmp_path):
    """The session dir is the only copy of what the user fed in and what came
    out; retention is the last moment it exists. With an archive dir configured,
    the purge packs it (with the job's metrics) before the rmtree."""
    import json
    import tarfile
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    archive_dir = tmp_path / "archive"
    Session = await _sm()
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    async with Session() as s:
        u = await _user(s)
        s.add(models.Job(session_id="old", user_id=u.id, mode="htmlnew",
                         kind="html", status="done", created_at=old,
                         source_filename="brief.docx", duration_ms=42000))
        await s.commit()
    d = tmp_path / "sessions" / "old"
    d.mkdir(parents=True)
    (d / "input.docx").write_bytes(b"raw")
    (d / "deck.html").write_text("<html>дека</html>", encoding="utf-8")

    assert await retention.purge_once(Session, ttl_hours=24,
                                      archive_dir=archive_dir) == 1
    assert not d.exists()                       # purge still happened
    with tarfile.open(archive_dir / "old.tar.gz") as tf:
        assert "old/deck.html" in set(tf.getnames())
    meta = json.loads((archive_dir / "old.json").read_text("utf-8"))
    assert meta["source_filename"] == "brief.docx"
    assert meta["mode"] == "htmlnew" and meta["duration_ms"] == 42000
    assert meta["created_at"].startswith(old.isoformat()[:16])


async def test_purge_deletes_even_if_archiving_fails(monkeypatch, tmp_path):
    """Archiving is best-effort: if it throws (disk full, bad path), the session
    must still be deleted — otherwise a broken archive silently fills the disk."""
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(retention.archive, "archive_session",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("full")))
    Session = await _sm()
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    async with Session() as s:
        u = await _user(s)
        s.add(models.Job(session_id="old", user_id=u.id, mode="htmlnew",
                         kind="html", status="done", created_at=old))
        await s.commit()
    d = tmp_path / "sessions" / "old"
    d.mkdir(parents=True)
    (d / "deck.html").write_text("x", encoding="utf-8")

    assert await retention.purge_once(Session, ttl_hours=24,
                                      archive_dir=tmp_path / "archive") == 1
    assert not d.exists()


async def test_purge_without_archive_dir_writes_nothing(monkeypatch, tmp_path):
    """Empty ARCHIVE_DIR = feature off (local dev, tests): no archive is made."""
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    called = []
    monkeypatch.setattr(retention.archive, "archive_session",
                        lambda *a, **kw: called.append(a))
    Session = await _sm()
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    async with Session() as s:
        u = await _user(s)
        s.add(models.Job(session_id="old", user_id=u.id, mode="htmlnew",
                         kind="html", status="done", created_at=old))
        await s.commit()
    assert await retention.purge_once(Session, ttl_hours=24) == 1
    assert called == []


async def test_purge_keeps_active_run_even_if_old(monkeypatch, tmp_path):
    """An in-flight run (status=queued) is never purged, even past the TTL —
    retention must not pull the row out from under a live build."""
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    Session = await _sm()
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    async with Session() as s:
        u = await _user(s)
        s.add(models.Job(session_id="old-done", user_id=u.id, mode="htmlnew",
                         kind="html", status="done", created_at=old))
        s.add(models.Job(session_id="old-active", user_id=u.id, mode="htmlnew",
                         kind="html", status="queued", created_at=old))
        await s.commit()
    removed = await retention.purge_once(Session, ttl_hours=24)
    assert removed == 1
    async with Session() as s:
        ids = {j.session_id for j in (await s.execute(select(models.Job))).scalars()}
    assert ids == {"old-active"}
