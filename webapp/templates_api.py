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
