"""Append-only usage log (see models.UsageEvent) — App2/slides side of the shared
cross-app contract docs/.../2026-06-22-usage-events-logging.md.

One row per finished build. NO files, NO full prompt — only an anonymised metric
(workflow/status/duration + a safe meta whitelist) for "who used it, how much".
Identity comes from the gateway headers, carried on the Job's owning User. This
table is excluded from retention so usage history accumulates long-term.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from webapp.db import models

APP_NAME = "slides"


def _duration_ms(started_at: datetime | None,
                 finished_at: datetime | None = None) -> int | None:
    if started_at is None:
        return None
    end = finished_at or datetime.now(timezone.utc)
    # SQLite hands back naive UTC — normalise so aware/naive subtraction can't
    # raise TypeError.
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max(0, int((end - started_at).total_seconds() * 1000))


def _slides_count(result_path: str | None) -> int | None:
    """Count slides in the finished deck (safe metric — no content stored).
    Only meaningful on a successful build with a real file on disk."""
    if not result_path:
        return None
    p = Path(result_path)
    if not p.is_file():
        return None
    try:
        from webapp import deck_edit
        return deck_edit.count_slides(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — metric is best-effort, never fatal
        return None


async def log_render(session: AsyncSession, *, owner_user_id: int | None,
                     status: str, workflow: str | None,
                     started_at: datetime | None,
                     result_path: str | None) -> None:
    """Stage a usage row in the caller's session (the caller commits). Identity is
    resolved from the owning User; meta carries only the slide count."""
    user = (await session.get(models.User, owner_user_id)
            if owner_user_id is not None else None)
    meta: dict[str, object] = {}
    n = _slides_count(result_path)
    if n is not None:
        meta["slides_count"] = n
    session.add(models.UsageEvent(
        app=APP_NAME,
        gateway_user_id=(user.gateway_user_id if user else None),
        email=(user.email if user else ""),
        event="render",
        workflow=workflow,
        status=status,
        duration_ms=_duration_ms(started_at),
        meta=meta,
    ))
