"""LLM-agent nodes for the v0.9 batch pipeline.

Each function is a LangGraph node:
    (state: SessionState) -> dict (state patch)

All nodes read upstream artefacts from ``state.artefacts`` and write their
own output back into the same dict. The reducer is shallow-merge by
LangGraph; we copy + update + return the whole ``artefacts`` dict to avoid
clobbering sibling keys.

Vision-capable nodes (Brief Reader, Visual Verifier) accept rendered PNGs.
Brief Reader runs vision-grounded if ``parsed_deck`` includes an
``original_pngs`` key with a list of base64 data URLs or paths; otherwise
falls back to text-only (still on Kimi — accuracy boost from grounding is
nice-to-have, not required).
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any

import structlog

from llm.output_parsers import call_and_parse
from llm.prompts import (
    agent_01_brief_reader,
    agent_02_slide_classifier,
    agent_03_content_distributor,
    agent_04_layout_designer,
    agent_05_icon_picker,
    agent_06_infographic_maker,
    agent_07_copy_editor,
    agent_10_visual_verifier,
)
from llm.roles import Role
from schemas.session import SessionState, Stage
from schemas.slides import (
    Brief,
    ContentAssignment,
    DeckClassification,
    IconAssignments,
    InfographicSpec,
    LayoutPlan,
    VisualVerdict,
)
from worker import progress

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Process-level icon-library cache (perf B3).
#
# icons_node() previously re-globbed the SVG directory on every deck.  The
# icons directory is static inside the Docker image, so we cache the sorted
# name list keyed by (resolved_dir_str, dir_mtime_ns).  A threading.Lock
# guards the single write path; reads are lock-free after population.
# ---------------------------------------------------------------------------
_ICONS_CACHE: dict[tuple, list[str]] = {}
_ICONS_CACHE_LOCK = threading.Lock()


def _get_icon_library(icons_dir: Path) -> list[str]:
    """Return sorted list of ``icons/<name>.svg`` strings from *icons_dir*.

    Results are cached by directory mtime so a new Docker image with an
    updated icon set is always picked up.  Returns ``[]`` when the directory
    does not exist.
    """
    if not icons_dir.is_dir():
        return []
    try:
        dir_mtime = icons_dir.stat().st_mtime_ns
    except OSError:
        return []
    key = (str(icons_dir.resolve()), dir_mtime)
    cached = _ICONS_CACHE.get(key)
    if cached is not None:
        return cached
    with _ICONS_CACHE_LOCK:
        cached = _ICONS_CACHE.get(key)
        if cached is not None:
            return cached
        result = sorted(f"icons/{p.name}" for p in icons_dir.glob("*.svg"))
        # Evict stale entries for the same directory.
        stale = [k for k in list(_ICONS_CACHE) if k[0] == str(icons_dir.resolve())]
        for k in stale:
            _ICONS_CACHE.pop(k, None)
        _ICONS_CACHE[key] = result
        return result


# ─── shared helpers ──────────────────────────────────────────────────────────

def _artefacts(state: SessionState) -> dict[str, Any]:
    """Shallow copy of state.artefacts so we can mutate-and-return safely."""
    return dict(state.artefacts)


def _emit(state: SessionState, stage: Stage, pct: int, detail: str) -> None:
    progress.stage(state.session_id, stage, pct=pct, detail=detail)


# ─── 01 Brief Reader (Kimi vision) ───────────────────────────────────────────

def brief_node(state: SessionState) -> dict[str, Any]:
    _emit(state, Stage.PARSING, pct=15, detail="чтение брифа")
    arts = _artefacts(state)
    parsed_deck = arts.get("parsed_deck")
    if parsed_deck is None:
        raise RuntimeError("brief_node: artefacts['parsed_deck'] missing — parse_node didn't run")

    # Optional vision grounding: orchestrator stores rendered PNGs under
    # 'original_pngs' (list of bytes / data-URLs). Kimi vision tolerates empty.
    images = arts.get("original_pngs", [])

    messages, imgs = agent_01_brief_reader.build_messages(parsed_deck, images=images)
    # Brief Reader uses Kimi vision (requires_vision=True). If no PNGs were
    # rendered (text-only draft like .md), inject a 1×1 placeholder PNG so
    # the vision gate in call_role doesn't fire. FIXME: render first slide
    # of input pptx to PNG in parse_node — better grounding than placeholder.
    if not imgs:
        # 1×1 transparent PNG (base64). Keeps Kimi happy without misleading.
        imgs = [
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkAAIAAAoAAv/lxKUAAAAASUVORK5CYII="
        ]

    brief, _ = call_and_parse(
        role=Role.BRIEF_PARSER,
        messages=messages,
        model_cls=Brief,
        images=imgs,
    )
    arts["brief"] = brief.model_dump()
    logger.info("node.brief.done", session_id=state.session_id,
                slide_count=brief.slide_count, topic=brief.topic[:60])
    return {"artefacts": arts, "stage": Stage.PARSING.value, "progress_pct": 20}


# ─── 02 Slide Classifier (DeepSeek) ──────────────────────────────────────────

def _coerce_thin_tables(classification_dump: dict[str, Any]) -> int:
    """Demote ``table_native`` slides with <3 columns to ``multicolumn``.

    The classifier prompt says "Регулярная таблица ≥3×3 → table_native" but
    the LLM occasionally picks ``table_native`` on 2-column lists. Canonical
    triggers (in ``validate_plan.py``) then flag it as a layout mistake.
    Coerce here so downstream nodes get a sensible donor.

    Mutates in place. Returns the count of coerced slides.
    """
    coerced = 0
    for s in classification_dump.get("slides") or []:
        if s.get("slide_type") != "table_native":
            continue
        tbl = s.get("table") or {}
        n_cols = len(tbl.get("headers") or [])
        if n_cols >= 3:
            continue
        # 2-col table → multicolumn with 2col hint; 0/1-col → text.
        s["slide_type"] = None
        s["table"] = None
        if n_cols == 2:
            s["category"] = "multicolumn"
            s["subcategory_hint"] = "2col"
        else:
            s["category"] = "text"
        coerced += 1
    return coerced


def _kpi_value_has_digit(value: Any) -> bool:
    """True iff a KPI ``value`` carries at least one decimal digit.

    The classifier delegates KPI pairing entirely to the LLM, which has no
    deterministic algorithm and once put a bare word ("Прогноз") into a KPI
    ``value`` — rendered as a giant non-number. A real metric always has a
    digit; units/symbols/spaces ("1 200 руб", "15%", "+3,5 п.п.") don't change
    that. ``str.isdigit`` covers unicode decimal digits too.
    """
    return any(ch.isdigit() for ch in str(value))


def _set_card_grid(
    s: dict[str, Any],
    header: str,
    cards: list[dict[str, str]],
) -> None:
    """Re-type slide ``s`` in place as a ``card_grid`` flow native.

    Single source of truth for the three card_grid construction sites (KPI
    overflow demotion, structured-group reconstruction, text-slide diversify):
    derives the column count from the card count, builds the canonical 11-key
    ``flow`` dict the flow_renderer reads, and nulls the sibling native blocks
    so only the flow survives. ``header`` is stored as-is (callers strip it).
    """
    ncards = len(cards)
    cols = 2 if ncards <= 4 else (3 if ncards <= 6 else 4)
    s["slide_type"] = "flow_diagram_native"
    s["category"] = "other"
    s["flow"] = {
        "header": header, "subtitle": "", "preset": "card_grid",
        "cards": cards, "columns": [], "rows": [],
        "statement": "", "support": "", "grid": False, "cols": cols,
        "blocks": [], "arrows": [],
    }
    for k in ("kpi", "chart", "table", "image"):
        s[k] = None


def _coerce_overflow_kpis(classification_dump: dict[str, Any]) -> int:
    """Validate ``kpi_native`` numbers and demote slides the renderer rejects.

    ``skill_assets/scripts/kpi_renderer.py::render_kpi`` only supports 1-3
    numbers, and KPI pairing is fully delegated to the LLM (no deterministic
    algorithm, no numeric check) — so values like "Прогноз" leak through and a
    blind ``nums[:3]`` silently drops money sums.

    Deterministic guard (in the repo's coerce/post-pass spirit):
      1. Drop every number whose ``value`` has NO digit (``_kpi_value_has_digit``)
         — garbage like "Прогноз" never reaches the renderer.
      2. 0 valid left → DEMOTE off ``kpi_native`` to ``multicolumn``. A KPI
         slide with no numbers is broken; the body survives via the brief on the
         donor/text route (fixes the "Прогноз" giant-word slide).
      3. >3 valid → DEMOTE to a ``card_grid`` flow native, one card per
         number+label pair. This PRESERVES ALL pairs (no silent ``nums[:3]``
         money-sum loss); the card grid renders any count cleanly.
      4. 1-3 valid → keep ``kpi_native`` with the filtered numbers.

    Mutates in place. Returns the count of slides touched.
    """
    coerced = 0
    for s in classification_dump.get("slides") or []:
        if s.get("slide_type") != "kpi_native":
            continue
        kpi = s.get("kpi") or {}
        nums = kpi.get("numbers") or []
        valid = [n for n in nums if _kpi_value_has_digit((n or {}).get("value"))]
        if len(valid) == len(nums) and 1 <= len(valid) <= 3:
            continue  # all numeric and within the renderer's limit — leave it
        if not valid:
            # 0 valid → demote to a safe text fallback; body recovered from brief.
            s["slide_type"] = None
            s["kpi"] = None
            s["category"] = "multicolumn"
        elif len(valid) > 3:
            # >3 valid → demote to card_grid, preserving EVERY number+label pair.
            cards = [
                {
                    "title": str((n or {}).get("value", "")).strip(),
                    "text": str((n or {}).get("desc", "")).strip(),
                }
                for n in valid
            ]
            _set_card_grid(s, (kpi.get("title") or "").strip(), cards)
        else:
            # 1-3 valid after dropping non-numeric — keep kpi_native, filtered.
            kpi["numbers"] = valid
            s["kpi"] = kpi
        coerced += 1
    return coerced


def _inject_parsed_tables(
    classification_dump: dict[str, Any],
    parsed_deck: dict[str, Any],
) -> int:
    """Force ``table_native`` with REAL cell data for slides whose source
    .pptx slide held a regular table.

    ``parse_pptx`` extracts the table grid, but the LLM brief→classify chain
    loses the cell text: Kimi marks ``intent=table`` with empty ``raw_body``,
    so the classifier defaults to ``category=text`` with no ``table`` block.
    Build then falls back to the donor-53 PNG-stub placeholder
    ("Столбец 1…/Строка 1…/+" — live dl1 slide 4 "DNS Resolvers"). Here we
    deterministically restore the table from the parsed grid so
    ``table_renderer`` draws the actual branded zebra table.

    Only regular tables (≥3 cols, uniform width, no merged cells) are
    injected; irregular/merged tables are left to the LLM (anti-distortion).
    Native types the classifier deliberately chose (kpi/chart/flow/image) and
    split parts are never overridden.

    Mutates in place. Returns the count of slides injected.
    """
    parsed_by_num: dict[int, dict[str, Any]] = {}
    for ps in (parsed_deck.get("slides") or []):
        n = ps.get("num")
        if not isinstance(n, int):
            continue
        tbls = [
            t for t in (ps.get("tables") or [])
            if t.get("regular") and len(t.get("headers") or []) >= 3
        ]
        if tbls:
            parsed_by_num[n] = {"grid": tbls[0], "title": ps.get("title") or ""}
    if not parsed_by_num:
        return 0

    injected = 0
    for s in classification_dump.get("slides") or []:
        src = s.get("_source_slide") or s.get("num")
        if not isinstance(src, int):
            continue
        entry = parsed_by_num.get(src)
        if not entry:
            continue
        if s.get("slide_type") in (
            "kpi_native", "chart_pptx_native",
            "flow_diagram_native", "image_native",
        ):
            continue
        if s.get("_split_part"):
            continue
        grid = entry["grid"]
        headers = [str(h) for h in (grid.get("headers") or [])]
        rows = [[str(c) for c in r] for r in (grid.get("rows") or [])]
        if not headers or not rows:
            continue
        prev = s.get("table") if isinstance(s.get("table"), dict) else {}
        header_txt = (prev.get("header") or entry["title"] or "").strip()
        s["slide_type"] = "table_native"
        s["category"] = "table"
        s["table"] = {
            "header": header_txt,
            "subtitle": prev.get("subtitle", ""),
            "style": "zebra",
            "headers": headers,
            "data": rows,
            "first_col_wider": True,
        }
        injected += 1
    return injected


def _inject_parsed_charts(
    classification_dump: dict[str, Any],
    parsed_deck: dict[str, Any],
) -> int:
    """Restore ``chart_pptx_native`` with REAL plotted data for slides whose
    source .pptx slide held a native chart object.

    Defect D: ``parse_pptx`` now extracts chart series/categories, but the
    LLM brief→classify chain loses them — the chart object never survives into
    the lossy brief, so the classifier renders a flat text donor and the chart
    is dropped ("0 pictures inserted"). Here we deterministically rebuild the
    branded chart from the parsed data (mirrors ``_inject_parsed_tables``).

    Only flat slides are overridden: a deliberate native the classifier chose
    (kpi/chart/table/flow/image) and split parts are left untouched. Mutates in
    place; returns the count of slides injected.
    """
    parsed_by_num: dict[int, dict[str, Any]] = {}
    for ps in (parsed_deck.get("slides") or []):
        n = ps.get("num")
        if not isinstance(n, int):
            continue
        charts = [
            c for c in (ps.get("charts") or [])
            if (c.get("series") or []) and (c.get("x") or c.get("type") == "pie")
        ]
        if charts:
            parsed_by_num[n] = {"chart": charts[0], "title": ps.get("title") or ""}
    if not parsed_by_num:
        return 0

    injected = 0
    for s in classification_dump.get("slides") or []:
        if s.get("_split_part"):
            continue
        if s.get("slide_type") in (
            "kpi_native", "chart_native", "chart_pptx_native",
            "table_native", "flow_diagram_native", "image_native",
        ):
            continue
        src = s.get("_source_slide") or s.get("num")
        if not isinstance(src, int):
            continue
        entry = parsed_by_num.get(src)
        if not entry:
            continue
        chart = dict(entry["chart"])
        if not chart.get("title"):
            chart["title"] = entry["title"]
        s["slide_type"] = "chart_pptx_native"
        s["category"] = "other"
        s["chart"] = chart
        for k in ("kpi", "table", "flow", "image"):
            s[k] = None
        injected += 1
    return injected


_MARKER_RE = re.compile(r"^\s*[\d]{1,2}\s*[.)]?\s*$")


def _cards_from_group_nodes(group_nodes: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Build card_grid cards from a structured slide's grouped text nodes.

    Source numbered diagrams alternate content nodes with standalone marker
    badges ("1", "2."): those markers carry no content and must not become
    their own cards. Content nodes often pack a label and a description
    separated by a blank line / vertical-tab; split them into card title+text.
    """
    cards: list[dict[str, str]] = []
    for gn in sorted(group_nodes, key=lambda x: x.get("order", 0)):
        raw = str(gn.get("text", "")).strip()
        if not raw or _MARKER_RE.match(raw):
            continue
        # normalise vertical-tab soft breaks, then split label/description
        norm = raw.replace("\x0b", " ")
        parts = re.split(r"\n\s*\n", norm, maxsplit=1)
        title = " ".join(parts[0].split())
        body = " ".join(parts[1].split()) if len(parts) > 1 else ""
        cards.append({"title": title, "text": body})
    return cards


def _force_image_native(s: dict[str, Any], title: str, img_path: str,
                        prev: dict[str, Any] | None = None) -> None:
    """Rewrite a classification slide as an ``image_native`` carrying ``img_path``.

    ``prev`` is the slide's existing image block, if any — its caption/
    subcategory/frame are preserved (so genuine screenshots keep their browser
    chrome). When absent (structured B-fallback) the render is a plain diagram.
    """
    prev = prev or {}
    s["slide_type"] = "image_native"
    s["category"] = "image"
    s["image"] = {
        "title": title,
        "image_path": img_path,
        "caption": prev.get("caption") or "",
        "subcategory": prev.get("subcategory") or "diagram",
        "frame": prev.get("frame"),
    }
    for k in ("kpi", "chart", "table", "flow"):
        s[k] = None


def _inject_visual_slides(
    classification_dump: dict[str, Any],
    parsed_deck: dict[str, Any],
) -> dict[str, int]:
    """Deterministically route visual slides the LLM brief→classify chain
    can't handle, using ``parsed_deck`` as source of truth.

    The brief is LLM-generated and lossy: ``image_path`` (a local temp path)
    and ``group_nodes`` never survive into it, so the classifier cannot route
    these slides itself (mirrors ``_inject_parsed_tables``).

    * raster/opaque slide with a resolved ``image_path`` → force
      ``image_native`` carrying that path (else build skips it → slide drop).
    * structured slide (>=3 grouped text nodes) → rebuild as
      ``flow_diagram_native`` / ``card_grid`` from ``group_nodes`` — a branded
      native diagram instead of a flat text slide.

    Split parts (``_split_part``) are never touched. Mutates in place.
    Returns {"image": n, "flow": n}.
    """
    parsed_by_num: dict[int, dict[str, Any]] = {}
    for ps in (parsed_deck.get("slides") or []):
        n = ps.get("num")
        if isinstance(n, int) and ps.get("visual_kind") in ("raster", "opaque", "structured"):
            parsed_by_num[n] = ps
    if not parsed_by_num:
        return {"image": 0, "flow": 0}

    img_n = 0
    flow_n = 0
    for s in classification_dump.get("slides") or []:
        if s.get("_split_part"):
            continue
        src = s.get("_source_slide") or s.get("num")
        if not isinstance(src, int):
            continue
        ps = parsed_by_num.get(src)
        if not ps:
            continue
        vk = ps.get("visual_kind")
        # Override is unconditional (unlike _inject_parsed_tables, which yields
        # to deliberate natives): visual_kind is authoritative ground truth and
        # the lossy brief carried no usable signal for the classifier to act on.
        if vk in ("raster", "opaque"):
            img_path = ps.get("image_path")
            if not img_path:
                continue  # no image available → leave as-is (logged downstream)
            prev = s.get("image") if isinstance(s.get("image"), dict) else {}
            title = (prev.get("title") or ps.get("title") or "").strip()
            _force_image_native(s, title, img_path, prev)
            img_n += 1
        elif vk == "structured":
            cards = _cards_from_group_nodes(ps.get("group_nodes") or [])
            if len(cards) < 3:
                # Reconstruction too sparse (e.g. group was mostly marker
                # badges) → B-fallback to the full-slide render so the slide
                # is never dropped (design §5). Stays a flat text slide only
                # if no render was stashed.
                fb = ps.get("image_path")
                if fb:
                    _force_image_native(s, (ps.get("title") or "").strip(), fb)
                    img_n += 1
                continue
            prev = s.get("flow") if isinstance(s.get("flow"), dict) else {}
            header = (prev.get("header") or ps.get("title") or "").strip()
            _set_card_grid(s, header, cards)
            flow_n += 1
    return {"image": img_n, "flow": flow_n}


# Label/description separators a brief body item may use, in priority order.
# A line like "Масштабируемость — рост до тысяч ядер" splits into a card
# title ("Масштабируемость") + body ("рост до тысяч ядер").
_CARD_SEP_RE = re.compile(r"\s+[—–-]\s+|:\s+")
_F_MIN_CARDS = 3
_F_MAX_CARDS = 8
_F_PARALLEL_LEN = 90  # an item this short (no separator) still reads as a card
# #2 (card_grid overflow): a card body must coexist with 2-8 siblings in a
# small box (~half-width × one-of-N-rows). A body beyond ~180 chars is prose,
# not a card caption — it overflows and clips even after shrink-to-fit. We pick
# 180 as a middle ground: comfortable for a 2-col grid, still safe-ish for the
# tighter 3/4-col cases (where the renderer's shrink/truncate is the backstop).
# A single over-cap body is treated as a hard veto: it's the CVE/Памятка blob
# pattern that motivated this fix, and forcing it into a card always clips.
_F_CARD_BODY_MAX = 180


def _card_from_body_item(raw: str) -> dict[str, str] | None:
    """Split one brief body line into a card {title, text}.

    Returns None for empty / pure-marker lines. Prefers a label/description
    split on the first separator; falls back to the whole line as the title.
    """
    norm = " ".join(str(raw).replace("\x0b", " ").split())
    if not norm or _MARKER_RE.match(norm):
        return None
    m = _CARD_SEP_RE.search(norm)
    if m and 0 < m.start() <= 60:
        title = norm[:m.start()].strip()
        body = norm[m.end():].strip()
    else:
        title, body = norm, ""
    return {"title": title, "text": body}


def _looks_like_card_grid(cards: list[dict[str, str]]) -> bool:
    """Shared "is this body a card grid, not prose?" heuristic.

    Used at BOTH card-promotion sites (``_diversify_text_slides`` and the
    recovery path in ``_recover_dropped_slides``) so they agree on what counts
    as card-shaped. The full heuristic is:

      * 3-8 non-empty card items (``_F_MIN_CARDS``-``_F_MAX_CARDS``), AND
      * no single card body over ``_F_CARD_BODY_MAX`` (one over-long blob is a
        hard veto — it's the CVE/Памятка prose pattern that clips a card), AND
      * a majority are "card-shaped": a separator body within the cap OR a
        short title-only item (``_F_PARALLEL_LEN``) — else it reads as prose.
    """
    if not (_F_MIN_CARDS <= len(cards) <= _F_MAX_CARDS):
        return False
    if any(len(c["text"]) > _F_CARD_BODY_MAX for c in cards):
        return False
    parallel = sum(
        1 for c in cards
        if (c["text"] and len(c["text"]) <= _F_CARD_BODY_MAX)
        or len(c["title"]) <= _F_PARALLEL_LEN
    )
    return parallel >= max(_F_MIN_CARDS, (len(cards) * 3 + 4) // 5)


def _diversify_text_slides(
    classification_dump: dict[str, Any],
    brief: dict[str, Any],
) -> int:
    """Promote flat ``text``/``multicolumn`` slides whose brief body is a
    parallel list of >=3 short items into a ``card_grid`` flow native.

    Defect F (low layout diversity): the classifier defaults multi-point
    content to a flat "title + big text block" donor. When the source body is
    actually a set of parallel labelled items, a branded card grid reads far
    better. High-precision by design — only fires when the body looks like a
    list of cards, not prose:

      * >=3 and <=8 non-empty body items, AND
      * a majority are "card-shaped" (carry a label separator OR are short).

    Prose bodies (few long paragraphs) are left to the donor route + bullets
    (defect C). Native slides, split parts, and already-typed slides are never
    touched. Mutates in place; returns the count of slides promoted.
    """
    brief_by_num: dict[int, dict[str, Any]] = {}
    for bs in (brief.get("slides") or []):
        n = bs.get("num")
        if isinstance(n, int):
            brief_by_num[n] = bs
    if not brief_by_num:
        return 0

    promoted = 0
    for s in classification_dump.get("slides") or []:
        if s.get("_split_part") or s.get("slide_type"):
            continue
        if s.get("category") not in ("text", "multicolumn"):
            continue
        src = s.get("_source_slide") or s.get("num")
        bs = brief_by_num.get(src) if isinstance(src, int) else None
        if not bs:
            continue
        raw_items = [str(x) for x in (bs.get("raw_body") or []) if str(x).strip()]
        if len(raw_items) < _F_MIN_CARDS:
            continue
        cards = [c for c in (_card_from_body_item(r) for r in raw_items) if c]
        # #2: count range + per-card body cap (one over-long blob is a hard
        # veto: the CVE/Памятка prose pattern clips a card) + majority must be
        # card-shaped (separator within the cap OR a short title-only item) —
        # else it's prose and stays a normal text slide where ALL content
        # survives. See ``_looks_like_card_grid`` (shared with the recovery path).
        if not _looks_like_card_grid(cards):
            continue
        header = (bs.get("raw_title") or "").strip()
        _set_card_grid(s, header, cards)
        promoted += 1
    return promoted


# A body is "substantial" — worth its own companion slide next to an
# ``image_native`` — when it carries real prose, not a one-line caption.
# Empirically, bodies below ~12 words read as a caption the image already
# implies; at or above it the text earns its own slide. 12 is a hand-tuned
# floor (a couple of short captions stay folded; two real sentences earn a
# companion), not a value derived from the sparse-detector constants.
_IMG_COMPANION_MIN_BODY_WORDS = 12


def _slide_body_word_count(bs: dict[str, Any]) -> int:
    """Total word count across a brief slide's ``raw_body`` lines."""
    return sum(
        len(str(x).replace("\x0b", " ").split())
        for x in (bs.get("raw_body") or [])
        if str(x).strip()
    )


def _inject_image_companions(
    classification_dump: dict[str, Any],
    brief: dict[str, Any],
) -> int:
    """Inject a companion text slide for an ``image_native`` whose body is lost.

    The ``image_native`` renderer draws only the picture (title/caption), never
    the body prose. The LLM emits a companion text slide ONLY when the body is
    card-shaped (CONFIG/IOTDM); for OBS/CCE-style slides (<=2 sections of prose
    + a dominant image) the body is dropped — image shown, prose gone.

    Deterministic mirror of that companion (repo's guard-over-LLM-trust spirit):
    for each ``image_native`` slide whose brief body is substantial
    (>= ``_IMG_COMPANION_MIN_BODY_WORDS`` words) AND has no sibling text/card
    slide for the same ``_source_slide``, inject a companion carrying the body —
    a ``card_grid`` flow native when card-shaped (``_looks_like_card_grid``),
    else a plain text/multicolumn slide the distributor fills from the brief by
    ``_source_slide``.

    Runs AFTER ``_inject_visual_slides`` (the image route must be settled) and
    consumes the brief, the source of the body the image_native slide can't show.
    Split parts are never touched. A fresh deck num (max existing + 1) is
    allocated per companion — reusing the source num collides with split parts.
    Mutates in place; returns the count of companions injected.
    """
    brief_by_num: dict[int, dict[str, Any]] = {}
    for bs in (brief.get("slides") or []):
        n = bs.get("num")
        if isinstance(n, int):
            brief_by_num[n] = bs
    if not brief_by_num:
        return 0

    slides = classification_dump.setdefault("slides", [])
    # A sibling that already carries the body: any non-image_native slide
    # (text/multicolumn donor OR a body-bearing native) sharing the source.
    siblings_by_src: dict[int, bool] = {}
    for s in slides:
        if s.get("slide_type") == "image_native":
            continue
        src = s.get("_source_slide") or s.get("num")
        if isinstance(src, int):
            siblings_by_src[src] = True

    next_num = max(
        (s.get("num") for s in slides if isinstance(s.get("num"), int)),
        default=0,
    ) + 1
    injected = 0
    for s in list(slides):
        if s.get("slide_type") != "image_native" or s.get("_split_part"):
            continue
        src = s.get("_source_slide") or s.get("num")
        if not isinstance(src, int):
            continue
        if siblings_by_src.get(src):
            continue  # a sibling text/card slide already carries the body
        bs = brief_by_num.get(src)
        if not bs:
            continue
        if _slide_body_word_count(bs) < _IMG_COMPANION_MIN_BODY_WORDS:
            continue  # caption-sized body the image already implies → fold it

        deck_num = next_num
        next_num += 1
        comp: dict[str, Any] = {
            "num": deck_num,
            "category": "text",
            "subcategory_hint": "",
            "rationale": "companion: image_native body recovered to text slide",
            "slide_type": None,
            "kpi": None, "chart": None, "table": None, "flow": None, "image": None,
            "_source_slide": src,
        }
        raw_items = [str(x) for x in (bs.get("raw_body") or []) if str(x).strip()]
        cards = [c for c in (_card_from_body_item(r) for r in raw_items) if c]
        if _looks_like_card_grid(cards):
            _set_card_grid(comp, (bs.get("raw_title") or "").strip(), cards)
        elif len(raw_items) > 1:
            comp["category"] = "multicolumn"
        slides.append(comp)
        siblings_by_src[src] = True
        injected += 1

    if injected:
        # Keep each companion next to its image_native sibling (same convention
        # as _recover_dropped_slides): order is by classification ARRAY position
        # downstream, lookups are by num/_source_slide — reordering is safe.
        slides.sort(key=lambda s: (s.get("_source_slide") or s.get("num") or 0,
                                   s.get("_split_part") or ""))
    return injected


# Brief intents that mean "a table IS the slide's content" (Agent 01 prompt:
# "schema/chart/table — диаграмма/график/таблица как основное содержимое").
# A dropped slide with this intent is recovered as a ``table_native``.
_TABLE_INTENTS = ("table",)


def _table_from_brief_slide(bs: dict[str, Any]) -> dict[str, Any]:
    """Build a valid ``TableConfig`` dict from a text-only brief slide.

    The brief is lossy — it carries no real cell grid, only ``raw_title`` +
    ``raw_body`` lines. We render each non-empty body line as a single-column
    row under a one-column header (the title). This is a structurally valid
    table that preserves every body line; if ``parse_pptx`` actually extracted
    the grid, ``_inject_parsed_tables`` (which runs AFTER this guard) overwrites
    this stub with the real cells keyed by ``_source_slide``.
    """
    header = (bs.get("raw_title") or "").strip()
    rows = [[" ".join(str(x).replace("\x0b", " ").split())]
            for x in (bs.get("raw_body") or []) if str(x).strip()]
    if not rows:
        rows = [[""]]
    return {
        "header": header,
        "subtitle": "",
        "style": "zebra",
        "headers": [header or "Данные"],
        "data": rows,
        "first_col_wider": True,
    }


def _recover_dropped_slides(
    classification_dump: dict[str, Any],
    brief: dict[str, Any],
) -> list[int]:
    """Guard: every brief slide ``num`` must be represented in classification.

    The LLM classifier can split a brief slide and mis-renumber, silently
    dropping a whole brief slide's content (live dl1: brief slide 4 DNS table
    vanished after a 3→(3,4) split). There is no deterministic check that each
    brief slide is represented — this is that check, in the repo's
    deterministic-guard-over-LLM-trust spirit (cf. ``strip_residual_markdown``,
    ``_recover_dropped_body_lines``, the Task-1 KPI guard).

    A classification slide represents the brief slide identified by
    ``_source_slide or num`` (the established split convention) — so both halves
    of a 3→(3,4) split credit brief slide 3, leaving brief slide 4 still needing
    recovery if no slide maps to it.

    For each unrepresented brief slide a recovery slide is INJECTED, built from
    that brief slide's own content (zero silent loss):
      * table intent → ``table_native`` (real grid restored later by
        ``_inject_parsed_tables`` if parsed; else a valid text-row stub),
      * structured body (>=3 card-shaped items) → ``card_grid`` flow native,
      * else → a plain donor-route text/multicolumn slide (the distributor
        fills it from the brief by ``num``/``_source_slide``).

    Runs BEFORE the type-coercion passes so injected slides flow through them.
    Mutates ``classification_dump`` in place; returns the recovered brief nums.
    """
    brief_slides = [bs for bs in (brief.get("slides") or [])
                    if isinstance(bs.get("num"), int)]
    if not brief_slides:
        return []

    represented: set[int] = set()
    for s in classification_dump.get("slides") or []:
        src = s.get("_source_slide") or s.get("num")
        if isinstance(src, int):
            represented.add(src)

    slides = classification_dump.setdefault("slides", [])
    # The classifier renumbers the deck IN PLACE after a split (e.g. brief 3 →
    # deck nums 3 AND 4), so a recovery slide MUST NOT reuse the brief num as its
    # deck ``num`` — that collides with a split part. Downstream lookups
    # (``_by_num`` in pipeline.py, ``cls_by_num`` in design_node/assemble_node)
    # are last-wins keyed by ``num``, so a duplicate silently drops a slide.
    # Allocate a fresh deck num (max existing + 1) per injection; keep
    # ``_source_slide = brief num`` so ``_inject_parsed_tables`` still restores
    # the real grid keyed on the brief slide.
    next_num = max(
        (s.get("num") for s in slides if isinstance(s.get("num"), int)),
        default=0,
    ) + 1
    recovered: list[int] = []
    for bs in brief_slides:
        num = bs["num"]
        if num in represented:
            continue
        deck_num = next_num
        next_num += 1
        rec: dict[str, Any] = {
            "num": deck_num,
            "category": "text",
            "subcategory_hint": "",
            "rationale": "recovered: brief slide dropped by classifier",
            "slide_type": None,
            "kpi": None, "chart": None, "table": None, "flow": None, "image": None,
            "_source_slide": num,
        }
        intent = str(bs.get("intent") or "").strip().lower()
        raw_items = [str(x) for x in (bs.get("raw_body") or []) if str(x).strip()]
        if intent in _TABLE_INTENTS:
            rec["slide_type"] = "table_native"
            rec["category"] = "table"
            rec["table"] = _table_from_brief_slide(bs)
        else:
            cards = [c for c in (_card_from_body_item(r) for r in raw_items) if c]
            if _looks_like_card_grid(cards):
                _set_card_grid(rec, (bs.get("raw_title") or "").strip(), cards)
            elif len(raw_items) > 1:
                rec["category"] = "multicolumn"
        slides.append(rec)
        represented.add(num)
        recovered.append(num)

    # Place recovery slides in correct reading order. ``assemble_node`` drives
    # the final render order by classification ARRAY position (it iterates
    # ``classification_slides`` directly), while every cross-artefact lookup is
    # by ``num``/``_source_slide`` — never array index. So reordering the array
    # is safe and lets us put each recovery slide (fresh max+1 ``num`` but
    # ``_source_slide`` = brief num) next to its sibling split parts instead of
    # dangling at the deck tail. A stable sort by ``(_source_slide or num,
    # _split_part)`` is a no-op on legitimate in-order LLM output and only moves
    # appended recovery slides into place.
    slides.sort(key=lambda s: (s.get("_source_slide") or s.get("num") or 0,
                               s.get("_split_part") or ""))
    return recovered


# Categories the sparse detector inspects. Everything else (title/divider/
# image/logo/pattern_bg/team/timeline/callout and all native slide_types)
# is intentionally light or specialised — never a sparse "flat donor" case.
_SPARSE_CATEGORIES = ("text", "multicolumn")
_SPARSE_MIN_BODY_SLOTS = 3
_SPARSE_THIN_WORDS = 2


def _detect_sparse_slides(
    classification: dict[str, Any],
    layouts: dict[str, Any],
    content: dict[str, Any],
) -> list[dict[str, Any]]:
    """Flag flat-donor slides whose body slots carry trivially little text —
    the "lonely header over empty brand decoration" case.

    Phase-2A signal is per-slot WORD volume, not occupancy: on real flat donors
    the distributor fills every slot, so underfill shows up as a body slot with
    <= ``_SPARSE_THIN_WORDS`` words (e.g. a 3-column donor holding 1–2 words per
    column), even at full occupancy. Timeline donors (variable-length roadmaps)
    are exempt — partial fill there is intentional. See spec §3–§4.

    Telemetry-only: returns diagnostics, mutates nothing. Runs at the end of
    ``distribute_node`` where both the chosen donor and placed content are known.
    """
    from graph import donor_map  # noqa: WPS433 — local import keeps cycle clear

    cls_by_num: dict[int, dict[str, Any]] = {
        int(s.get("num", 0)): s for s in (classification.get("slides") or [])
    }
    lay_by_num: dict[int, dict[str, Any]] = {
        int(s.get("num", 0)): s for s in (layouts.get("slides") or [])
    }

    out: list[dict[str, Any]] = []
    for cs in (content.get("slides") or []):
        num = int(cs.get("slide_num", 0))
        cls = cls_by_num.get(num) or {}
        if cls.get("_split_part"):
            continue
        category = cls.get("category")
        if category not in _SPARSE_CATEGORIES:
            continue
        layout_idx = cs.get("layout_idx") or (lay_by_num.get(num) or {}).get("layout_idx") or 0
        if not layout_idx:  # native render — no donor to underfill
            continue
        layout_idx = int(layout_idx)
        total = donor_map.body_slot_count(layout_idx)
        if total < _SPARSE_MIN_BODY_SLOTS:
            continue
        if donor_map.is_timeline_donor(layout_idx):  # roadmap — sparse by design
            continue
        body_idxs = donor_map.body_ph_indices(layout_idx)
        words_per_slot: list[int] = []
        for pa in (cs.get("placeholder_assignments") or []):
            ph = pa.get("ph_idx")
            if ph is None or int(ph) not in body_idxs:
                continue
            text = (pa.get("content") or "").strip()
            if text:
                words_per_slot.append(len(text.split()))
        thin = sum(1 for w in words_per_slot if w <= _SPARSE_THIN_WORDS)
        if thin == 0:
            continue
        out.append({
            "num": num,
            "source_slide": cls.get("_source_slide") or num,
            "category": category,
            "layout_idx": layout_idx,
            "body_slots_total": total,
            "body_slots_filled": len(words_per_slot),
            "body_words_per_slot": words_per_slot,
            "thin_slot_count": thin,
            "body_word_total": sum(words_per_slot),
        })
    return out


def classify_node(state: SessionState) -> dict[str, Any]:
    _emit(state, Stage.CLASSIFYING, pct=25, detail="классификация слайдов")
    arts = _artefacts(state)
    brief = arts["brief"]

    classification, _ = call_and_parse(
        role=Role.CLASSIFIER,
        messages=agent_02_slide_classifier.build_messages(brief),
        model_cls=DeckClassification,
    )
    classification_dump = classification.model_dump()
    recovered_slides = _recover_dropped_slides(classification_dump, brief)
    thin_tables = _coerce_thin_tables(classification_dump)
    overflow_kpis = _coerce_overflow_kpis(classification_dump)
    injected_tables = _inject_parsed_tables(
        classification_dump, arts.get("parsed_deck") or {})
    injected_charts = _inject_parsed_charts(
        classification_dump, arts.get("parsed_deck") or {})
    visual_routed = _inject_visual_slides(
        classification_dump, arts.get("parsed_deck") or {})
    image_companions = _inject_image_companions(classification_dump, brief)
    diversified = _diversify_text_slides(classification_dump, brief)
    arts["classification"] = classification_dump
    if recovered_slides:
        logger.warning(
            "node.classify.slides_recovered",
            session_id=state.session_id,
            count=len(recovered_slides),
            nums=recovered_slides,
        )
    if injected_tables:
        logger.info(
            "node.classify.parsed_tables_injected",
            session_id=state.session_id,
            count=injected_tables,
        )
    if injected_charts:
        logger.info(
            "node.classify.parsed_charts_injected",
            session_id=state.session_id,
            count=injected_charts,
        )
    if visual_routed["image"] or visual_routed["flow"]:
        logger.info(
            "node.classify.visual_slides_routed",
            session_id=state.session_id,
            image=visual_routed["image"],
            flow=visual_routed["flow"],
        )
    if thin_tables:
        logger.warning(
            "node.classify.thin_tables_coerced",
            session_id=state.session_id,
            count=thin_tables,
        )
    if overflow_kpis:
        logger.warning(
            "node.classify.overflow_kpis_coerced",
            session_id=state.session_id,
            count=overflow_kpis,
        )
    if image_companions:
        logger.info(
            "node.classify.image_companions_injected",
            session_id=state.session_id,
            count=image_companions,
        )
    if diversified:
        logger.info(
            "node.classify.text_slides_diversified",
            session_id=state.session_id,
            count=diversified,
        )
    logger.info("node.classify.done", session_id=state.session_id,
                slides=len(classification.slides),
                thin_tables_coerced=thin_tables,
                overflow_kpis_coerced=overflow_kpis,
                parsed_tables_injected=injected_tables,
                parsed_charts_injected=injected_charts,
                visual_image_routed=visual_routed["image"],
                visual_flow_routed=visual_routed["flow"],
                text_slides_diversified=diversified,
                image_companions_injected=image_companions,
                slides_recovered=len(recovered_slides))
    return {"artefacts": arts, "stage": Stage.CLASSIFYING.value, "progress_pct": 30}


# ─── 04 Layout Designer (DeepSeek) ───────────────────────────────────────────

def design_node(state: SessionState) -> dict[str, Any]:
    """Runs Agent 04 BEFORE Distributor — Distributor needs slot capacities
    from the chosen donors. Order: classify → design → distribute.

    Post-LLM, validates every ``layout_idx`` against
    ``donor_map.valid_donor_ids()``. Picks that aren't in the slot map
    (designer hallucination — common before v1.1 prompt rewrite, e.g.
    template meta-slides 1, 9) are replaced by
    ``default_donor_for_category()``. We DON'T re-run the LLM on bad
    picks — a deterministic fallback keeps the cost predictable.
    Native slides (layout_idx=0) are passed through.
    """
    _emit(state, Stage.DESIGNING, pct=35, detail="подбор layout")
    arts = _artefacts(state)
    classification = arts["classification"]

    layouts, _ = call_and_parse(
        role=Role.DESIGNER,
        messages=agent_04_layout_designer.build_messages(classification),
        model_cls=LayoutPlan,
    )

    from graph import donor_map  # noqa: WPS433 — local to keep cycle clear
    valid = donor_map.valid_donor_ids()
    cls_by_num: dict[int, dict[str, Any]] = {
        int(s.get("num", 0)): s for s in (classification.get("slides") or [])
    }

    layouts_dump = layouts.model_dump(by_alias=True)
    repairs: list[dict[str, Any]] = []
    for entry in layouts_dump.get("slides") or []:
        idx = entry.get("layout_idx")
        if idx in (None, 0):
            # 0 = native render (no donor) — leave alone.
            continue
        if int(idx) in valid:
            continue
        cls = cls_by_num.get(int(entry.get("num") or 0)) or {}
        fallback = donor_map.default_donor_for_category(
            cls.get("category", "other"),
            subcategory_hint=cls.get("subcategory_hint"),
            dark=bool(cls.get("dark")),
        )
        repairs.append({
            "num": entry.get("num"),
            "from": idx,
            "to": fallback,
            "category": cls.get("category"),
        })
        # Fallback to None means we couldn't find a safe donor — keep the
        # LLM's pick so the pipeline still produces something; assemble_node
        # will log the unmapped donor when it tries to translate slots.
        entry["layout_idx"] = fallback if fallback is not None else idx
        entry["layout_name"] = entry.get("layout_name") or "fallback"
        entry["rationale"] = (entry.get("rationale") or "") + " [auto-repair: donor not in slot map]"

    if repairs:
        logger.warning(
            "node.design.invalid_donors_repaired",
            session_id=state.session_id,
            count=len(repairs),
            repairs=repairs,
        )

    # A4: capacity scoring — the designer picks by content TYPE; when the
    # slide's raw text volume badly overloads the chosen donor's body
    # capacity, swap for the smallest content-family donor that fits.
    brief_by_num: dict[int, dict[str, Any]] = {
        int(s.get("num", 0)): s
        for s in ((arts.get("brief") or {}).get("slides") or [])
    }
    upgrades: list[dict[str, Any]] = []
    for entry in layouts_dump.get("slides") or []:
        idx = entry.get("layout_idx")
        if not idx:
            continue
        cls = cls_by_num.get(int(entry.get("num") or 0)) or {}
        if cls.get("slide_type"):
            continue  # native render — donor is decorative only
        src = cls.get("_source_slide") or entry.get("num")
        bslide = brief_by_num.get(int(src or 0)) or {}
        required = sum(
            len(x) for x in (bslide.get("raw_body") or []) if isinstance(x, str)
        )
        if cls.get("_source_slide"):
            required //= 2  # split rules always split into 2 parts
        new_idx = donor_map.upgrade_donor_for_volume(
            int(idx),
            cls.get("category", "other"),
            required,
            dark=bool(cls.get("dark")),
        )
        if new_idx is None:
            continue
        upgrades.append({
            "num": entry.get("num"), "from": idx, "to": new_idx,
            "required_chars": required,
            "capacity": donor_map.body_capacity(int(idx)),
        })
        entry["layout_idx"] = new_idx
        entry["rationale"] = (entry.get("rationale") or "") + \
            " [capacity-upgrade: content volume exceeds donor body capacity]"

    if upgrades:
        logger.info(
            "node.design.capacity_upgrades",
            session_id=state.session_id,
            count=len(upgrades),
            upgrades=upgrades,
        )

    arts["layouts"] = layouts_dump
    logger.info("node.design.done", session_id=state.session_id,
                slides=len(layouts_dump.get("slides") or []),
                repaired=len(repairs))
    return {"artefacts": arts, "stage": Stage.DESIGNING.value, "progress_pct": 40}


# ─── 03 Content Distributor (GLM OFF) ────────────────────────────────────────

def distribute_node(state: SessionState) -> dict[str, Any]:
    _emit(state, Stage.DESIGNING, pct=45, detail="распределение контента")
    arts = _artefacts(state)
    brief = arts["brief"]
    classification = arts["classification"]
    layouts = arts["layouts"]
    # Pull per-donor slot capacities from skill_assets/brand/donor-slot-map.yaml
    # so GLM can fit copy to safe_max_chars. Native slides (layout_idx=0) are
    # skipped — they don't have a donor and the distributor ignores them.
    from graph import donor_map  # noqa: WPS433 — local import keeps cycle clear
    layout_idxs = [
        s.get("layout_idx") or s.get("donor") or 0
        for s in (layouts.get("slides") or [])
    ]
    slot_specs = donor_map.slot_specs_for_layouts(layout_idxs)

    content, _ = call_and_parse(
        role=Role.DISTRIBUTOR,
        messages=agent_03_content_distributor.build_messages(
            brief, classification, layouts, slot_specs,
        ),
        model_cls=_DeckContentAssignment,
    )
    arts["content"] = content.model_dump()
    sparse = _detect_sparse_slides(
        classification, layouts, arts["content"])
    if sparse:
        # Phase-2A telemetry: thin flat-donor slides by per-slot word volume.
        logger.info(
            "node.distribute.sparse_volume",
            session_id=state.session_id,
            count=len(sparse),
            slides=sparse,
        )
    logger.info("node.distribute.done", session_id=state.session_id,
                slides=len(content.slides),
                thin_slides=len(sparse))
    return {"artefacts": arts, "stage": Stage.DESIGNING.value, "progress_pct": 50}


# Distributor outputs a deck-level wrapper {"slides": [ContentAssignment, ...]}.
# schemas/slides.py defines ContentAssignment per-slide; declare the wrapper
# locally so we don't pollute the public schema module.
from pydantic import BaseModel, ConfigDict, Field, ValidationError  # noqa: E402 — co-located helper


class _DeckContentAssignment(BaseModel):
    model_config = ConfigDict(extra="allow")
    slides: list[ContentAssignment] = Field(default_factory=list)


class _DeckIcons(BaseModel):
    model_config = ConfigDict(extra="allow")
    slides: list[IconAssignments] = Field(default_factory=list)


class _DeckInfographics(BaseModel):
    model_config = ConfigDict(extra="allow")
    slides: list[InfographicSpec] = Field(default_factory=list)


# ─── 05 Icon Picker (GLM OFF) ────────────────────────────────────────────────

def icons_node(state: SessionState) -> dict[str, Any]:
    _emit(state, Stage.DESIGNING, pct=55, detail="подбор иконок")
    arts = _artefacts(state)
    # Scan vendored SVGs. Currently only brand_arrow.svg ships with M2;
    # Icon Picker will return fallback=TODO for most blocks until the
    # library is populated (tracked outside M3).
    from worker.skill_bridge import SKILL_BRAND  # noqa: WPS433
    icons_dir = SKILL_BRAND / "icons"
    icon_library = _get_icon_library(icons_dir)

    icons, _ = call_and_parse(
        role=Role.ICON_PICKER,
        messages=agent_05_icon_picker.build_messages(
            arts["classification"], arts["content"], icon_library,
        ),
        model_cls=_DeckIcons,
    )
    arts["icons"] = icons.model_dump()
    logger.info("node.icons.done", session_id=state.session_id,
                slides=len(icons.slides))
    return {"artefacts": arts, "stage": Stage.DESIGNING.value, "progress_pct": 60}


# ─── 06 Infographic Maker (GLM OFF) ──────────────────────────────────────────

def infographic_node(state: SessionState) -> dict[str, Any]:
    _emit(state, Stage.DESIGNING, pct=65, detail="инфографика")
    arts = _artefacts(state)
    try:
        infographics, _ = call_and_parse(
            role=Role.INFOGRAPHIC_MAKER,
            messages=agent_06_infographic_maker.build_messages(
                arts["classification"], arts["content"],
            ),
            model_cls=_DeckInfographics,
        )
    except (ValueError, ValidationError) as e:
        # Infographics are a cosmetic enrichment step. A malformed/truncated
        # LLM reply (call_and_parse already auto-bumps tokens + retries once
        # before raising — real incident: GLM-5.1 truncated JSON at char
        # ~15135) must NOT take down the whole deck. Degrade to an empty
        # infographics set: assemble_plan_node tolerates an empty/absent
        # 'infographics' artefact (info_by_num lookups return {} and it only
        # attaches shapes when infographic_type not in (None, "none")), so
        # slides build as plain donor slides without infographic shapes.
        arts["infographics"] = _DeckInfographics(slides=[]).model_dump()
        logger.warning(
            "node.infographic.fallback_empty",
            session_id=state.session_id,
            error=str(e)[:300],
        )
        return {"artefacts": arts, "stage": Stage.DESIGNING.value,
                "progress_pct": 70}
    arts["infographics"] = infographics.model_dump()
    logger.info("node.infographic.done", session_id=state.session_id,
                slides=len(infographics.slides))
    return {"artefacts": arts, "stage": Stage.DESIGNING.value, "progress_pct": 70}


# ─── Enrich fan-out (B2): icons ∥ infographic ∥ copyedit ────────────────────
#
# All three enrichment agents depend only on distribute output
# (classification + content) and write DISJOINT artefact keys
# (icons / infographics / copy_edited), so they can run concurrently.
# We parallelize inside ONE graph node (ThreadPoolExecutor) instead of three
# parallel LangGraph branches: branch-level parallelism would require
# reducers on every concurrently-written SessionState key (artefacts, stage,
# progress_pct). The Cloud.ru RPS limiter in llm/client.py gates the
# concurrent outbound calls.

def enrich_fanout_node(state: SessionState) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor

    _emit(state, Stage.DESIGNING, pct=55,
          detail="иконки + инфографика + редактура")
    branches = (icons_node, infographic_node, copyedit_node)
    with ThreadPoolExecutor(max_workers=len(branches)) as pool:
        futures = [pool.submit(node, state) for node in branches]
        patches = [f.result() for f in futures]

    arts = _artefacts(state)
    for patch in patches:
        arts.update(patch["artefacts"])
    logger.info("node.enrich_fanout.done", session_id=state.session_id)
    return {"artefacts": arts, "stage": Stage.DESIGNING.value, "progress_pct": 75}


# ─── 07 Copy Editor (GLM OFF) ────────────────────────────────────────────────

# Emoji codepoints render as empty squares (□) under SB Sans Display (the
# Cloud.ru template font). Visual Verifier flagged this on slide 4 of the
# 2026-06-04 live run ("эмодзи отображаются как пустые квадраты"). Source
# decks routinely use emoji as bullet markers (📤🔗📊🧠) which the LLM
# happily passes through. Strip them deterministically post-copyedit so we
# don't depend on the LLM remembering to clean them up.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols & pictographs, transport, emoticons, supplemental
    "\U00002600-\U000027BF"  # misc symbols + dingbats
    "\U0001F1E6-\U0001F1FF"  # regional indicator (flags)
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0001F000-\U0001F02F"  # mahjong/dominos
    "\U0001F0A0-\U0001F0FF"  # playing cards
    "]",
    flags=re.UNICODE,
)


def _strip_unsupported_glyphs(text: str) -> str:
    """Remove emoji codepoints and collapse any whitespace they leave behind.

    Returns the input unchanged when there are no matches so we don't churn
    well-formed strings.
    """
    if not text or not _EMOJI_PATTERN.search(text):
        return text
    cleaned = _EMOJI_PATTERN.sub("", text)
    # Tidy up leftover double spaces / leading bullets without an icon.
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"(?m)^[ \t]+", "", cleaned)
    return cleaned.strip()


