"""/htmlnew — сборка self-contained HTML-деки пакетом htmlslides.

Не LangGraph-узел: build_deck вызывается целиком, без чекпоинтов (при обрыве
дешевле перезапустить — сборка ~4–10 мин). Вызывается из run_pipeline по
ветке Mode.HTMLNEW; исключения наружу — терминальный failed эмитит run_pipeline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from graph.nodes.pipeline import _session_workdir
from schemas.session import SessionState, Stage
from worker import progress

logger = structlog.get_logger(__name__)

# Префиксы прогресс-сообщений build_deck → (стадия бота, грубый %).
# Источник истины по префиксам: htmlslides/pipeline/build.py (progress(...)).
_STAGE_MAP: list[tuple[str, Stage, int]] = [
    ("parse:", Stage.PARSING, 10),
    ("rebrand", Stage.PARSING, 15),       # "rebrand:" и "rebrand-скриншоты недоступны"
    ("plan:", Stage.CLASSIFYING, 25),
    ("fill:", Stage.DESIGNING, 45),
    ("assemble:", Stage.RENDERING, 65),
    ("lint:", Stage.VALIDATING, 75),
    ("vision-qa", Stage.VALIDATING, 80),  # "vision-qa:" и "vision-qa пропущен"
    ("autofix:", Stage.AUTOFIXING, 90),
    ("done:", Stage.FINALIZING, 95),
]


def map_progress(message: str) -> tuple[Stage, int] | None:
    """Сообщение build_deck → (Stage, pct); None — стадию не менять (warn и пр.)."""
    for prefix, stage_, pct in _STAGE_MAP:
        if message.startswith(prefix):
            return stage_, pct
    return None


def pick_mode(input_path: Path) -> str:
    """pptx — rebrand (планировщик видит скриншоты исходника), остальное — auto."""
    return "rebrand" if input_path.suffix.lower() == ".pptx" else "auto"


def run_htmlnew(state: SessionState) -> dict[str, Any]:
    """Синхронная сборка. Возвращает summary того же вида, что run_pipeline."""
    from htmlslides.pipeline.build import build_deck  # лениво: пакет есть только в worker

    session_id = state.session_id
    input_path = Path(state.input_s3_key)
    stem = Path(state.source_filename or input_path.name).stem or "deck"
    out = _session_workdir(session_id) / f"{stem}.html"
    log = logger.bind(session_id=session_id)

    current: tuple[Stage, int] = (Stage.PARSING, 5)

    def on_progress(message: str) -> None:
        nonlocal current
        mapped = map_progress(message)
        if mapped is not None:
            current = mapped
        progress.stage(session_id, current[0], current[1], detail=message)

    progress.stage(session_id, Stage.PARSING, 5, detail="старт сборки HTML")
    log.info("htmlnew.start", input=str(input_path), mode=pick_mode(input_path))
    result = build_deck(
        input_path,
        out,
        mode=pick_mode(input_path),
        vision=True,
        freeform_ok=True,        # включён управляемый freeform (вариант B)
        progress=on_progress,
    )
    log.info("htmlnew.done", result=str(result))
    progress.done(session_id, detail="HTML-дека готова", result_path=str(result))
    return {"ok": True, "session_id": session_id, "stage": Stage.DONE.value}
