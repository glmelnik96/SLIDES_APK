"""Typed structured slide content (feature: content-first chat build).

A ``DraftSlide`` may carry a ``slide_type`` + ``fields``: a small, strictly typed
contract per type. ``validate_fields`` normalises raw field dicts (returning None
when they don't fit the type — the caller then keeps the slide "raw" and lets the
old LLM-fill path handle it). ``map_typed`` deterministically maps a typed slide
to an existing engine template + content dict, so rendering needs no LLM.

This module is pure: no I/O, no engine imports beyond the template *ids* it names.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError


class TitleFields(BaseModel):
    heading: str
    subtitle: str = ""


class BulletsFields(BaseModel):
    heading: str
    bullets: list[str] = Field(default_factory=list)


class StatItem(BaseModel):
    value: str = ""
    label: str = ""


class StatsFields(BaseModel):
    heading: str
    stats: list[StatItem] = Field(default_factory=list)


class TwoColFields(BaseModel):
    heading: str
    left: list[str] = Field(default_factory=list)
    right: list[str] = Field(default_factory=list)


# slide_type → (Pydantic model, engine template id)
_SPECS: dict[str, tuple[type[BaseModel], str]] = {
    "title":   (TitleFields,   "cover"),
    "bullets": (BulletsFields, "cards-6"),
    "stats":   (StatsFields,   "stats-row"),
    "two_col": (TwoColFields,  "three-col"),
}

SLIDE_TYPES = tuple(_SPECS)


def validate_fields(slide_type, raw) -> dict | None:
    """Normalise ``raw`` for ``slide_type``; None if the type is unknown or the
    fields don't satisfy the contract (missing required heading, wrong shape)."""
    spec = _SPECS.get(slide_type)
    if spec is None or not isinstance(raw, dict):
        return None
    model, _ = spec
    try:
        return model.model_validate(raw).model_dump()
    except ValidationError:
        return None


def _clean(items) -> list[str]:
    return [str(x).strip() for x in items if str(x).strip()]


def map_typed(slide_type, fields) -> tuple[str | None, dict]:
    """Map a typed slide to (engine_template_id, content_dict). Returns
    (None, {}) when the type/fields are invalid — the caller falls back to the
    raw LLM-fill path. Content is left permissive; draft_render's ``_safe_content``
    clamps text/lists to the template's slot contract."""
    norm = validate_fields(slide_type, fields)
    if norm is None:
        return None, {}
    if slide_type == "title":
        return "cover", {"title": norm["heading"], "subtitle": norm["subtitle"]}
    if slide_type == "bullets":
        cards = [{"text": b} for b in _clean(norm["bullets"])]
        return "cards-6", {"title": norm["heading"], "cards": cards}
    if slide_type == "stats":
        stats = [{"value": s.get("value", ""), "label": s.get("label", "")}
                 for s in norm["stats"]]
        return "stats-row", {"title": norm["heading"], "stats": stats}
    if slide_type == "two_col":
        columns = [{"text": " • ".join(_clean(norm["left"]))},
                   {"text": " • ".join(_clean(norm["right"]))}]
        return "three-col", {"title": norm["heading"], "columns": columns}
    return None, {}