# Source-brief page-reference citations ("(стр. 3)", "(стр. 5, 10)",
# "(стр.7)", "(стр 12)", "(стр. 5-10)") leak through copyediting into card
# bodies (deck3/Горбачевский s14). They are document-internal cross-references,
# meaningless on a slide. Strip them deterministically, like the emoji pass.
_PAGE_REF_PATTERN = re.compile(
    r"\s*\(\s*стр\.?\s*\d+(?:\s*[,–—-]\s*\d+)*\s*\)",
    flags=re.UNICODE | re.IGNORECASE,
)


def _strip_page_refs(text: str) -> str:
    """Remove parenthesised "(стр. N[, M…])" page-ref citations and tidy the
    whitespace they leave behind. Returns the input unchanged when there are no
    matches so well-formed strings aren't churned."""
    if not text or not _PAGE_REF_PATTERN.search(text):
        return text
    cleaned = _PAGE_REF_PATTERN.sub("", text)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _strip_page_refs_from_content(content_dump: dict[str, Any]) -> int:
    """Apply ``_strip_page_refs`` to every placeholder content in a
    DeckContentAssignment dump. Returns the number of fields that changed.
    Mutates the dict in place."""
    changed = 0
    for slide in content_dump.get("slides") or []:
        for ph in slide.get("placeholder_assignments") or []:
            orig = ph.get("content")
            if isinstance(orig, str):
                new = _strip_page_refs(orig)
                if new != orig:
                    ph["content"] = new
                    changed += 1
    return changed


