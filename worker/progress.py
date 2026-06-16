"""Progress channel — Redis pub/sub bridge between sync worker and async bot.

Workers publish ProgressEvent JSON to channel `progress:{session_id}`.
The bot subscribes and edits the user-facing status message with debouncing.

Keep this module *sync* — it's imported into LangGraph nodes that run inside
Celery prefork tasks. The async side lives in `bot/handlers/progress.py`.
"""
from __future__ import annotations

import structlog

from schemas.session import ProgressEvent, Stage
from storage.redis_client import DB, sync_client

logger = structlog.get_logger(__name__)


def channel_name(session_id: str) -> str:
    return f"progress:{session_id}"


def publish(event: ProgressEvent) -> None:
    """Publish a single progress event. Best-effort — failures are logged
    but never propagate, so a Redis blip can't kill a slide-render task.
    """
    try:
        r = sync_client(DB.PUBSUB)
        r.publish(channel_name(event.session_id), event.model_dump_json())
    except Exception as e:  # noqa: BLE001
        logger.warning("progress.publish_failed", session_id=event.session_id, error=str(e))


def stage(session_id: str, stage_: Stage, pct: int, detail: str = "") -> None:
    """Convenience: emit a non-terminal stage update."""
    publish(ProgressEvent(
        session_id=session_id,
        stage=stage_.value,
        progress_pct=max(0, min(100, pct)),
        detail=detail,
    ))


def done(session_id: str, detail: str = "", result_path: str | None = None) -> None:
    publish(ProgressEvent(
        session_id=session_id,
        stage=Stage.DONE.value,
        progress_pct=100,
        detail=detail,
        terminal=True,
        result_path=result_path,
    ))


def failed(session_id: str, error: str) -> None:
    publish(ProgressEvent(
        session_id=session_id,
        stage=Stage.FAILED.value,
        progress_pct=0,
        terminal=True,
        error=error,
    ))


def cancelled(session_id: str) -> None:
    publish(ProgressEvent(
        session_id=session_id,
        stage=Stage.CANCELLED.value,
        progress_pct=0,
        terminal=True,
    ))
