"""Export a Figma Design spec (JSON) for a saved deck.

Does not call Figma: the JSON is the contract for Plugin API / Cursor MCP
(`editorType=design`, 1920×1080 frames). HTML and PPTX stay the other outputs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from htmlslides.figma_spec import deck_to_figma_spec
from htmlslides.models import DeckPlan, SlidePlan
from webapp.deck_edit import get_theme

_SECTION_RE = re.compile(
    r'<section\b([^>]*\bclass="[^"]*\bslide\b[^"]*")([^>]*)>(.*?)</section>',
    re.IGNORECASE | re.DOTALL,
)
_TEMPLATE_RE = re.compile(r'\bdata-template="([^"]+)"')
_SLOT_RE = re.compile(r'data-slot="(title|label)"[^>]*>([^<]*)', re.IGNORECASE)


def _slides_from_dict(raw: dict) -> DeckPlan:
    slides: list[SlidePlan] = []
    for i, item in enumerate(raw.get("slides") or [], start=1):
        if not isinstance(item, dict):
            continue
        tid = item.get("template_id") or "blank"
        slides.append(SlidePlan(
            index=i,
            type=item.get("type") or "content",
            template_id=tid,
            freeform=bool(item.get("freeform")),
            content=item.get("content") or {},
        ))
    if not slides:
        raise ValueError("в плане нет слайдов")
    return DeckPlan(title=str(raw.get("title") or ""), slides=slides)


def _plan_from_html(html: str) -> DeckPlan:
    slides: list[SlidePlan] = []
    for i, match in enumerate(_SECTION_RE.finditer(html), start=1):
        head = match.group(1) + match.group(2)
        body = match.group(3)
        tm = _TEMPLATE_RE.search(head)
        tid = tm.group(1) if tm else "blank"
        sm = _SLOT_RE.search(body)
        title = sm.group(2).strip() if sm else ""
        slides.append(SlidePlan(
            index=i, type="content", template_id=tid,
            content={"title": title, "label": title},
        ))
    if not slides:
        raise ValueError("в deck.html нет слайдов")
    return DeckPlan(title="", slides=slides)


def _load_plan(deck_html: Path) -> DeckPlan:
    plan_path = deck_html.parent / "plan.json"
    if plan_path.is_file():
        raw = json.loads(plan_path.read_text("utf-8"))
        return _slides_from_dict(raw)
    return _plan_from_html(deck_html.read_text("utf-8"))


def export_figma(deck_html: Path, out_json: Path) -> Path:
    plan = _load_plan(deck_html)
    theme = get_theme(deck_html.read_text("utf-8")) if deck_html.is_file() else "dark"
    spec = deck_to_figma_spec(plan, theme=theme or "dark")
    out_json.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_json
