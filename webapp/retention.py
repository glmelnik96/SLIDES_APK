"""Startup reconcile + background retention for App2.

reconcile: the in-memory job runner is lost on restart, so any Job row left in a
non-terminal state (queued/running) is orphaned. Flip those to failed on boot.

retention: result/session data and Job rows are never cleaned otherwise. A
background loop drops session dirs and Job rows older than the TTL.

Note: only the ``jobs`` table is purged here. The append-only ``usage_events`` log
is deliberately left untouched (shared usage-logging contract) so usage history
accumulates long-term.
"""
from __future__ import annotations

import asyncio
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select, update

from webapp import archive
from webapp.db import models
from webapp.paths import session_dir

_NONTERMINAL = ("queued", "running")
_TERMINAL = ("done", "failed", "cancelled")
# Purged on the same TTL as finished builds (from created_at). A draft ("draft"
# status) is also purgeable so abandoned drafts don't accumulate forever; 24h of
# continuous editing on one draft is implausible.
_PURGEABLE = _TERMINAL + ("draft",)
_INTERVAL_SEC = 10 * 60
# Job columns copied into the archive sidecar: everything that describes the run
# (what was asked, what came out, what it cost) minus the FK to the user row —
# the archive holds raw content, so it stays as anonymous as it usefully can.
_META_COLS = (models.Job.session_id, models.Job.mode, models.Job.kind,
              models.Job.source_filename, models.Job.exact_transfer,
              models.Job.status, models.Job.error, models.Job.created_at,
              models.Job.finished_at, models.Job.in_tokens,
              models.Job.out_tokens, models.Job.cost_rub,
              models.Job.duration_ms)


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


async def purge_once(sessionmaker, *, ttl_hours: int,
                     archive_dir: Path | None = None) -> int:
    """Delete TERMINAL Job rows (and their session dirs) older than ttl_hours.
    Results are kept for the full TTL measured from job START (created_at). An
    in-flight run (queued/running) is never purged, even if it somehow predates
    the cutoff, so retention can't pull the row out from under a live build.

    With archive_dir set, each session is packed there (inputs + outputs + the
    row's metrics) right before the rmtree — the last moment that content
    exists, and the one that captures its FINAL state after any edits.
    Returns the number of rows removed."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
    cond = (models.Job.created_at < cutoff) & models.Job.status.in_(_PURGEABLE)
    async with sessionmaker() as s:
        rows = await s.execute(select(*_META_COLS).where(cond))
        records = [dict(r) for r in rows.mappings().all()]
        if records:
            await s.execute(delete(models.Job).where(cond))
            await s.commit()
    for rec in records:
        sid = rec["session_id"]
        d = session_dir(sid)
        if archive_dir is not None:
            try:
                await asyncio.to_thread(
                    archive.archive_session, archive_dir, sid, d, rec)
            except Exception:  # noqa: BLE001 — see below
                pass  # archiving must never block the delete (else disk fills)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    return len(records)


async def retention_loop(sessionmaker, *, ttl_hours: int,
                         archive_dir: Path | None = None,
                         archive_ttl_days: int = 14,
                         archive_max_bytes: int = 0) -> None:
    while True:
        await asyncio.sleep(_INTERVAL_SEC)
        try:
            await purge_once(sessionmaker, ttl_hours=ttl_hours,
                             archive_dir=archive_dir)
            if archive_dir is not None:
                await asyncio.to_thread(
                    archive.purge_archive, archive_dir,
                    ttl_days=archive_ttl_days, max_bytes=archive_max_bytes)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — retention must never crash the app
            pass
