"""Worker-side nodes for the /html pipeline (Path B).

Mirrors the proven host driver ``scripts/html_deck.py`` slide loop verbatim:
per slide — snap_payload → compose → Gate 0 (deterministic verbatim title) →
Gate 1 (brand critic on HTML) → Chromium render → Gate 2 (vision pixel-judge,
keep-better repair) — then pack all PNGs full-bleed into the output .pptx.

parse/brief/classify are reused read-only from the donor pipeline (wired in
``graph.html.graph``); planner helpers come from graph.designer.planner
(read-only import, /design itself is never touched).
"""
from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import structlog

from graph.designer.planner import archetype_for, slide_content_for
from graph.nodes.pipeline import _output_filename, _session_workdir
from renderers.html.compose import compose_slide
from renderers.html.fidelity import snap_payload
from renderers.html.pack import pack_pngs
from renderers.html.qa import critic_gate, judge_slide
from renderers.html.render import SlideRenderer, load_brand_css
from schemas.session import SessionState, Stage
from worker import progress

logger = structlog.get_logger(__name__)

# One repair attempt per gate per slide (bounded cost, mirrors /design).
CRITIC_REPAIR_BUDGET = 1
VISION_REPAIR_BUDGET = 1

# Slides are independent — compose them in parallel (mirrors the validated
# /design compose fan-out). Each worker thread owns its own Chromium instance
# (Playwright sync objects are bound to their creating thread).
HTML_COMPOSE_WORKERS = int(os.environ.get("HTML_COMPOSE_WORKERS", "4"))

# Wall-clock budget for the whole compose node. Slides that *start* after the
# deadline run in fast mode (single compose + render, QA gates skipped) so the
# deck always finishes well inside Celery's soft time limit (3300s) instead of
# riding into the hard kill — observed live 2026-06-10 on a 14-slide deck
# under degraded Cloud.ru latency.
HTML_COMPOSE_BUDGET_S = float(os.environ.get("HTML_COMPOSE_BUDGET_S", "2100"))


def _artefacts(state: SessionState) -> dict[str, Any]:
    return dict(state.artefacts)


def _emit(state: SessionState, stage: Stage, pct: int, detail: str) -> None:
    progress.stage(state.session_id, stage, pct=pct, detail=detail)


def _strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _compose_one_slide(payload: dict[str, Any], brand_css: str,
                       renderer: SlideRenderer, archetype: str,
                       log: Any, fast: bool = False) -> bytes:
    """Full per-slide flow: compose + 3 quality gates. Returns the final PNG.

    ``fast=True`` (compose-node deadline exceeded): single compose + render,
    LLM QA gates skipped — a complete deck of un-judged slides beats a hard
    timeout that delivers nothing. Gate 0 (deterministic title check) stays.
    """
    body = compose_slide(payload, brand_css)

    # Gate 1: brand critic on the HTML (canon violations).
    for _ in range(CRITIC_REPAIR_BUDGET if not fast else 0):
        cv = critic_gate(body, payload)
        if cv.verdict == "READY":
            break
        log.info("html.gate1.critic_repair", reasons="; ".join(cv.reasons)[:200])
        body = compose_slide(payload, brand_css, feedback=cv.reasons)

    # Gate 0 (deterministic): slide title must appear verbatim in the HTML —
    # composers occasionally drop a letter («ВЗГЛЯД»→«ВЗГЛЯ») and the LLM
    # judges don't reliably catch it. Runs AFTER the critic gate because a
    # critic-repair recompose can itself corrupt the title (run3 s13).
    title = re.sub(r"\s+", " ", str(payload.get("title") or "")).strip()
    if title:
        for _ in range(2):
            if title.lower() in _strip_tags(body).lower():
                break
            log.info("html.gate0.title_recompose")
            body = compose_slide(payload, brand_css, feedback=[
                f"Заголовок слайда искажён или отсутствует. Выведи его ДОСЛОВНО: «{title}»",
            ])

    # Gate 2: vision pixel-judge on the render (keep-better repair).
    png = renderer.render(body)
    for _ in range(VISION_REPAIR_BUDGET if not fast else 0):
        pv = judge_slide(payload, png, archetype)
        if pv.ok:
            break
        log.info("html.gate2.judge_repair", issues="; ".join(pv.issues)[:200])
        cand_body = compose_slide(payload, brand_css, feedback=pv.issues)
        # A repair that corrupts the verbatim title is automatically worse.
        if title and title.lower() not in _strip_tags(cand_body).lower():
            log.info("html.gate2.repair_corrupted_title_kept_original")
            break
        cand_png = renderer.render(cand_body)
        cand_pv = judge_slide(payload, cand_png, archetype)
        if cand_pv.ok or len(cand_pv.issues) <= len(pv.issues):
            png = cand_png
        else:
            log.info("html.gate2.repair_worse_kept_original")
        break  # budget=1: one repair round either way
    return png


