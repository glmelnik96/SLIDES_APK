"""Progress channel — emits ProgressEvent updates from the sync engine.

The engine (LangGraph nodes / htmlslides build) calls ``stage``/``done``/
``failed``/``cancelled`` here. ``publish`` is the single sink for those events;
the web app's JobRunner replaces it at startup with a routing sink that fans
events out to the right per-session queue (see ``webapp.runner``).

Keep this module *sync* — it's imported directly into the engine call path.
"""
from __future__ import annotations

from typing import Callable

from schemas.session import ProgressEvent, Stage


class Cancelled(Exception):
    """Raised inside the sync build to abort a run the user asked to stop.

    The pipeline never imports the web layer, so it doesn't know about
    ``webapp.runner.JobCancelled``. Instead it calls the ``check_cancel`` hook
    (built in ``worker.tasks.htmlnew``) at safe checkpoints; that hook raises this
    when ``is_cancelled`` reports a stop. ``webapp.runner.work()`` also treats any
    error raised while a stop is pending as a clean cancel, so this unwinds the
    build to a terminal ``cancelled`` event.
    """


# Predicate installed by the runtime (``webapp.runner`` at sink setup) so the sync
# build loop can ask "was this session asked to stop?" at a checkpoint and bail
# before launching a new LLM call — the fast half of variant-A cancellation.
_cancel_check: Callable[[str], bool] = lambda session_id: False


def set_cancel_check(fn: Callable[[str], bool]) -> None:
    """Install the stop predicate (see ``_cancel_check``)."""
    global _cancel_check
    _cancel_check = fn


def is_cancelled(session_id: str) -> bool:
    """Has a stop been requested for this session? Drives the build checkpoint."""
    return bool(_cancel_check(session_id))


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


def done(
    session_id: str,
    detail: str = "",
    result_path: str | None = None,
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    cost_rub: float | None = None,
) -> None:
    """Терминальный DONE. Расход прогона (токены/стоимость) — опционально:
    движок считает его из ``client.usage_total`` и прайса. duration_ms сюда не
    кладём — его выставляет раннер (единая точка для done/failed/cancelled)."""
    publish(ProgressEvent(
        session_id=session_id,
        stage=Stage.DONE.value,
        progress_pct=100,
        detail=detail,
        terminal=True,
        result_path=result_path,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_rub=cost_rub,
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