def _strip_emoji_from_content(content_dump: dict[str, Any]) -> int:
    """Apply ``_strip_unsupported_glyphs`` to every placeholder content in a
    DeckContentAssignment dump. Returns the number of fields that changed.

    Mutates the dict in place.
    """
    changed = 0
    for slide in content_dump.get("slides") or []:
        for ph in slide.get("placeholder_assignments") or []:
            orig = ph.get("content")
            if isinstance(orig, str):
                new = _strip_unsupported_glyphs(orig)
                if new != orig:
                    ph["content"] = new
                    changed += 1
    return changed


def copyedit_node(state: SessionState) -> dict[str, Any]:
    _emit(state, Stage.DESIGNING, pct=72, detail="редактура текста")
    arts = _artefacts(state)
    edited, _ = call_and_parse(
        role=Role.COPY_EDITOR,
        messages=agent_07_copy_editor.build_messages(arts["content"]),
        model_cls=_DeckContentAssignment,
    )
    edited_dump = edited.model_dump()
    emoji_stripped = _strip_emoji_from_content(edited_dump)
    page_refs_stripped = _strip_page_refs_from_content(edited_dump)
    arts["copy_edited"] = edited_dump
    total_edits = sum(s.edits_count for s in edited.slides)
    logger.info("node.copyedit.done", session_id=state.session_id,
                slides=len(edited.slides), edits=total_edits,
                emoji_stripped=emoji_stripped,
                page_refs_stripped=page_refs_stripped)
    return {"artefacts": arts, "stage": Stage.DESIGNING.value, "progress_pct": 75}


