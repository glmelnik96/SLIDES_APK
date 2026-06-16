"""Progress channel — emits ProgressEvent updates from the sync engine.

The engine (LangGraph nodes / htmlslides build) calls ``stage``/``done``/
``failed``/``cancelled`` here. ``publish`` is the single sink for those events;
the web app's JobRunner replaces it at startup with a routing sink that fans
events out to the right per-session queue (see ``webapp.runner``).

Keep this module *sync* — it's imported directly into the engine call path.
"""
from __future__ import annotations

from schemas.session import ProgressEvent, Stage


def publish(event: ProgressEvent) -> None:
    """Default sink — a no-op. The runtime (``webapp.runner``) overrides this
    with its routing sink before any job starts; if a stray event is emitted
    without a sink installed, drop it silently rather than crash the engine.
    """


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