def html_compose_node(state: SessionState) -> dict[str, Any]:
    """Author + render every slide; store PNGs in artefacts (base64-free:
    written straight to the session workdir to keep checkpoints small)."""
    _emit(state, Stage.DESIGNING, pct=35, detail="HTML-вёрстка")
    arts = _artefacts(state)
    log = logger.bind(session_id=state.session_id)

    classification = arts.get("classification") or {}
    brief = arts.get("brief") or {}
    parsed_by_num = {
        int(s.get("num", 0)): s
        for s in ((arts.get("parsed_deck") or {}).get("slides") or [])
    }
    cls_slides = classification.get("slides") or []
    brief_by_num = {int(s.get("num", 0)): s for s in (brief.get("slides") or [])}

    brand_css = load_brand_css()
    workdir = _session_workdir(state.session_id)
    total = max(1, len(cls_slides))
    deadline = time.monotonic() + HTML_COMPOSE_BUDGET_S
    done_lock = threading.Lock()
    done_count = 0

    def _do_slide(i: int, cls: dict[str, Any],
                  renderer: SlideRenderer) -> tuple[int, str] | None:
        """Compose+QA one slide; returns (orig_index, png_path) or None (phantom)."""
        nonlocal done_count
        num = int(cls.get("num") or (i + 1))
        archetype = archetype_for(cls, is_first=(i == 0))
        content = slide_content_for(cls, brief_by_num.get(num))
        has_text = bool((content.get("title") or "").strip()) or any(
            str(b).strip() for b in (content.get("body") or [])
        )
        has_native = any(content.get(k) for k in ("kpi", "chart", "table", "flow", "image"))
        if not has_text and not has_native:
            log.info("html.slide.phantom_skip", num=num)
            return None

        fast = time.monotonic() > deadline
        if fast:
            log.warning("html.slide.fast_mode", num=num,
                        budget_s=HTML_COMPOSE_BUDGET_S)
        payload = snap_payload({**content, "archetype": archetype},
                               parsed_by_num.get(num))
        png = _compose_one_slide(payload, brand_css, renderer,
                                 archetype, log.bind(num=num), fast=fast)
        p = workdir / f"html_s{num:02d}.png"
        p.write_bytes(png)
        with done_lock:
            done_count += 1
            k = done_count
        _emit(state, Stage.DESIGNING, pct=35 + int(55 * k / total),
              detail=f"слайд {k}/{len(cls_slides)}")
        log.info("html.slide.done", num=num, archetype=archetype, fast=fast)
        return i, str(p)

    def _do_chunk(chunk: list[tuple[int, dict[str, Any]]]) -> list[tuple[int, str]]:
        """Sequential slice of slides on one thread-owned Chromium instance."""
        out: list[tuple[int, str]] = []
        with SlideRenderer(brand_css) as renderer:
            for i, cls in chunk:
                res = _do_slide(i, cls, renderer)
                if res is not None:
                    out.append(res)
        return out

    items = list(enumerate(cls_slides))
    workers = max(1, min(HTML_COMPOSE_WORKERS, len(items)))
    if workers > 1:
        # Round-robin split keeps chunks balanced; order restored by index.
        chunks = [items[k::workers] for k in range(workers)]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            chunk_results = list(pool.map(_do_chunk, chunks))
        indexed = sorted(pair for res in chunk_results for pair in res)
    else:
        indexed = _do_chunk(items)

    png_paths = [path for _, path in indexed]
    if not png_paths:
        raise RuntimeError("html_compose_node: no renderable slides")
    arts["html_png_paths"] = png_paths
    return {"artefacts": arts, "stage": Stage.RENDERING.value, "progress_pct": 90}


def html_pack_node(state: SessionState) -> dict[str, Any]:
    """Pack rendered PNGs full-bleed into the output .pptx."""
    _emit(state, Stage.RENDERING, pct=92, detail="сборка .pptx")
    arts = _artefacts(state)
    from pathlib import Path

    pngs = [Path(p).read_bytes() for p in (arts.get("html_png_paths") or [])]
    workdir = _session_workdir(state.session_id)
    out_path = str(workdir / _output_filename(state.session_id, state.source_filename))
    pack_pngs(pngs, out_path)
    arts["result_path"] = out_path
    logger.info("node.html_pack.done", session_id=state.session_id,
                slides=len(pngs), path=out_path)
    return {"artefacts": arts, "result_s3_key": out_path,
            "stage": Stage.FINALIZING.value, "progress_pct": 95}


def html_finalize_node(state: SessionState) -> dict[str, Any]:
    """Terminal DONE event — the bot's subscriber delivers the file and
    releases the per-user lock (same contract as /design finalize)."""
    arts = _artefacts(state)
    result_path = arts.get("result_path") or state.result_s3_key
    progress.done(state.session_id, detail="готово", result_path=result_path)
    logger.info("node.html_finalize.done", session_id=state.session_id,
                has_result=bool(result_path))
    return {"stage": Stage.DONE.value, "progress_pct": 100,
            "notes": [*state.notes, "Готово"]}
