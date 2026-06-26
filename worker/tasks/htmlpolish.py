"""htmlpolish — пересборка черновика (manual/chat) через движок.

Берёт DraftPlan (plan.json) уже собранной вручную/в чате деки и прогоняет его
через ту же финальную фазу движка, что и загруженный документ: assemble → lint →
vision-QA → один круг autofix. Не планирует и не заполняет с нуля — улучшает то,
что пользователь уже собрал (правит переполнение/вёрстку по замечаниям QA).

Результат пишется прямо в session_dir/deck.html (источник правды редактора), а
plan.json удаляется при успехе — после пересборки дека становится HTML-as-truth
(как у загруженной), и правки идут через contenteditable, а не через формы/чат.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from schemas.session import SessionState, Stage
from worker import progress
from worker.tasks.htmlnew import map_progress

logger = structlog.get_logger(__name__)


def run_htmlpolish(state: SessionState) -> dict[str, Any]:
    """Синхронная пересборка черновика. Возвращает summary как run_pipeline."""
    from htmlslides.pipeline.build import polish_plan
    from webapp import deck_edit, draft, draft_render

    session_id = state.session_id
    log = logger.bind(session_id=session_id)

    plan = draft.load_plan(session_id)
    if not plan.slides:
        raise ValueError("черновик пуст — добавьте хотя бы один слайд перед пересборкой")

    deck_plan = draft_render._to_deck_plan(plan)  # коэрсия в валидный DeckPlan
    out = deck_edit.deck_path(session_id)         # перезаписываем deck.html на месте

    current: tuple[Stage, int] = (Stage.RENDERING, 60)

    def on_progress(message: str) -> None:
        nonlocal current
        mapped = map_progress(message)
        if mapped is not None:
            current = mapped
        progress.stage(session_id, current[0], current[1], detail=message)

    progress.stage(session_id, Stage.RENDERING, 60, detail="старт пересборки через движок")
    log.info("htmlpolish.start", slides=len(deck_plan.slides))
    result = polish_plan(deck_plan, out, vision=True, progress=on_progress)
    # Успех: дека теперь HTML-as-truth, черновик-структура больше не источник правды.
    try:
        draft.plan_path(session_id).unlink(missing_ok=True)
    except OSError:
        pass
    log.info("htmlpolish.done", result=str(result))
    progress.done(session_id, detail="пересборка завершена", result_path=str(result))
    return {"ok": True, "session_id": session_id, "stage": Stage.DONE.value}
