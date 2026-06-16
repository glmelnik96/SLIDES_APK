"""Top-level Celery task: drive the LangGraph for one user job.

Runs sync. Catches SoftTimeLimitExceeded so we can emit a clean cancellation
event before the hard kill arrives.
"""
from __future__ import annotations

from typing import Any

import structlog
from celery.exceptions import SoftTimeLimitExceeded

from graph.graph import get_compiled_graph, thread_config
from schemas.session import Mode, SessionInput, SessionState
from worker import progress
from worker.celery_app import app

logger = structlog.get_logger(__name__)


@app.task(name="pipeline.run", bind=True, acks_late=True)
def run_pipeline(self, payload: dict[str, Any]) -> dict[str, Any]:
    """Entry point. `payload` is the JSON-encoded SessionInput.

    Returns a small summary dict for the result backend / debug.
    """
    inp = SessionInput.model_validate(payload)
    state = SessionState.from_input(inp)
    cfg = thread_config(state.session_id)
    log = logger.bind(session_id=state.session_id, user_id=state.user_id, task_id=self.request.id)
    log.info("pipeline.start", mode=state.mode)
    try:
        # /htmlnew не использует LangGraph: build_deck вызывается целиком,
        # чекпоинтов нет (см. worker/tasks/htmlnew.py). Ранний return до
        # компиляции графов; обработка исключений ниже — общая.
        if state.mode == Mode.HTMLNEW.value:
            from worker.tasks.htmlnew import run_htmlnew
            result = run_htmlnew(state)
            log.info("pipeline.done", final_stage=result.get("stage"))
            return result
        # /design uses the standalone from-scratch designer graph, /html the
        # HTML-render graph; everything else (verstai/audit/brief) stays on
        # the donor pipeline graph.
        if state.mode == Mode.DESIGN.value:
            from graph.designer.graph import get_compiled_designer_graph
            graph = get_compiled_designer_graph()
        elif state.mode == Mode.HTML.value:
            from graph.html.graph import get_compiled_html_graph
            graph = get_compiled_html_graph()
        else:
            graph = get_compiled_graph()
        final = graph.invoke(state.model_dump(), cfg)
        log.info("pipeline.done", final_stage=final.get("stage"))
        return {"ok": True, "session_id": state.session_id, "stage": final.get("stage")}
    except SoftTimeLimitExceeded:
        log.warning("pipeline.soft_timeout")
        progress.failed(state.session_id, error="Превышен лимит времени обработки")
        return {"ok": False, "session_id": state.session_id, "stage": "timeout"}
    except Exception as e:  # noqa: BLE001
        log.exception("pipeline.failed", error=str(e))
        progress.failed(state.session_id, error=f"Сбой: {type(e).__name__}")
        raise


@app.task(name="pipeline.resume", bind=True, acks_late=True)
def resume_pipeline(self, session_id: str, progress_message_id: int,
                    chat_id: int) -> dict[str, Any]:
    """Pick up an existing checkpoint.

    We update the state with the *new* progress_message_id (user got a fresh
    message after `/resume`) and let RedisSaver replay from the last completed
    node by invoking the graph with `input=None`.
    """
    cfg = thread_config(session_id)
    log = logger.bind(session_id=session_id, task_id=self.request.id)
    log.info("pipeline.resume.start")
    try:
        graph = get_compiled_graph()
        graph.update_state(cfg, {
            "progress_message_id": progress_message_id,
            "chat_id": chat_id,
        })
        final = graph.invoke(None, cfg)
        log.info("pipeline.resume.done", final_stage=final.get("stage"))
        return {"ok": True, "session_id": session_id, "stage": final.get("stage")}
    except SoftTimeLimitExceeded:
        log.warning("pipeline.resume.soft_timeout")
        progress.failed(session_id, error="Превышен лимит времени обработки")
        return {"ok": False, "session_id": session_id, "stage": "timeout"}
    except Exception as e:  # noqa: BLE001
        log.exception("pipeline.resume.failed", error=str(e))
        progress.failed(session_id, error=f"Сбой при возобновлении: {type(e).__name__}")
        raise