# ─── 10 Visual Verifier (Kimi vision) ────────────────────────────────────────

def visual_verify_node(state: SessionState) -> dict[str, Any]:
    _emit(state, Stage.VALIDATING, pct=90, detail="визуальная проверка")
    arts = _artefacts(state)
    plan = arts.get("plan")
    if plan is None:
        raise RuntimeError("visual_verify_node: artefacts['plan'] missing — assemble_plan_node didn't run")
    rendered_pngs = arts.get("rendered_pngs", [])
    if not rendered_pngs:
        # FIXME(next-chunk): render_png_node must populate this. For now
        # skip with a NEEDS_REWORK verdict so we don't silently pass.
        logger.warning("node.visual.skip_no_pngs", session_id=state.session_id)
        arts["visual_verdict"] = {
            "llm_verdict": "NEEDS_REWORK",
            "score_avg": 0,
            "slides": [],
            "next_actions": ["render_png_node not yet implemented — cannot verify"],
        }
        return {"artefacts": arts, "stage": Stage.VALIDATING.value, "progress_pct": 92}

    messages, imgs = agent_10_visual_verifier.build_messages(plan, rendered_pngs)
    verdict, _ = call_and_parse(
        role=Role.VISUAL_VERIFIER,
        messages=messages,
        model_cls=VisualVerdict,
        images=imgs,
    )
    arts["visual_verdict"] = verdict.model_dump()
    logger.info("node.visual.done", session_id=state.session_id,
                verdict=verdict.llm_verdict, score=verdict.score_avg)
    return {"artefacts": arts, "stage": Stage.VALIDATING.value, "progress_pct": 92}


