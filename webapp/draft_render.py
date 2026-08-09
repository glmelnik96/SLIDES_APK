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

from webapp import deck_edit, slide_types, templates_api
from webapp.draft import DraftPlan

_PLACEHOLDER = "…"
# Empty-state slide shown when a draft has no slides yet (valid cover content).
_EMPTY_TITLE = "Новая презентация"
_EMPTY_SUBTITLE = "Добавьте слайд или попросите ассистента в чате"


def build_draft_html(plan: DraftPlan) -> str:
    """Собрать HTML деки из черновика, ничего не записывая на диск.

    Отдельно от записи, чтобы вызывающий мог убедиться, что дека собирается,
    ДО того как сохранит план: иначе при поломке сборки план уезжает вперёд
    деривата и они расходятся навсегда (см. _persist_draft в app.py)."""
    theme = plan.theme if plan.theme in deck_edit.THEMES else "dark"
    return assemble(_to_deck_plan(plan), theme=theme)


def render_draft(session_id: str, plan: DraftPlan) -> Path:
    """Render the draft to deck.html (derived artifact) and return its path."""
    return deck_edit.save_deck(session_id, build_draft_html(plan))


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
        if s.slide_type:
            tid, content = slide_types.map_typed(s.slide_type, s.fields or {})
            if tid:
                spec = library.get(tid)
                slides.append(SlidePlan(
                    index=i, type=spec.type, template_id=tid,
                    content=_safe_content(library, tid, content)))
                continue
            # invalid typed fields → fall through to the raw path below
        tid = s.template_id or "blank"
        spec = library.get(tid)
        slides.append(SlidePlan(index=i, type=spec.type, template_id=tid,
                                content=_safe_content(library, tid, s.content)))
    return DeckPlan(title=plan.title, slides=slides)


def _safe_content(library: TemplateLibrary, template_id: str,
                  content: dict) -> dict:
    """Coerce content so it passes the slot contract (so assemble won't raise).
    Only the template's known slots are kept; empty slots get representative filler
    (so a freshly applied master shows example text, not «…»); over-limit
    text/lists are clamped."""
    slots = library.get(template_id).slots
    return {name: _coerce_slot(spec, content.get(name), name)
            for name, spec in slots.items()}


def _coerce_slot(spec: SlotSpec, value, name: str, idx: int = 0):
    if spec.kind == "text":
        if value in (None, "", []):
            # Empty slot → representative filler from the SAME source the picker
            # preview uses, so the input field can stay empty while the slide still
            # shows an example. Template-owned slots (image/lead) sample to "" and
            # let the template's own `content.x or default` supply the visual.
            s = templates_api.sample_slot(name, spec, idx)
            if s:
                return s[:spec.max_chars] if spec.max_chars else s
            return _PLACEHOLDER if spec.required else ""
        text = str(value)
        if spec.max_chars and len(text) > spec.max_chars:
            text = text[:spec.max_chars]
        return text
    if spec.kind == "list":
        items = list(value) if isinstance(value, (list, tuple)) else []
        if not items and spec.required:
            # Whole list empty → rich multi-item sample (descending values), so an
            # untouched chart/list master previews like the picker, not one bar.
            sample = templates_api.sample_slot(name, spec)
            items = list(sample) if isinstance(sample, list) else [{}]
        if spec.max_items:
            items = items[:spec.max_items]
        return [_coerce_group(spec, it if isinstance(it, dict) else {}, i)
                for i, it in enumerate(items)]
    if spec.kind == "group":
        return _coerce_group(spec, value if isinstance(value, dict) else {})
    return value if value is not None else ""


def _coerce_group(spec: SlotSpec, item: dict, idx: int = 0) -> dict:
    # Respect intentionally-empty OPTIONAL fields: once the user has started
    # filling this item (any field non-empty), a blank optional sub-slot renders
    # as "" (dropped by the template's `{% if s.x %}`) instead of getting example
    # filler. A wholly-untouched item still samples filler so a freshly applied
    # master previews populated (picker parity). Required-empty always coerces via
    # _coerce_slot (placeholder / sample), so the slide never shows a broken slot.
    touched = any(v not in (None, "", []) for v in item.values())
    out = {}
    for name, sub in spec.item_slots.items():
        raw = item.get(name)
        if touched and not sub.required and raw in (None, "", []):
            out[name] = ""
        else:
            out[name] = _coerce_slot(sub, raw, name, idx)
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
