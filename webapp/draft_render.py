"""Render a (possibly partial) DraftPlan into deck HTML — never raises on
incomplete content.

The engine's ``assemble`` validates every slot and raises on any violation, which
is wrong for a draft the user is still filling in. So here we *coerce* each slide's
content to a guaranteed-valid shape before assembling: empty required slots get a
placeholder, over-long text is clamped, over-long lists are trimmed, and unknown
keys are dropped. The stored draft (plan.json) keeps the user's raw content intact;
this coercion only affects the rendered preview.
"""
from __future__ import annotations

from pathlib import Path

from htmlslides.assembler import assemble
from htmlslides.library import SlotSpec, TemplateLibrary
from htmlslides.models import DeckPlan, SlidePlan

from webapp import deck_edit
from webapp.draft import DraftPlan

_PLACEHOLDER = "…"
# Empty-state slide shown when a draft has no slides yet (valid cover content).
_EMPTY_TITLE = "Новая презентация"
_EMPTY_SUBTITLE = "Добавьте слайд или попросите ассистента в чате"


def render_draft(session_id: str, plan: DraftPlan) -> Path:
    """Render the draft to deck.html (derived artifact) and return its path."""
    html = assemble(_to_deck_plan(plan))
    return deck_edit.save_deck(session_id, html)


def _to_deck_plan(plan: DraftPlan) -> DeckPlan:
    library = TemplateLibrary.load()
    if not plan.slides:
        return DeckPlan(title=plan.title or _EMPTY_TITLE, slides=[
            SlidePlan(index=1, type="title", template_id="cover",
                      content={"title": _EMPTY_TITLE, "subtitle": _EMPTY_SUBTITLE})])
    slides: list[SlidePlan] = []
    for i, s in enumerate(plan.slides, start=1):
        if s.freeform:
            slides.append(SlidePlan(index=i, type="content", freeform=True,
                                    content={"html": str(s.content.get("html", ""))}))
            continue
        tid = s.template_id or "blank"
        spec = library.get(tid)
        slides.append(SlidePlan(index=i, type=spec.type, template_id=tid,
                                content=_safe_content(library, tid, s.content)))
    return DeckPlan(title=plan.title, slides=slides)


def _safe_content(library: TemplateLibrary, template_id: str,
                  content: dict) -> dict:
    """Coerce content so it passes the slot contract (so assemble won't raise).
    Only the template's known slots are kept; required-empty slots get a
    placeholder; over-limit text/lists are clamped."""
    slots = library.get(template_id).slots
    return {name: _coerce_slot(spec, content.get(name))
            for name, spec in slots.items()}


def _coerce_slot(spec: SlotSpec, value):
    if spec.kind == "text":
        if value in (None, "", []):
            return _PLACEHOLDER if spec.required else ""
        text = str(value)
        if spec.max_chars and len(text) > spec.max_chars:
            text = text[:spec.max_chars]
        return text
    if spec.kind == "list":
        items = list(value) if isinstance(value, (list, tuple)) else []
        if not items and spec.required:
            items = [{}]
        if spec.max_items:
            items = items[:spec.max_items]
        return [_coerce_group(spec, it if isinstance(it, dict) else {})
                for it in items]
    if spec.kind == "group":
        return _coerce_group(spec, value if isinstance(value, dict) else {})
    return value if value is not None else ""


def _coerce_group(spec: SlotSpec, item: dict) -> dict:
    out = {name: _coerce_slot(sub, item.get(name))
           for name, sub in spec.item_slots.items()}
    # Keep the whole group within item_max_chars (validator sums all field
    # lengths): trim text fields from the end until it fits.
    if spec.item_max_chars:
        overflow = sum(len(str(v)) for v in out.values()) - spec.item_max_chars
        for k in reversed(list(out.keys())):
            if overflow <= 0:
                break
            v = str(out[k])
            cut = min(len(v), overflow)
            out[k] = v[:len(v) - cut]
            overflow -= cut
    return out
