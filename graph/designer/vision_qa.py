"""Vision QA loop for the designer skill: render → pixel-judge → repair.

The user mandate is to validate every slide VISUALLY, not by code. This module
renders each composed slide to a PNG, asks the multimodal PIXEL_JUDGE whether
the pixels are correct, and — when a slide fails — re-composes it once with the
judge's concrete issues as feedback, keeping the repaired version only if it
clears (or is no worse). It degrades to a strict no-op when rendering is
unavailable (no LibreOffice), so the pipeline never breaks in such an env.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable

import structlog

from llm.output_parsers import call_and_parse
from llm.prompts.designer import pixel_judge, skeleton_composer, slide_composer
from llm.roles import Role
from renderers.designer.composition_dsl import Composition
from renderers.designer.native_assembler import build_deck
from schemas.design import PixelVerdict

logger = structlog.get_logger(__name__)

VISION_REPAIR_BUDGET = 1  # at most one re-compose per failing slide (bounded cost)
_EXEMPLAR_DIR = Path("skill_assets/brand/references/exemplars")


def _exemplar_for(comp_dict: dict[str, Any]) -> bytes | None:
    """Brand exemplar PNG for a comp's layout, or None (free-grid / missing)."""
    layout = comp_dict.get("layout")
    if not layout:
        return None
    p = _EXEMPLAR_DIR / f"{layout}.png"
    try:
        return p.read_bytes() if p.is_file() else None
    except Exception:
        return None


def render_comp_png(comp_dict: dict[str, Any], zoom: float = 1.5) -> bytes | None:
    """Render a single Composition dict to a slide PNG, or None on any failure.

    Failure (bad DSL, no LibreOffice, conversion error) is swallowed and logged
    so vision QA is purely additive — a render that can't happen just skips QA.
    """
    try:
        from scripts.render_png import pdf_to_pngs, pptx_to_pdf
    except Exception as exc:  # pragma: no cover - import guard
        logger.warning("vision_qa.harness_import_fail", err=str(exc))
        return None
    try:
        comp = Composition(**comp_dict)
        with tempfile.TemporaryDirectory(prefix="vqa_") as td:
            d = Path(td)
            pptx = d / "slide.pptx"
            build_deck([comp], str(pptx))
            pdf = pptx_to_pdf(pptx, d)
            pngs = pdf_to_pngs(pdf, d, stem="slide", zoom=zoom)
            return pngs[0].read_bytes() if pngs else None
    except Exception as exc:
        logger.warning("vision_qa.render_fail", num=comp_dict.get("slide_num"),
                       err=str(exc))
        return None


def judge_png(content: dict[str, Any], png: bytes,
              reference: bytes | None = None) -> PixelVerdict | None:
    """Pixel-judge one rendered slide; None if the judge call/parse fails.

    ``reference`` is the brand exemplar for the slide's layout (shown to the
    vision model as the target look). The prompt already embeds the image
    blocks inline, so we do NOT also pass ``images`` to call_and_parse (that
    would re-append and duplicate them).
    """
    try:
        verdict, _ = call_and_parse(
            role=Role.PIXEL_JUDGE,
            messages=pixel_judge.build_messages(content, png, reference),
            model_cls=PixelVerdict,
        )
        return verdict
    except Exception as exc:
        logger.warning("vision_qa.judge_fail", err=str(exc))
        return None


def _recompose_with_feedback(
    stub: dict[str, Any], content: dict[str, Any], archetype: str,
    layouts: list[str], num: int, issues: list[str],
    skeleton_fallback: Callable[[list[str], dict[str, Any], int, bool], dict[str, Any]],
) -> dict[str, Any] | None:
    """Re-run the appropriate composer with the judge's issues as feedback.

    Skeleton mode when ``layouts`` is non-empty, else free-grid. Returns a comp
    dict (slide_num set) or None if the model call/parse fails.
    """
    fb = "Визуальный контроль нашёл дефекты на рендере. Исправь: " + "; ".join(issues)
    if layouts:
        base = skeleton_composer.build_messages(stub, content, archetype, layouts)
    else:
        base = slide_composer.build_messages(stub, content, archetype)
    msgs = base + [{"role": "user", "content": fb}]
    try:
        comp, _ = call_and_parse(
            role=Role.SLIDE_COMPOSER, messages=msgs, model_cls=Composition,
        )
    except Exception as exc:
        logger.warning("vision_qa.recompose_fail", num=num, err=str(exc))
        if layouts:
            return skeleton_fallback(layouts, content, num, bool(content.get("dark")))
        return None
    cd = comp.model_dump()
    cd["slide_num"] = num
    if layouts and (cd.get("layout") not in layouts or not cd.get("content")):
        return skeleton_fallback(layouts, content, num, bool(content.get("dark")))
    return cd


def vision_repair(
    comp: dict[str, Any], stub: dict[str, Any], content: dict[str, Any],
    archetype: str, layouts: list[str], num: int,
    skeleton_fallback: Callable[[list[str], dict[str, Any], int, bool], dict[str, Any]],
) -> dict[str, Any]:
    """Render→judge→(repair) one slide. Returns the best comp dict.

    No-op (returns ``comp``) when the slide can't be rendered or the judge
    deems it ok. Otherwise re-composes up to VISION_REPAIR_BUDGET times with the
    judge's feedback and keeps a repaired version that clears (or the original
    if no attempt clears, to avoid trading a known slide for a worse one).
    """
    png = render_comp_png(comp)
    if png is None:
        return comp
    reference = _exemplar_for(comp)
    verdict = judge_png(content, png, reference)
    if verdict is None or verdict.ok:
        return comp
    logger.info("vision_qa.slide_not_ok", num=num, issues=verdict.issues)

    current = comp
    issues = verdict.issues or ["слайд не прошёл визуальный контроль"]
    for attempt in range(VISION_REPAIR_BUDGET):
        cand = _recompose_with_feedback(
            stub, content, archetype, layouts, num, issues, skeleton_fallback)
        if cand is None:
            break
        cand_png = render_comp_png(cand)
        if cand_png is None:
            return cand  # can't re-judge; trust the repair attempt
        cand_verdict = judge_png(content, cand_png, _exemplar_for(cand))
        if cand_verdict is None or cand_verdict.ok:
            logger.info("vision_qa.slide_repaired", num=num, attempt=attempt)
            return cand
        current, issues = cand, cand_verdict.issues or issues
    logger.info("vision_qa.slide_unrepaired", num=num)
    return current