# ─── M4 Autofix loop ─────────────────────────────────────────────────────────

AUTOFIX_BUDGET = 1
"""Max number of autofix passes per session. Each pass is a full re-build +
re-verify cycle, so the wall-clock cost is roughly equal to one nominal
pipeline run. We cap at 1 to keep per-deck Cloud.ru spend predictable; raise
only after measuring real lift from a second pass."""


# Categories of issues for autofix routing — see T1.1 diagnosis 2026-06-04
# (memory/live_run_findings.md). Live run had 8/11 blockers in text_replaced
# + semantics (caused by ph_type bug, fixed in T0.2) — the autofix loop wasted
# a retry on COPY_EDITOR which couldn't address them. These tags let
# autofix_can_help() skip when COPY_EDITOR would be ineffective.
_ISSUE_CATEGORIES = (
    "text_overflow",   # chars > max — COPY_EDITOR can rephrase/shorten
    "text_replaced",   # placeholder leaked into render — needs build/donor fix
    "semantics",       # content doesn't match slide topic — COPY_EDITOR may help
    "aesthetic",       # missing brand accents / scannability — needs visual agent
    "other",
)
# Substrings (lowercased) that map an issue line to a category.
_TAG_BY_SUBSTRING: tuple[tuple[str, str], ...] = (
    ("text_replaced", "text_replaced"),
    ("placeholder", "text_replaced"),
    ("overflow", "text_overflow"),
    ("chars > max", "text_overflow"),
    ("strategy 3", "text_overflow"),
    ("semantics_ok", "semantics"),
    ("не соответствует", "semantics"),
    ("hierarchy", "aesthetic"),
    ("philosophy", "aesthetic"),
    ("function", "aesthetic"),
    ("detail", "aesthetic"),
    ("бренд", "aesthetic"),
    ("сканируется", "aesthetic"),
)


