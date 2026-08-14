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
# «Очистить историю» сносит всё, что НЕ в полёте: терминальные сборки + черновики.
# Отдельно от _TERMINAL, чтобы список истории и mark_terminal не считали draft
# терминальным. queued/running не трогаем — иначе осиротим активную сборку.
_CLEARABLE = _TERMINAL | {"draft"}


async def create(session: AsyncSession, *, session_id: str, user_id: int,
                 mode: str, kind: str, source_filename: str | None,
                 status: str = "queued",
                 exact_transfer: bool = False) -> models.Job:
    job = models.Job(session_id=session_id, user_id=user_id, mode=mode, kind=kind,
                     source_filename=source_filename, status=status,
                     exact_transfer=exact_transfer)
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
                        limit: int | None = None) -> list[models.Job]:
    """История сборок = только завершённые джобы (done/failed/cancelled). Активные
    (queued/running) сюда не попадают — они живут в «Активных сборках» (/api/jobs/
    active). Иначе ещё не собранная презентация светилась бы в истории с кнопкой
    «Открыть» (а заодно фантомный queued от 429 — он тоже терминальным не является).

    1-1 (аудит раунда 2, 2026-08-15): жёсткий limit=10 делал 11-ю+ сборку
    недостижимой из UI, хотя её файлы живы в окне ретеншена — «Открыть» и
    повторное скачивание пропадали. Рост списка ограничивает ретеншен (24 ч
    чистит строки jobs), а не срез новейших — тот же довод, что у списка
    черновиков (B-8/B-9)."""
    q = (select(models.Job).where(
            models.Job.user_id == user_id,
            models.Job.status.in_(_TERMINAL))
         .order_by(models.Job.created_at.desc()))
    if limit is not None:
        q = q.limit(limit)
    res = await session.execute(q)
    return list(res.scalars().all())


async def list_drafts_for_user(session: AsyncSession, user_id: int,
                               limit: int | None = None) -> list[models.Job]:
    """Черновики пользователя = только сессии со status="draft" (незавершённая
    работа конструктора/чата). Держатся отдельно и от истории (терминальные), и от
    активной очереди (queued/running), поэтому имеют свой список — по образцу
    list_for_user, но по статусу "draft".

    Аудит 2026-08-14 (B-8/B-9): дефолтный limit=10 резал и список на главной
    (старые «Продолжить» недостижимы), и окно подсчёта лимита стеклянной сборки
    (свежие пустые черновики вытесняли незавершённые из окна — потолок обходился).
    Рост списка ограничивает ретеншен, а не срез новейших."""
    q = (select(models.Job).where(
            models.Job.user_id == user_id,
            models.Job.status == "draft")
         .order_by(models.Job.created_at.desc()))
    if limit is not None:
        q = q.limit(limit)
    res = await session.execute(q)
    return list(res.scalars().all())


async def mark_terminal(session: AsyncSession, session_id: str, *, status: str,
                        result_path: str | None, error: str | None,
                        in_tokens: int | None = None,
                        out_tokens: int | None = None,
                        cost_rub: float | None = None,
                        duration_ms: int | None = None) -> None:
    """Флип джобы в терминальный статус + расход прогона (токены/стоимость/время).
    Поля расхода опциональны и по умолчанию None — старые вызовы (только
    status/result_path/error) остаются валидными; None не затирает осмысленным
    нулём (у исходов без LLM и старых строк расход честно неизвестен)."""
    job = (await session.execute(
        select(models.Job).where(models.Job.session_id == session_id))
    ).scalar_one_or_none()
    if job is None:
        return
    job.status = status if status in _TERMINAL else "failed"
    job.result_path = result_path
    job.error = error
    job.finished_at = datetime.now(timezone.utc)
    job.in_tokens = in_tokens
    job.out_tokens = out_tokens
    job.cost_rub = cost_rub
    job.duration_ms = duration_ms


async def delete_for_user(session: AsyncSession, user_id: int) -> list[str]:
    """Clear the user's list. Terminal builds AND drafts are removed (the feed is
    one unified list, so «Очистить историю» clears everything visible). Only an
    in-flight run (queued/running) is left intact so clearing never deletes the
    Job row out from under an active build (which would 404 its /events, /deck and
    /cancel and orphan the result). Returns the removed session_ids."""
    rows = await session.execute(
        select(models.Job.session_id).where(
            models.Job.user_id == user_id,
            models.Job.status.in_(_CLEARABLE)))
    session_ids = [r[0] for r in rows.all()]
    await session.execute(
        delete(models.Job).where(
            models.Job.user_id == user_id,
            models.Job.status.in_(_CLEARABLE)))
    return session_ids
