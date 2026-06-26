"""Expose the slide-template catalog as JSON for the manual builder UI.

Drives the template picker and auto-generates per-slot input forms. Mirrors the
engine's TemplateLibrary slot contract (kind / required / max_chars / max_items /
item_slots) so the frontend can render and pre-validate the right fields.
"""
from __future__ import annotations

from htmlslides.library import SlotSpec, TemplateLibrary

# Section dividers / back-cover aren't user-fillable content slides.
_HIDDEN = {"section-dots", "section-frame", "back-cover"}


def _slot_dict(spec: SlotSpec) -> dict:
    out: dict = {"kind": spec.kind, "required": spec.required}
    if spec.max_chars:
        out["max_chars"] = spec.max_chars
    if spec.max_items:
        out["max_items"] = spec.max_items
    if spec.item_max_chars:
        out["item_max_chars"] = spec.item_max_chars
    if spec.item_slots:
        out["item_slots"] = {n: _slot_dict(s) for n, s in spec.item_slots.items()}
    return out


def catalog() -> list[dict]:
    """All user-fillable templates with their slot contracts, grouped by type."""
    lib = TemplateLibrary.load()
    out = []
    for t in lib.templates:
        if t.id in _HIDDEN:
            continue
        out.append({
            "id": t.id,
            "type": t.type,
            "intent": t.intent,
            "slots": {n: _slot_dict(s) for n, s in t.slots.items()},
        })
    return out


# Representative sample text per common slot name (for the visual preview only).
_SAMPLE = {
    "title": "Заголовок слайда", "subtitle": "Короткий подзаголовок",
    "heading": "Пункт", "text": "Короткое описание пункта",
    "label": "Метрика", "value": "99%", "caption": "пояснение",
    "highlight": "Главное", "accent": "NEW", "name": "Сервис",
    "left": "Было", "right": "Стало", "before": "Раньше", "after": "Теперь",
    "stat": "85%", "year": "2025", "period": "Q1",
}


def _sample_value(name: str, spec: SlotSpec):
    if spec.kind == "text":
        s = _SAMPLE.get(name, "Текст")
        return s[: spec.max_chars] if spec.max_chars else s
    if spec.kind == "list":
        n = min(spec.max_items or 3, 4) or 3
        return [_sample_group(spec) for _ in range(n)]
    if spec.kind == "group":
        return _sample_group(spec)
    return ""


def _sample_group(spec: SlotSpec) -> dict:
    if spec.item_slots:
        return {n: _sample_value(n, s) for n, s in spec.item_slots.items()}
    return {}


def sample_content(template_id: str) -> dict:
    """Representative content for a template, to render a visual preview."""
    spec = TemplateLibrary.load().get(template_id)
    return {n: _sample_value(n, s) for n, s in spec.slots.items()}