def _categorize_issue(line: str) -> str:
    s = line.lower()
    for needle, tag in _TAG_BY_SUBSTRING:
        if needle in s:
            return tag
    return "other"


def issue_breakdown(arts: dict[str, Any]) -> dict[str, int]:
    """Count blockers + warnings by category. Used by route guards + logs."""
    counts: dict[str, int] = {c: 0 for c in _ISSUE_CATEGORIES}
    ver = arts.get("verifier_verdict") or {}
    for item in (ver.get("blockers") or []) + (ver.get("warnings") or []):
        text = item if isinstance(item, str) else \
               (item.get("msg") or item.get("text") or str(item))
        counts[_categorize_issue(str(text))] += 1
    return counts


_AUTOFIX_SCORE_FLOOR = 60
"""Verdict scores >= this number are considered shippable as-is — autofix
risks regressing other slides for marginal gain. Empirical: 2026-06-05 run
went from score=61 (after first build) to 43 after autofix retry, because
COPY_EDITOR touches every slide and breaks aesthetic balance on those
that weren't the target."""


def autofix_can_help(arts: dict[str, Any]) -> bool:
    """True iff autofix retry is likely to improve the verdict.

    COPY_EDITOR fixes ``text_overflow`` (shorten) and ``semantics``
    (rephrase to match topic). ``text_replaced`` (placeholder leak — build
    bug) and ``aesthetic`` (needs INFOGRAPHIC_MAKER) are out of scope.

    Three gates compose (all must pass to enter autofix):
      1. score < _AUTOFIX_SCORE_FLOOR — verdict is bad enough that the
         risk of regressing other slides is worth taking.
      2. at least one fixable category (text_overflow + semantics > 0).
      3. fixable categories are not dominated by unfixable ones — if
         aesthetic/text_replaced/other outnumber fixable 2:1, the
         feedback list is mostly noise to COPY_EDITOR and the retry
         tends to over-edit (2026-06-05 run regressed 11→13 warnings).
    """
    ver = arts.get("verifier_verdict") or {}
    score = int(ver.get("score_avg") or 0)
    if score >= _AUTOFIX_SCORE_FLOOR:
        return False
    b = issue_breakdown(arts)
    fixable = b["text_overflow"] + b["semantics"]
    if fixable == 0:
        return False
    unfixable = b["text_replaced"] + b["aesthetic"] + b["other"]
    if unfixable > 2 * fixable:
        return False
    return True


