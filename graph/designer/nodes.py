"""Designer-skill LangGraph nodes (the /design from-scratch path).

Flow: art_director (locked stub) → compose (per-slide DSL + critic gate) →
native_assemble (DSL → native .pptx). Upstream parse/brief/classify are
reused from the donor pipeline (read-only).
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import structlog

from llm.output_parsers import call_and_parse
from llm.prompts.designer import (
    art_director,
    brand_critic_v2,
    skeleton_composer,
    slide_composer,
)
from llm.roles import Role
from renderers.designer.composition_dsl import Composition
from renderers.designer.native_assembler import build_deck
from schemas.design import CriticVerdict, DesignStub
from schemas.session import SessionState, Stage
from worker import progress
from graph.designer.planner import archetype_for, layout_options, slide_content_for
from graph.designer.vision_qa import vision_repair
# Shared with the donor pipeline so the built .pptx lands in the per-session
# workdir (honours SLIDESBOT_WORKDIR) the bot reads from for send_document.
from graph.nodes.pipeline import _output_filename, _session_workdir

logger = structlog.get_logger(__name__)

COMPOSE_RETRY_BUDGET = 2  # spec §10: max 2 re-composes before fallback
# Slides are independent → compose them concurrently. Cloud.ru cap is 20 RPS,
# each slide makes 1-6 sequential calls, so 4 workers stay far below the limit.
COMPOSE_WORKERS = max(1, int(os.environ.get("DESIGNER_COMPOSE_WORKERS", "4")))


def _artefacts(state: SessionState) -> dict[str, Any]:
    return dict(state.artefacts)


def _emit(state: SessionState, stage: Stage, pct: int, detail: str) -> None:
    progress.stage(state.session_id, stage, pct=pct, detail=detail)


# ─── art_director (combined locked stub) ─────────────────────────────────────

def art_director_node(state: SessionState) -> dict[str, Any]:
    _emit(state, Stage.DESIGNING, pct=30, detail="арт-директор")
    arts = _artefacts(state)
    brief = arts.get("brief")
    if brief is None:
        raise RuntimeError("art_director_node: artefacts['brief'] missing")

    stub, _ = call_and_parse(
        role=Role.ART_DIRECTOR,
        messages=art_director.build_messages(brief),
        model_cls=DesignStub,
    )
    arts["design_stub"] = stub.model_dump()
    logger.info("node.art_director.done", session_id=state.session_id,
                tonality=stub.tonality, dark_ratio=stub.dark_ratio)
    return {"artefacts": arts, "stage": Stage.DESIGNING.value, "progress_pct": 35}


# ─── compose (per-slide DSL + critic gate) ───────────────────────────────────

def _fallback_title_body(content: dict[str, Any], num: int, dark: bool) -> dict[str, Any]:
    """Safest archetype — always renderable (spec §5 fallback)."""
    blocks: list[dict[str, Any]] = [{
        "role": "title", "text": content.get("title") or "",
        "grid": {"c": 1, "r": 1, "cs": 10, "rs": 2},
    }]
    body = content.get("body") or []
    if body:
        blocks.append({
            "role": "body", "bullets": [str(b) for b in body][:6],
            "grid": {"c": 1, "r": 3, "cs": 11, "rs": 7},
        })
    return {
        "slide_num": num,
        "tone": "dark" if dark else "light",
        "background": {"kind": "graphite" if dark else "white"},
        "blocks": blocks,
    }


def _skeleton_fallback(layouts: list[str], content: dict[str, Any], num: int,
                       dark: bool) -> dict[str, Any]:
    """Deterministic skeleton Composition when the LLM skeleton-compose fails.

    Picks the safest candidate layout and fills its content dict straight from
    the planner content (native data verbatim), so a slide always renders
    on-brand even without a live model response.
    """
    title = content.get("title") or ""
    body = [str(b) for b in (content.get("body") or []) if str(b).strip()]
    layout = layouts[0]
    if layout.startswith("cover"):
        c: dict[str, Any] = {"title": title}
        if body:
            c["subtitle"] = body[0]
    elif layout == "section_divider":
        c = {"title": title}
    elif layout == "table_zebra":
        tbl = content.get("table") or {}
        c = {"title": title or tbl.get("header") or "",
             "headers": tbl.get("headers") or [],
             "rows": tbl.get("data") or tbl.get("rows") or []}
    elif layout == "chart_columns":
        ch = content.get("chart") or {}
        series = ch.get("series") or []
        c = {"title": title or ch.get("title") or "",
             "categories": ch.get("x") or ch.get("categories") or [],
             "series": [{"name": s.get("name", ""), "values": s.get("values", [])}
                        for s in series],
             "accent_idx": int(ch.get("accent_idx", 0) or 0)}
    elif layout == "roadmap_timeline":
        c = {"title": title,
             "milestones": [{"label": str(i + 1), "text": b}
                            for i, b in enumerate(body[:6])]}
    else:  # bullet_list / points_* → bullet_list is the universally-safe shape
        layout = "bullet_list"
        c = {"title": title, "bullets": body[:8]}
    tone = "green" if layout == "cover_green" else ("dark" if dark else "light")
    bg = "green" if layout == "cover_green" else ("graphite" if dark else "white")
    return {"slide_num": num, "tone": tone, "background": {"kind": bg},
            "layout": layout, "content": c}


def _compose_skeleton(stub: dict[str, Any], content: dict[str, Any], archetype: str,
                      layouts: list[str], num: int) -> dict[str, Any]:
    """Skeleton-mode compose: LLM picks a layout + fills its content dict.

    No brand critic — the skeleton guarantees the on-brand layout; visual QA
    (Phase 4) replaces the grid-overlap critic that only made sense for free
    placement. Falls back to a deterministic skeleton on parse failure.
    """
    msgs = skeleton_composer.build_messages(stub, content, archetype, layouts)
    try:
        comp, _ = call_and_parse(
            role=Role.SLIDE_COMPOSER, messages=msgs, model_cls=Composition,
        )
    except Exception as exc:
        logger.warning("node.compose.skeleton_parse_fail", num=num, err=str(exc))
        return _skeleton_fallback(layouts, content, num, bool(content.get("dark")))
    comp_dump = comp.model_dump()
    comp_dump["slide_num"] = num
    # Guard: model must return a known candidate layout with non-empty content.
    if comp_dump.get("layout") not in layouts or not comp_dump.get("content"):
        logger.info("node.compose.skeleton_off_menu", num=num,
                    got=comp_dump.get("layout"), allowed=layouts)
        return _skeleton_fallback(layouts, content, num, bool(content.get("dark")))
    return comp_dump


def _compose_one(stub: dict[str, Any], content: dict[str, Any], archetype: str,
                 num: int, use_critic: bool = True) -> dict[str, Any]:
    messages = slide_composer.build_messages(stub, content, archetype)
    reasons: list[str] = []
    for attempt in range(COMPOSE_RETRY_BUDGET + 1):
        msgs = messages
        if reasons:
            msgs = messages + [{
                "role": "user",
                "content": "Композиция отклонена бренд-критиком. Исправь: "
                           + "; ".join(reasons),
            }]
        try:
            comp, _ = call_and_parse(
                role=Role.SLIDE_COMPOSER, messages=msgs, model_cls=Composition,
            )
        except Exception as exc:  # parse/validation failed even after retry
            logger.warning("node.compose.parse_fail", num=num, attempt=attempt, err=str(exc))
            break
        comp_dump = comp.model_dump()
        comp_dump["slide_num"] = num
        if not use_critic:
            return comp_dump
        verdict, _ = call_and_parse(
            role=Role.BRAND_CRITIC_V2,
            messages=brand_critic_v2.build_messages(stub, comp_dump),
            model_cls=CriticVerdict,
        )
        if verdict.verdict == "READY":
            return comp_dump
        reasons = verdict.reasons or ["не соответствует locked stub"]
        logger.info("node.compose.not_ready", num=num, attempt=attempt, reasons=reasons)
    return _fallback_title_body(content, num, bool(content.get("dark")))


def compose_node(state: SessionState) -> dict[str, Any]:
    _emit(state, Stage.DESIGNING, pct=55, detail="компоновка слайдов")
    arts = _artefacts(state)
    stub = arts["design_stub"]
    classification = arts.get("classification") or {}
    brief = arts.get("brief") or {}
    cls_slides = classification.get("slides") or []
    brief_by_num = {int(s.get("num", 0)): s for s in (brief.get("slides") or [])}

    use_critic = bool(arts.get("designer_use_critic", True))
    use_vision_qa = bool(arts.get("designer_vision_qa", True))

    def _compose_slide(i: int, cls: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
        """Compose one slide; returns (comp | None for phantom, is_fallback)."""
        num = int(cls.get("num") or (i + 1))
        archetype = archetype_for(cls, is_first=(i == 0))
        content = slide_content_for(cls, brief_by_num.get(num))
        has_text = bool((content.get("title") or "").strip()) or any(
            str(b).strip() for b in (content.get("body") or [])
        )
        has_native = any(content.get(k) for k in ("kpi", "chart", "table", "flow", "image"))
        if not has_text and not has_native:
            logger.info("node.compose.skip_phantom", session_id=state.session_id, num=num)
            return None, False  # phantom/empty slide — do not fabricate
        is_fallback = False
        layouts = layout_options(archetype, content)
        if layouts:
            comp = _compose_skeleton(stub, content, archetype, layouts, num)
        else:
            comp = _compose_one(stub, content, archetype, num, use_critic=use_critic)
            if not comp.get("blocks") or all(b.get("role") == "title" for b in comp["blocks"]):
                is_fallback = True
        if use_vision_qa:
            comp = vision_repair(comp, stub, content, archetype, layouts, num,
                                 _skeleton_fallback)
        return comp, is_fallback

    # Slides are independent: compose them in parallel, preserving slide order.
    if COMPOSE_WORKERS > 1 and len(cls_slides) > 1:
        with ThreadPoolExecutor(max_workers=COMPOSE_WORKERS) as pool:
            results = list(pool.map(lambda ic: _compose_slide(*ic), enumerate(cls_slides)))
    else:
        results = [_compose_slide(i, cls) for i, cls in enumerate(cls_slides)]

    comps = [comp for comp, _ in results if comp is not None]
    fallbacks = sum(1 for comp, fb in results if comp is not None and fb)

    arts["compositions"] = comps
    logger.info("node.compose.done", session_id=state.session_id,
                slides=len(comps), fallbacks=fallbacks)
    return {"artefacts": arts, "stage": Stage.DESIGNING.value, "progress_pct": 75}


# ─── native_assemble (DSL → native .pptx) ────────────────────────────────────

def native_assemble_node(state: SessionState, out_dir: str | None = None) -> dict[str, Any]:
    _emit(state, Stage.RENDERING, pct=85, detail="сборка .pptx")
    arts = _artefacts(state)
    comps = [Composition(**c) for c in (arts.get("compositions") or [])]
    # Default to the shared per-session workdir (honours SLIDESBOT_WORKDIR) so
    # the bot container can read the file for send_document; an explicit out_dir
    # (host validation scripts) overrides it.
    if out_dir:
        workdir = Path(out_dir)
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        workdir = _session_workdir(state.session_id)
    out_path = str(workdir / _output_filename(state.session_id, state.source_filename))
    build_deck(comps, out_path)
    arts["result_path"] = out_path
    logger.info("node.native_assemble.done", session_id=state.session_id,
                slides=len(comps), path=out_path)
    return {"artefacts": arts, "result_s3_key": out_path,
            "stage": Stage.FINALIZING.value, "progress_pct": 95}


# ─── finalize (terminal) ─────────────────────────────────────────────────────

def finalize_node(state: SessionState) -> dict[str, Any]:
    """Publish the terminal DONE event with the built .pptx path.

    Mirrors the donor pipeline's finalize: this is the ONLY place the designer
    graph emits a terminal progress event, which the bot's subscriber needs to
    deliver the result and release the global queue lock. Without it a /design
    job would stall the shared queue (the bot waits forever for a terminal).
    """
    arts = _artefacts(state)
    result_path = arts.get("result_path") or state.result_s3_key
    progress.done(state.session_id, detail="готово", result_path=result_path)
    logger.info("node.designer_finalize.done", session_id=state.session_id,
                has_result=bool(result_path))
    return {"stage": Stage.DONE.value, "progress_pct": 100,
            "notes": [*state.notes, "Готово"]}
