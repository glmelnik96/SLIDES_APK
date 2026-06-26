"""DB access for Job rows: ownership scoping + history persistence.

A Job row is created at submit (mapping session_id → owning user) and flipped to a
terminal status when the build finishes. Ownership lookups (get_owned) are how every
per-job endpoint refuses access to another user's session.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from webapp.db import models

_TERMINAL = {"done", "failed", "cancelled"}


async def create(session: AsyncSession, *, session_id: str, user_id: int,
                 mode: str, kind: str, source_filename: str | None,
                 status: str = "queued") -> models.Job:
    job = models.Job(session_id=session_id, user_id=user_id, mode=mode, kind=kind,
                     source_filename=source_filename, status=status)
    session.add(job)
    await session.flush()
    return job


async def get_owned(session: AsyncSession, session_id: str,
                    user_id: int) -> models.Job | None:
    res = await session.execute(
        select(models.Job).where(
            models.Job.session_id == session_id,
            models.Job.user_id == user_id))
    return res.scalar_one_or_none()


async def list_for_user(session: AsyncSession, user_id: int,
                        limit: int = 10) -> list[models.Job]:
    """История сборок = только завершённые джобы (done/failed/cancelled). Активные
    (queued/running) сюда не попадают — они живут в «Активных сборках» (/api/jobs/
    active). Иначе ещё не собранная презентация светилась бы в истории с кнопкой
    «Открыть» (а заодно фантомный queued от 429 — он тоже терминальным не является)."""
    res = await session.execute(
        select(models.Job).where(
            models.Job.user_id == user_id,
            models.Job.status.in_(_TERMINAL))
        .order_by(models.Job.created_at.desc()).limit(limit))
    return list(res.scalars().all())


async def mark_terminal(session: AsyncSession, session_id: str, *, status: str,
                        result_path: str | None, error: str | None) -> None:
    job = (await session.execute(
        select(models.Job).where(models.Job.session_id == session_id))
    ).scalar_one_or_none()
    if job is None:
        return
    job.status = status if status in _TERMINAL else "failed"
    job.result_path = result_path
    job.error = error
    job.finished_at = datetime.now(timezone.utc)


async def delete_for_user(session: AsyncSession, user_id: int) -> list[str]:
    """Clear the user's history. Only TERMINAL jobs are removed — an in-flight
    run (queued/running) is left intact so clearing history never deletes the Job
    row out from under an active build (which would 404 its /events, /deck and
    /cancel and orphan the result). Returns the removed session_ids."""
    rows = await session.execute(
        select(models.Job.session_id).where(
            models.Job.user_id == user_id,
            models.Job.status.in_(_TERMINAL)))
    session_ids = [r[0] for r in rows.all()]
    await session.execute(
        delete(models.Job).where(
            models.Job.user_id == user_id,
            models.Job.status.in_(_TERMINAL)))
    return session_ids