def _collect_verifier_feedback(arts: dict[str, Any]) -> list[str]:
    """Extract per-slide actionable issues for the autofix prompt.

    Pulls from ``verifier_verdict.warnings`` (already filtered for canonical
    noise in ``process_verify_node``) and ``visual_verdict.slides[].issues``
    so the copy editor sees both validate_plan-level and vision-level signal.
    """
    feedback: list[str] = []
    ver = arts.get("verifier_verdict") or {}
    for b in (ver.get("blockers") or []):
        feedback.append(f"BLOCKER: {b}")
    for w in (ver.get("warnings") or []):
        feedback.append(f"WARN: {w}")
    vis = arts.get("visual_verdict") or {}
    for sv in (vis.get("slides") or []):
        if sv.get("slide_verdict") in ("REJECT", "NEEDS_REWORK"):
            num = sv.get("num")
            for iss in (sv.get("issues") or []):
                rule = iss.get("rule") or ""
                msg = (iss.get("msg") or "")[:200]
                feedback.append(f"slide {num} ({rule}): {msg}")
    # Deduplicate while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for line in feedback:
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out


def autofix_node(state: SessionState) -> dict[str, Any]:
    """Single autofix pass.

    Reads verifier feedback, re-runs Agent 07 (Copy Editor) with the
    feedback baked into the user message, and re-strips emoji. The graph
    loops back to ``assemble_plan`` so build / brand / render / visual /
    process_verify all re-run with the updated content.

    Deterministic-only mutations (overflow size_pt, table demotion) are
    handled upstream in ``classify_node`` and ``design_node``; this node
    targets text-shaped issues that need LLM intervention.
    """
    arts = _artefacts(state)
    iteration = int(state.autofix_iterations or 0) + 1
    _emit(state, Stage.VALIDATING, pct=95,
          detail=f"автоисправление #{iteration}")

    feedback = _collect_verifier_feedback(arts)
    base_content = arts.get("copy_edited") or arts.get("content") or {}

    # Build the copy-editor prompt and append verifier feedback so the LLM
    # knows what to focus on. Keep the original SYSTEM rules intact — we
    # don't want the editor to start rewriting semantics, just to address
    # the specific complaints. If the feedback list is empty we fall back
    # to a plain re-run (still helpful: occasionally Copy Editor catches
    # things it missed first time).
    msgs = agent_07_copy_editor.build_messages(base_content)
    if feedback:
        bullet_list = "\n".join(f"- {line}" for line in feedback[:30])
        msgs.append({
            "role": "user",
            "content": (
                "Верификатор нашёл проблемы. Исправь только их, остальное оставь:\n"
                f"{bullet_list}\n\n"
                "ОСОБОЕ ВНИМАНИЕ: эмодзи (📤🔗📊🧠 и т.п.) в шрифте Cloud.ru "
                "отображаются как пустые квадраты — удаляй их полностью. "
                "Длинные строки сокращай, не теряя смысла."
            ),
        })

    edited, _ = call_and_parse(
        role=Role.COPY_EDITOR,
        messages=msgs,
        model_cls=_DeckContentAssignment,
    )
    edited_dump = edited.model_dump()
    emoji_stripped = _strip_emoji_from_content(edited_dump)
    _strip_page_refs_from_content(edited_dump)
    arts["copy_edited"] = edited_dump

    logger.info(
        "node.autofix.done",
        session_id=state.session_id,
        iteration=iteration,
        feedback_items=len(feedback),
        emoji_stripped=emoji_stripped,
        slides=len(edited.slides),
        breakdown=issue_breakdown(arts),
    )
    return {
        "artefacts": arts,
        "stage": Stage.VALIDATING.value,
        "progress_pct": 78,  # rewind progress to indicate the loop-back
        "autofix_iterations": iteration,
    }
