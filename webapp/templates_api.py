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


# Representative sample text per slot name (visual preview only). Each value is
# short enough to fit the SMALLEST max_chars among all slots sharing that name,
# so the preview never truncates mid-word (the old "num" default surfaced as a
# clipped "Текс"). Keys mirror the live slot vocabulary in library.json.
_SAMPLE = {
    "title": "Заголовок слайда", "subtitle": "Короткий подзаголовок",
    "highlight": "Главный вывод секции",
    "heading": "Пункт", "text": "Короткое описание пункта",
    "body": "Короткое описание пункта или блок текста.",
    "label": "Метрика", "caption": "пояснение к метрике",
    "unit": "клиентов", "desc": "активных пользователей", "number": "01",
    # service-table
    "service_name": "Cloud Compute", "service_desc": "краткое описание",
    "num": "01", "benefit": "Преимущество сервиса",
    # before-after
    "before_label": "Было", "after_label": "Стало",
    "before_text": "Ручные операции и разрозненные данные.",
    "after_text": "Единая платформа и автоматизация.",
    "delta": "−40% времени",
    # stacked-bar segments — sum ≈ 100 per bar
    "v1": "40", "v2": "30", "v3": "20", "v4": "10",
}

# Slots whose default lives in the template itself (rendered via `content.x or …`).
# Preview leaves them empty so the master's own default is the single source of truth.
_TEMPLATE_OWNED = {"image", "lead"}

# Chart value-labels: leave empty so each chart falls back to its numeric `value`
# (or its own computed %). Keeps the printed label consistent with the geometry
# instead of showing an unrelated placeholder string.
_FALLBACK_EMPTY = {"display"}

# Descending magnitudes so bar/line/donut/rings previews read as real data rather
# than a flat block (equal values render every bar full-width / the line dead
# straight). Indexed by item position; all ≤100 so kpi-rings (clamped 0..100)
# stays valid.
_SERIES = (86, 72, 63, 54, 45, 37, 28, 20)


def _sample_value(name: str, spec: SlotSpec, idx: int = 0):
    if spec.kind == "text":
        if name in _TEMPLATE_OWNED or name in _FALLBACK_EMPTY:
            return ""  # let the template supply its own default / computed fallback
        if name == "value":
            return str(_SERIES[idx % len(_SERIES)])  # vary per item → non-flat charts
        s = _SAMPLE.get(name, "Текст")
        return s[: spec.max_chars] if spec.max_chars else s
    if spec.kind == "list":
        # stats2 — второй ряд stats-row, необязательный: в превью пикера показываем
        # только первый ряд, чтобы карточка мастера читалась как классический один ряд.
        if name == "stats2":
            return []
        n = min(spec.max_items or 3, 4) or 3
        return [_sample_group(spec, i) for i in range(n)]
    if spec.kind == "group":
        return _sample_group(spec, idx)
    return ""


def _sample_group(spec: SlotSpec, idx: int = 0) -> dict:
    if spec.item_slots:
        return {n: _sample_value(n, s, idx) for n, s in spec.item_slots.items()}
    return {}


def sample_content(template_id: str) -> dict:
    """Representative content for a template, to render a visual preview."""
    spec = TemplateLibrary.load().get(template_id)
    return {n: _sample_value(n, s) for n, s in spec.slots.items()}


def sample_slot(name: str, spec: SlotSpec, idx: int = 0):
    """Representative default for ONE slot (text/list/group). Shared by the picker
    preview and the draft renderer: an empty slot renders this filler so a freshly
    applied master shows example text instead of «…», while its input field stays
    empty (plan.json keeps the user's raw content). Template-owned slots (image/
    lead) sample to "" and fall back to the template's own default."""
    return _sample_value(name, spec, idx)
