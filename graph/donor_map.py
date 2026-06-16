"""Donor-slot-map helper — loads ``skill_assets/brand/donor-slot-map.yaml``
and exposes lookup helpers for the orchestration nodes.

``distribute_node`` needs per-layout slot capacities so GLM can fit copy
to safe_max_chars; ``assemble_plan_node`` needs a ph_idx → ph_name map
so PlanSlide.slots can use the canonical slot names ``build_v9`` keys on.

The YAML file is the single source of truth; we cache it on the first
call (worker process) and re-parse only on explicit ``reload()``.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from worker import skill_bridge


def _map_path() -> Path:
    return Path(skill_bridge.DONOR_SLOT_MAP)


# YAML semantic slot names → canonical OOXML PlaceholderType enum.
# Kept here (not imported from schemas/) to avoid a circular import: schemas
# is at the bottom of the dependency graph and graph.donor_map is one of
# its consumers. The two tables MUST stay in sync — see PlaceholderType in
# schemas/slides.py. _normalize_ph_type there is a defensive fallback for
# anything that slips past this translation.
_OOXML_BY_SLOT_NAME: dict[str, str] = {
    "title": "TITLE",
    "center_title": "CENTER_TITLE",
    "subtitle": "SUBTITLE",
    "body": "BODY",
    "content": "CONTENT",
    "picture": "PICTURE",
    "image": "PICTURE",
    "logo": "PICTURE",
}


def _slot_name_to_ooxml(slot_name: str) -> str:
    """Translate a YAML semantic slot key (e.g. ``col1_body``) into the
    canonical OOXML PlaceholderType enum (e.g. ``BODY``). Falls back to
    substring detection for multi-column variants (``col1_body``,
    ``body_left`` → BODY); anything unrecognised becomes ``OTHER``.
    """
    s = (slot_name or "").lower().strip()
    if s in _OOXML_BY_SLOT_NAME:
        return _OOXML_BY_SLOT_NAME[s]
    if "title" in s:
        return "TITLE"
    if "body" in s:
        return "BODY"
    if "content" in s:
        return "CONTENT"
    if "picture" in s or "image" in s or "logo" in s:
        return "PICTURE"
    return "OTHER"


@lru_cache(maxsize=1)
def _load_raw() -> dict[str, Any]:
    """Parse donor-slot-map.yaml and return the whole document.

    Donor IDs in the YAML are YAML integer keys; PyYAML returns them as
    ``int``. We keep that type so callers can lookup with the same
    ``layout_idx`` value that flows through LayoutPlan.
    """
    path = _map_path()
    if not path.is_file():
        raise FileNotFoundError(f"donor-slot-map missing: {path}")
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@lru_cache(maxsize=1)
def _load() -> dict[int, dict[str, Any]]:
    raw = _load_raw()
    donors = raw.get("donors") or {}
    return {int(k): v for k, v in donors.items() if v}


def reload() -> None:
    """Drop the cached map. Use in tests when the YAML changes on disk."""
    _load.cache_clear()
    _load_raw.cache_clear()


def valid_donor_ids() -> set[int]:
    """Set of donor indices that exist in donor-slot-map.yaml.

    Designer picks outside this set silently degrade to template default
    text (see stack_gotchas #10 — first live run had donors 1 and 9 chosen,
    which are template-internal meta-slides, not real donors). Use this
    set to reject hallucinated picks before they reach build_v9.
    """
    return set(_load().keys())


def category_equivalence() -> dict[str, list[int]]:
    """Return ``{yaml_category: [donor_idx, ...]}`` from the YAML.

    Empty lists are dropped (e.g. ``team`` has no usable donors in the
    current template — those are all photo-PNGs).
    """
    raw = _load_raw()
    out: dict[str, list[int]] = {}
    for cat, lst in (raw.get("category_equivalence") or {}).items():
        ids = [int(x) for x in (lst or []) if isinstance(x, int)]
        # Defensive: keep only donors that are actually mapped.
        ids = [i for i in ids if i in valid_donor_ids()]
        if ids:
            out[str(cat)] = ids
    return out


def tone_groups() -> dict[str, list[int]]:
    raw = _load_raw()
    out: dict[str, list[int]] = {}
    for grp, lst in (raw.get("tone_groups") or {}).items():
        ids = [int(x) for x in (lst or []) if isinstance(x, int)]
        ids = [i for i in ids if i in valid_donor_ids()]
        if ids:
            out[str(grp)] = ids
    return out


# Bridge between SlideCategory (schemas/slides.py) + subcategory_hint (free text
# from classifier) and the canonical categories in donor-slot-map.yaml. Keep the
# values strictly in sync with `category_equivalence` keys in the YAML.
_CATEGORY_BRIDGE: dict[str, list[str]] = {
    "title":       ["title_open", "title_dark"],
    "divider":     ["divider"],
    "text":        ["content_text"],
    "multicolumn": ["content_2col", "content_3col", "content_4block", "content_text"],
    "image":       ["image_grid", "image_main", "screenshot"],
    "team":        ["team"],
    "timeline":    ["timeline"],
    "table":       ["table"],
    "callout":     ["callout"],
    "logo":        ["logo_finale"],
    # pattern_bg/tech/other have no first-class mapping — fall back to content_text.
    "pattern_bg":  ["content_text"],
    "tech":        ["content_text"],
    "other":       ["content_text"],
}

# Subcategory hints from the classifier ("2col", "3col", "4subtitles", "6blocks",
# "8blocks", "kpi3") narrow multicolumn picks. The classifier emits these as
# free-form strings; we substring-match to stay resilient.
_SUBCAT_OVERRIDES: list[tuple[str, str]] = [
    ("2col",       "content_2col"),
    ("3col",       "content_3col"),
    ("4block",     "content_4block"),  # matches "4blocks" and "4block"
    ("4subtitle",  "content_4block"),
    ("6block",     "content_6subtitles"),  # only present if added to YAML
    ("8block",     "content_8subtitles"),
    ("kpi",        "kpi"),
]


def default_donor_for_category(
    category: str,
    subcategory_hint: str | None = None,
    dark: bool = False,
) -> int | None:
    """Pick a sensible donor index for a (category, subcategory_hint) pair.

    Returns the first valid donor index from the relevant
    ``category_equivalence`` bucket, or ``None`` if nothing in the YAML
    fits — caller should keep the LLM's pick or fall back to a safe text
    donor of its own choosing.

    Honours ``dark`` for title category (prefers ``title_dark`` first).
    """
    eq = category_equivalence()
    sub = (subcategory_hint or "").lower()
    # Subcategory hint wins if it matches a known multicolumn variant.
    for needle, yaml_key in _SUBCAT_OVERRIDES:
        if needle in sub and eq.get(yaml_key):
            return eq[yaml_key][0]
    buckets = list(_CATEGORY_BRIDGE.get(category, []))
    if category == "title" and dark:
        # Promote dark variant if requested.
        buckets = [b for b in buckets if b == "title_dark"] + \
                  [b for b in buckets if b != "title_dark"]
    for b in buckets:
        ids = eq.get(b)
        if ids:
            return ids[0]
    return None


# A3: when a slot has no explicit safe_max_chars, treat ~70% of the hard
# ceiling as the comfortable budget instead of handing the LLM the ceiling
# itself (which caused overflow/cramped text on slots missing the field).
# Mirrored as a literal in skill_assets/scripts/{validate_plan,build_v9}.py
# (vendored scripts are standalone and cannot import graph.*).
SAFE_MAX_FALLBACK_RATIO = 0.70


def effective_safe_max_chars(slot: dict[str, Any]) -> int | None:
    """Comfortable char budget for a slot: explicit ``safe_max_chars``,
    else ``int(0.70 * max_chars)``, else None."""
    safe = slot.get("safe_max_chars")
    if safe:
        return int(safe)
    hard = slot.get("max_chars")
    if hard:
        return int(SAFE_MAX_FALLBACK_RATIO * hard)
    return None


def donor_summary() -> list[dict[str, Any]]:
    """Compact per-donor record for the Layout Designer prompt.

    Returns a list of ``{idx, category, description, use_when, max_chars}``
    sorted by idx. Drives the dynamically-generated DONOR_TABLE in
    ``llm/prompts/agent_04_layout_designer.py`` — keep this small (one
    line per donor) so the prompt stays within context budget.
    """
    out: list[dict[str, Any]] = []
    for idx, donor in sorted(_load().items()):
        slots = donor.get("slots") or {}
        # Find the longest text slot's max_chars as a rough capacity hint.
        max_chars = 0
        for slot in slots.values():
            if isinstance(slot, dict):
                v = effective_safe_max_chars(slot) or 0
                if isinstance(v, int) and v > max_chars:
                    max_chars = v
        out.append({
            "idx": int(idx),
            "category": donor.get("category", ""),
            "description": donor.get("description", "")[:120],
            "use_when": (donor.get("use_when") or "")[:120],
            "max_chars": max_chars,
        })
    return out


def slot_specs_for_layouts(layout_idxs: list[int]) -> dict[str, list[dict[str, Any]]]:
    """Return ``{layout_idx_as_str: [slot_spec, ...]}`` for the requested donors.

    Each ``slot_spec`` is ``{ph_idx, ph_type, safe_max_chars}`` — the
    shape Agent 03 (Content Distributor) expects (see prompt). Donors
    not present in the YAML are skipped; the LLM tolerates missing
    entries (falls back to category heuristics).

    ``layout_idx == 0`` denotes native render (no donor) — also skipped.
    """
    donors = _load()
    out: dict[str, list[dict[str, Any]]] = {}
    for idx in layout_idxs:
        if not idx:  # 0 = native; falsy/None = unset
            continue
        donor = donors.get(int(idx))
        if donor is None:
            continue
        slots = donor.get("slots") or {}
        specs: list[dict[str, Any]] = []
        for slot_name, slot in slots.items():
            if not isinstance(slot, dict):
                continue
            # Translate the YAML's semantic slot name (title/col1_body/etc.)
            # into the OOXML PlaceholderType enum the schema expects. Without
            # this, the LLM mirrors lowercase back and Pydantic rejects every
            # placeholder_assignment (39× failure 2026-06-04 live).
            # schemas.slides._normalize_ph_type also catches edge cases.
            spec = {
                "ph_idx": slot.get("shape_idx"),
                "ph_type": _slot_name_to_ooxml(slot_name),
                "slot_name": slot_name,  # keep original for debug / future use
                "safe_max_chars": effective_safe_max_chars(slot),
            }
            # Drop slots without a shape_idx — meaningless to the distributor.
            if spec["ph_idx"] is None:
                continue
            specs.append(spec)
        if specs:
            out[str(int(idx))] = specs
    return out


def slot_name_by_ph_idx(layout_idx: int) -> dict[int, str]:
    """Return ``{ph_idx: slot_name}`` for a single donor.

    ``assemble_plan_node`` uses this to translate the ph_idx values
    produced by the Distributor into the canonical slot names
    ``build_v9`` expects in ``PlanSlide.slots``.
    """
    donors = _load()
    donor = donors.get(int(layout_idx))
    if donor is None:
        return {}
    slots = donor.get("slots") or {}
    out: dict[int, str] = {}
    for name, slot in slots.items():
        if isinstance(slot, dict) and slot.get("shape_idx") is not None:
            out[int(slot["shape_idx"])] = name
    return out


def body_ph_indices(layout_idx: int) -> set[int]:
    """Placeholder indices that map to body-type slots in a donor.

    Reuses ``_slot_name_to_ooxml`` (the same classifier the distributor
    trusts) so "body" capacity here matches what build_v9 actually fills.
    Native (``layout_idx == 0``) and unknown donors return an empty set.
    """
    if not layout_idx:
        return set()
    donor = _load().get(int(layout_idx))
    if donor is None:
        return set()
    out: set[int] = set()
    for name, slot in (donor.get("slots") or {}).items():
        if not isinstance(slot, dict):
            continue
        ph = slot.get("shape_idx")
        if ph is None:
            continue
        if _slot_name_to_ooxml(name) == "BODY":
            out.add(int(ph))
    return out


def body_slot_count(layout_idx: int) -> int:
    """Number of body-type slots a donor exposes (0 for native/unknown)."""
    return len(body_ph_indices(layout_idx))


_STEP_BODY_RE = re.compile(r"step\d+_body$")


def is_timeline_donor(layout_idx: int) -> bool:
    """True for variable-length roadmap donors (paired ``*_date`` +
    ``stepN_body`` slots, e.g. donor 60).

    Partial fill of such a donor is an intentional short roadmap, not a
    sparse flat slide, so the sparse detector exempts them.
    """
    if not layout_idx:
        return False
    donor = _load().get(int(layout_idx))
    if donor is None:
        return False
    names = list((donor.get("slots") or {}).keys())
    has_dates = any(str(n).endswith("_date") for n in names)
    has_steps = any(_STEP_BODY_RE.search(str(n)) for n in names)
    return has_dates and has_steps


def body_capacity(layout_idx: int) -> int:
    """Total comfortable char budget across the body-type slots of a donor.

    Sums ``effective_safe_max_chars`` over slots classified as BODY (same
    classifier the distributor trusts). Native (``0``) and unknown donors
    return 0.
    """
    if not layout_idx:
        return 0
    donor = _load().get(int(layout_idx))
    if donor is None:
        return 0
    total = 0
    for name, slot in (donor.get("slots") or {}).items():
        if not isinstance(slot, dict):
            continue
        if _slot_name_to_ooxml(name) != "BODY":
            continue
        total += effective_safe_max_chars(slot) or 0
    return total


# A4: donor capacity scoring. The Layout Designer picks by content TYPE and
# routinely sends text-heavy slides to small donors (content_text = 140
# comfortable chars). When the slide's raw volume exceeds
# CAPACITY_OVERLOAD_RATIO × capacity (the distributor can squeeze up to
# ~1.5×, beyond that it drops content or overflows), we deterministically
# swap the pick for the smallest content-family donor that fits.
CAPACITY_OVERLOAD_RATIO = 1.5

# Categories whose slides are body-volume driven and safe to re-route within
# the light content family. Visual/structural categories (title, divider,
# image, table, timeline, callout, …) are never touched — their capacity
# isn't body-driven and swapping changes the slide's nature.
_CAPACITY_UPGRADE_CATEGORIES = {"text", "multicolumn", "pattern_bg", "tech", "other"}

# Upgrade ladder: all light content donors, regardless of which bucket the
# original pick came from (content_text → 2col → 3col → 4/6/8 blocks).
_CONTENT_FAMILY_BUCKETS = [
    "content_text", "content_2col", "content_3col",
    "content_4block", "content_6subtitles", "content_8subtitles",
]


def upgrade_donor_for_volume(
    layout_idx: int,
    category: str,
    required_chars: int,
    dark: bool = False,
) -> int | None:
    """Return a higher-capacity content-family donor when *required_chars*
    badly overloads the current pick, else ``None`` (keep the pick).

    Dark slides are kept as-is: the content-family alternatives are all
    light donors and a capacity upgrade must not flip the slide's tone.
    """
    if dark or not required_chars:
        return None
    if category not in _CAPACITY_UPGRADE_CATEGORIES:
        return None
    current = body_capacity(layout_idx)
    if current and required_chars <= CAPACITY_OVERLOAD_RATIO * current:
        return None
    eq = category_equivalence()
    candidates: list[int] = []
    for bucket in _CONTENT_FAMILY_BUCKETS:
        for idx in eq.get(bucket, []):
            if idx not in candidates:
                candidates.append(idx)
    if not candidates:
        return None
    fitting = [i for i in candidates if body_capacity(i) >= required_chars]
    if fitting:
        best = min(fitting, key=lambda i: (body_capacity(i), i))
    else:
        best = max(candidates, key=lambda i: (body_capacity(i), -i))
    if best == int(layout_idx) or body_capacity(best) <= current:
        return None
    return best


def slot_max_chars(layout_idx: int, slot_name: str) -> int | None:
    """Physical ``max_chars`` capacity of a single donor slot, or ``None``
    when the donor/slot is unknown or carries no capacity hint.

    Body-recovery uses this to bound how much recovered text it may append
    to a column body slot (donor 28's columns are ``max_chars: 250``), so a
    pathological brief can never produce an off-slide wall of text.
    """
    if not layout_idx:
        return None
    donor = _load().get(int(layout_idx))
    if donor is None:
        return None
    slot = (donor.get("slots") or {}).get(slot_name)
    if not isinstance(slot, dict):
        return None
    v = slot.get("max_chars")
    return int(v) if isinstance(v, int) else None


__all__ = [
    "body_capacity",
    "upgrade_donor_for_volume",
    "slot_specs_for_layouts",
    "slot_name_by_ph_idx",
    "body_ph_indices",
    "body_slot_count",
    "is_timeline_donor",
    "slot_max_chars",
    "reload",
    "valid_donor_ids",
    "category_equivalence",
    "tone_groups",
    "default_donor_for_category",
    "donor_summary",
]
