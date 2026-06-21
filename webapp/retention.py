"""Startup reconcile + background retention for App2.

reconcile: the in-memory job runner is lost on restart, so any Job row left in a
non-terminal state (queued/running) is orphaned. Flip those to failed on boot.

retention: result/session data and Job rows are never cleaned otherwise. A
background loop drops session dirs and Job rows older than the TTL.
"""
from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update

from webapp.db import models
from webapp.paths import session_dir

_NONTERMINAL = ("queued", "running")
_TERMINAL = ("done", "failed", "cancelled")
_INTERVAL_SEC = 10 * 60


async def reconcile_interrupted(sessionmaker) -> int:
    """Flip orphaned queued/running Job rows to failed. Returns rows updated."""
    async with sessionmaker() as s:
        res = await s.execute(
            update(models.Job)
            .where(models.Job.status.in_(_NONTERMINAL))
            .values(status="failed", error="прервано перезапуском сервера",
                    finished_at=datetime.now(timezone.utc)))
        await s.commit()
    return res.rowcount or 0


async def purge_once(sessionmaker, *, ttl_hours: int) -> int:
    """Delete TERMINAL Job rows (and their session dirs) older than ttl_hours.
    Results are kept for the full TTL measured from job START (created_at). An
    in-flight run (queued/running) is never purged, even if it somehow predates
    the cutoff, so retention can't pull the row out from under a live build.
    Returns the number of rows removed."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    cond = (models.Job.created_at < cutoff) & models.Job.status.in_(_TERMINAL)
    async with sessionmaker() as s:
        rows = await s.execute(select(models.Job.session_id).where(cond))
        session_ids = [r[0] for r in rows.all()]
        if session_ids:
            await s.execute(delete(models.Job).where(cond))
            await s.commit()
    for sid in session_ids:
        d = session_dir(sid)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    return len(session_ids)


async def retention_loop(sessionmaker, *, ttl_hours: int) -> None:
    while True:
        await asyncio.sleep(_INTERVAL_SEC)
        try:
            await purge_once(sessionmaker, ttl_hours=ttl_hours)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — retention must never crash the app
            pass
