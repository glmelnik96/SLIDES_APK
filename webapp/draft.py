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

from pathlib import Path

from pydantic import BaseModel, Field

from webapp.paths import session_dir


class DraftSlide(BaseModel):
    template_id: str | None = None
    freeform: bool = False
    content: dict = Field(default_factory=dict)
    brief: str = ""        # тема слайда в аутлайне (до сборки)
    filled: bool = False   # прогнан ли через fill_slide


class DraftPlan(BaseModel):
    title: str = ""
    slides: list[DraftSlide] = Field(default_factory=list)


def plan_path(session_id: str) -> Path:
    return session_dir(session_id) / "plan.json"


def load_plan(session_id: str) -> DraftPlan:
    """Load the draft, or an empty plan if none exists yet."""
    p = plan_path(session_id)
    if p.is_file():
        return DraftPlan.model_validate_json(p.read_text("utf-8"))
    return DraftPlan()


def save_plan(session_id: str, plan: DraftPlan) -> None:
    plan_path(session_id).write_text(
        plan.model_dump_json(), encoding="utf-8")


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
