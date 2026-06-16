"""Deterministic per-slide archetype planner for the designer skill.

Maps a classified slide (Agent 02 output) to one of the designer archetypes
and extracts the content payload the slide_composer needs. No LLM here — the
art direction is already locked; this is a pure routing function.
"""
from __future__ import annotations

import re
from typing import Any

# Designer archetypes (see spec §5).
ARCHETYPES = (
    "cover", "data-chart", "kpi", "diagram-flow", "comparison", "table",
    "timeline", "team", "section-divider", "title-body",
)

# Source-brief page-reference citations — «(стр. 7)», «(стр. 12-14)», «(стр.7)»,
# «(с. 5)» — are document-internal cross-refs, meaningless on a slide. Strip
# them deterministically (mirrors main-repo commit 5280aa3). Conservative: only
# the «стр.»/«с.» page-ref shape, never other parenthesised content.
_PAGE_REF_PATTERN = re.compile(
    r"\s*\(\s*(?:стр|с)\.?\s*\d+(?:\s*[,–—-]\s*\d+)*\s*\)",
    flags=re.UNICODE | re.IGNORECASE,
)

# Truncated page-ref tail: an UNCLOSED «(стр.» / «(с. 12» at end of string.
# Happens when an upstream model output was cut mid-citation (observed live
# 2026-06-09: «кибербезопасность (стр.» leaked onto a slide). End-anchored so
# legitimate parenthesised prose is never touched.
_PAGE_REF_TAIL_PATTERN = re.compile(
    r"\s*\(\s*(?:стр|с)\.?\s*\d*(?:\s*[,–—-]\s*\d*)*\s*$",
    flags=re.UNICODE | re.IGNORECASE,
)


def _strip_pageref(s: str) -> str:
    """Remove parenthesised «(стр. N[-M])» page-ref fragments — including an
    unclosed, truncated «(стр.» tail — and tidy the whitespace they leave
    behind. Returns the input unchanged when there are no matches so
    well-formed strings aren't churned."""
    if not s:
        return s
    if not (_PAGE_REF_PATTERN.search(s) or _PAGE_REF_TAIL_PATTERN.search(s)):
        return s
    cleaned = _PAGE_REF_PATTERN.sub("", s)
    cleaned = _PAGE_REF_TAIL_PATTERN.sub("", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _strip_pageref_deep(value: Any) -> Any:
    """Recursively strip page-refs from every string inside a nested structure
    (dict / list / scalar). Native data blocks (kpi/chart/table/flow/image) are
    copied verbatim from the classifier into the composer payload and rendered
    text-is-sacred, so any page-ref in a cell/label/heading would leak onto the
    slide. Mirrors the conservative, no-churn behaviour of ``_strip_pageref``:
    only strings matching the «стр.»/«с.» shape change; everything else (numbers,
    bools, keys, unmatched strings) is returned untouched."""
    if isinstance(value, str):
        return _strip_pageref(value)
    if isinstance(value, dict):
        return {k: _strip_pageref_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_pageref_deep(v) for v in value]
    return value


# Archetype → candidate skeleton layout(s). When an archetype maps to one or
# more skeletons the composer picks among them and fills the skeleton's content
# dict (the skeleton owns layout — see renderers.designer.layouts). Archetypes
# absent here have no skeleton and fall back to the free-grid block composer.
_LAYOUT_CANDIDATES: dict[str, tuple[str, ...]] = {
    "section-divider": ("section_divider",),
    "title-body": ("bullet_list", "points_3", "points_4", "points_6", "points_8"),
    "data-chart": ("chart_columns",),
    "table": ("table_zebra",),
    "timeline": ("roadmap_timeline",),
}


def layout_options(archetype: str, content: dict[str, Any]) -> list[str]:
    """Candidate skeleton layouts for an archetype, or [] for free-grid mode.

    ``cover`` resolves to a single tone-appropriate cover skeleton; everything
    else reads from ``_LAYOUT_CANDIDATES``. The composer chooses one candidate
    and fills its content dict.
    """
    if archetype == "cover":
        return ["cover_dark"] if content.get("dark") else ["cover_green"]
    return list(_LAYOUT_CANDIDATES.get(archetype, ()))


def archetype_for(cls: dict[str, Any], is_first: bool) -> str:
    """Pick an archetype for one classified slide."""
    slide_type = cls.get("slide_type")
    category = cls.get("category", "other")

    if slide_type == "kpi_native":
        return "kpi"
    if slide_type in ("chart_native", "chart_pptx_native"):
        return "data-chart"
    if slide_type == "flow_diagram_native":
        return "diagram-flow"
    if slide_type == "table_native":
        return "table"

    if is_first or category == "title":
        return "cover" if is_first else "section-divider"
    if category == "divider":
        return "section-divider"
    if category == "team":
        return "team"
    if category == "timeline":
        return "timeline"
    if category == "multicolumn":
        return "comparison"
    if category in ("kpi", "callout"):
        return "kpi"
    return "title-body"


def slide_content_for(cls: dict[str, Any], brief_slide: dict[str, Any] | None) -> dict[str, Any]:
    """Assemble the content payload handed to the composer for one slide.

    Pulls native data blocks (kpi/chart/table/flow) when present, plus the
    raw title/body text from the brief slide. Text is passed through verbatim
    (text-is-sacred) — the composer may re-layout but not paraphrase.
    """
    payload: dict[str, Any] = {
        "num": cls.get("num"),
        "category": cls.get("category"),
        "slide_type": cls.get("slide_type"),
        "subcategory_hint": cls.get("subcategory_hint", ""),
        "dark": cls.get("dark", False),
    }
    for key in ("kpi", "chart", "table", "flow", "image"):
        if cls.get(key):
            # Native blocks carry text-is-sacred cell/label/heading strings
            # straight to the composer → strip page-refs from every nested str.
            payload[key] = _strip_pageref_deep(cls[key])
    if brief_slide:
        payload["title"] = _strip_pageref(brief_slide.get("raw_title") or "")
        body_items = brief_slide.get("raw_body") or []
        payload["body"] = [
            stripped
            for b in body_items
            if (stripped := _strip_pageref(str(b)).strip())
        ]
        payload["key_phrase"] = _strip_pageref(brief_slide.get("key_phrase", "") or "")
    return payload
