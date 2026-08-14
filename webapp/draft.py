"""Draft deck state — DeckPlan-as-truth for the manual + chat builders.

A draft is a structured list of slides (not raw HTML): the source of truth is
``plan.json`` per session. The rendered ``deck.html`` is derived from it (see
``draft_render``) so the existing editor / ``/deck`` / ``/png`` endpoints keep
working unchanged, while slides can be added / removed / reordered reliably as
data instead of by parsing HTML.

Draft slides are intentionally permissive: ``content`` may be empty or partial
while the user is still filling them in. ``draft_render`` is what makes any draft
safe to render; persistence here never validates.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from pydantic import BaseModel, Field

from webapp.paths import session_dir


class DraftSlide(BaseModel):
    template_id: str | None = None
    freeform: bool = False
    content: dict = Field(default_factory=dict)
    brief: str = ""              # тема слайда в аутлайне (до сборки)
    filled: bool = False         # прогнан ли через fill_slide (только для сырых)
    slide_type: str | None = None  # типизированный слайд: title|bullets|stats|two_col
    fields: dict | None = None     # структурированные поля под slide_type
    # Макет и его содержимое ДО перевода слайда в свободный режим — питает кнопку
    # «Вернуть макет». Живёт на самом слайде, а не в sessionStorage по номеру
    # позиции: номер врал после перестановки (кнопка возвращала чужой макет), не
    # переживал закрытие вкладки и не появлялся вовсе после правки через чат.
    prev_layout: dict | None = None
    # ── стеклянная сборка (glass) — все поля опциональны: старые plan.json
    # валидны без миграции. status="needs_input" — ИИ сомневается в макете:
    # степпер такой слайд пропускает, вопрос ждёт ответа в любой момент.
    status: str | None = None      # None | "needs_input"
    question: str | None = None    # вопрос ИИ пользователю (при needs_input)
    candidates: list[str] | None = None  # top-3 template_id для чипов-кандидатов


class DraftPlan(BaseModel):
    title: str = ""
    slides: list[DraftSlide] = Field(default_factory=list)
    # Тема деки (dark|light). Живёт в плане, а не только в deck.html: deck.html —
    # производный артефакт и пересобирается из плана при каждой правке формы.
    theme: str = "dark"
    # Что сборка сделала за спиной автора (пока только «документ обрезан по
    # потолку»). В плане, а не в ответе /glass/start: старт редиректит в редактор,
    # и ответ до панели не доживает. Пустая строка = сказать нечего.
    notice: str = ""
    # Хвост документа, не вошедший в деку по потолку MAX_GLASS_SLIDES: сколько
    # разделов осталось (rest) и с какого по счёту подходящего раздела их
    # продолжать (rest_from). Потеря обратима — кнопка в панели сборки заводит
    # вторую деку ровно с rest_from (glass.continue_glass). 0 = хвоста нет.
    rest: int = 0
    rest_from: int = 0


def plan_path(session_id: str) -> Path:
    return session_dir(session_id) / "plan.json"


def load_plan(session_id: str) -> DraftPlan:
    """Load the draft, or an empty plan if none exists yet."""
    p = plan_path(session_id)
    if p.is_file():
        return DraftPlan.model_validate_json(p.read_text("utf-8"))
    return DraftPlan()


def save_plan(session_id: str, plan: DraftPlan) -> None:
    """Записать план атомарно: временный файл рядом + rename.

    plan.json — единственная истина черновика, и переписывался он на месте:
    обрыв на записи (рестарт юнита, полный диск) оставлял обрезанный JSON, то
    есть потерянный черновик. Стеклянная сборка пишет план на каждом шаге, так
    что окно стало заметным. Временный файл — в том же каталоге: rename атомарен
    только внутри одной файловой системы."""
    path = plan_path(session_id)
    # Имя временного файла уникально по потоку: степпер стеклянной сборки пишет
    # из пула, редактор — из петли событий, и общий tmp они бы затирали.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(plan.model_dump_json(), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


# ── pure slide operations (index is 1-based, matching the editor/UI) ─────────
def add_slide(plan: DraftPlan, slide: DraftSlide,
              at: int | None = None) -> DraftPlan:
    """Insert a slide (1-based ``at``; append when None or out of range)."""
    slides = list(plan.slides)
    if at is None or at < 1 or at > len(slides) + 1:
        slides.append(slide)
    else:
        slides.insert(at - 1, slide)
    return plan.model_copy(update={"slides": slides})


def update_slide(plan: DraftPlan, index: int, *,
                 content: dict | None = None,
                 template_id: str | None = None) -> DraftPlan:
    slides = list(plan.slides)
    if not 1 <= index <= len(slides):
        raise IndexError(f"slide {index} out of range (1..{len(slides)})")
    s = slides[index - 1]
    updates: dict = {}
    if content is not None:
        updates["content"] = content
    if template_id is not None:
        updates["template_id"] = template_id
    slides[index - 1] = s.model_copy(update=updates)
    return plan.model_copy(update={"slides": slides})


def delete_slide(plan: DraftPlan, index: int) -> DraftPlan:
    slides = list(plan.slides)
    if not 1 <= index <= len(slides):
        raise IndexError(f"slide {index} out of range (1..{len(slides)})")
    del slides[index - 1]
    return plan.model_copy(update={"slides": slides})


def reorder(plan: DraftPlan, index: int, to: int) -> DraftPlan:
    """Move the slide at 1-based ``index`` to 1-based position ``to``."""
    slides = list(plan.slides)
    n = len(slides)
    if not (1 <= index <= n and 1 <= to <= n):
        raise IndexError(f"index/to out of range (1..{n})")
    s = slides.pop(index - 1)
    slides.insert(to - 1, s)
    return plan.model_copy(update={"slides": slides})
