"""Deterministic text re-grounding for the HTML pipeline (text-is-sacred guard).

The brief parser and classifier are LLMs: when they copy slide text into JSON
they occasionally drop characters («ВЗГЛЯД»→«ВЗГЛЯ»), fabricate KPI values
(116→15) or promote a page number to the title. The parse stage, however, is
deterministic python-pptx extraction — exact ground truth.

``snap_payload`` snaps every user-visible string in a composer payload back to
the closest grounded line when it is a near-but-not-exact match, fixes
numeric-only titles, and re-derives corrupted KPI values from the run order.
Pure-python, zero LLM calls — safe to run on every slide.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

from graph.designer.planner import _strip_pageref  # noqa: PLC2701 — read-only reuse

_SNAP_RATIO = 0.80  # below this a string is "different text", not a typo

_NUM_RE = re.compile(r"\d[\d\s.,%]*")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _grounded_lines(parsed_slide: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if parsed_slide.get("title"):
        lines.append(str(parsed_slide["title"]))
    for b in parsed_slide.get("body") or []:
        lines.append(str(b))
    for r in parsed_slide.get("text_runs") or []:
        lines.append(str(r))
    for t in parsed_slide.get("tables") or []:
        for h in t.get("headers") or []:
            lines.append(str(h))
        for row in t.get("rows") or []:
            lines.extend(str(c) for c in row)
    # Dedup, keep order, drop empties. Page-refs «(стр. N)» — including the
    # truncated «(стр.» tail — are navigation junk even when they sit in the
    # source deck verbatim; strip them so _snap never re-introduces a ref the
    # planner already removed (observed live 2026-06-10: s07/s14 leak).
    seen: set[str] = set()
    out = []
    for ln in lines:
        ln = _strip_pageref(ln.strip())
        key = _norm(ln)
        if ln and key not in seen:
            seen.add(key)
            out.append(ln)
    return out


def _snap(s: str, grounded: list[str]) -> str:
    """Return the grounded line closest to *s* if it's a near-match typo."""
    if not s or not grounded:
        return s
    n = _norm(s)
    for g in grounded:
        if _norm(g) == n:
            return s  # already verbatim (modulo whitespace/case)
    best, best_r = s, 0.0
    for g in grounded:
        r = difflib.SequenceMatcher(None, n, _norm(g)).ratio()
        if r > best_r:
            best, best_r = g, r
    return best if best_r >= _SNAP_RATIO else s


def _fix_numeric_title(payload: dict[str, Any],
                       parsed_slide: dict[str, Any]) -> None:
    """Page number leaked into title («5») → recover the real heading."""
    title = (payload.get("title") or "").strip()
    if title and not re.fullmatch(r"[\d\s.\-–—/]+", title):
        return  # title has letters — fine
    body: list[str] = payload.get("body") or []
    parsed_title = (parsed_slide.get("title") or "").strip()
    if parsed_title and not re.fullmatch(r"[\d\s.\-–—/]+", parsed_title):
        payload["title"] = parsed_title
        # If the recovered title also sits in body — drop the duplicate.
        payload["body"] = [b for b in body if _norm(b) != _norm(parsed_title)]
        return
    # Fallback: promote the first short heading-looking body line —
    # never a numeric-only line (that's the same leaked page number).
    for i, b in enumerate(body):
        bs = str(b).strip()
        if (bs and len(bs) <= 60 and not bs.endswith(('.', ';', ':'))
                and not re.fullmatch(r"[\d\s.\-–—/]+", bs)):
            payload["title"] = bs
            payload["body"] = body[:i] + body[i + 1:]
            return


def _grounded_numbers(grounded: list[str]) -> list[str]:
    nums: list[str] = []
    for ln in grounded:
        nums.extend(m.strip(" .,") for m in _NUM_RE.findall(ln))
    return [n for n in nums if n]


def _fix_kpi(kpi: dict[str, Any], grounded: list[str]) -> None:
    """Snap KPI labels; re-derive fabricated values from run adjacency."""
    numbers = kpi.get("numbers") or []
    gnums = _grounded_numbers(grounded)
    for item in numbers:
        if not isinstance(item, dict):
            continue
        if item.get("desc"):
            item["desc"] = _snap(str(item["desc"]), grounded)
        val = str(item.get("value") or "").strip()
        if not val:
            continue
        bare = val.rstrip("%+ ").strip()
        if any(bare == g or val == g for g in gnums):
            continue  # value exists verbatim on the slide
        # Fabricated value: find the grounded line containing the (snapped)
        # label and take the number adjacent to it in source order.
        desc_n = _norm(str(item.get("desc") or ""))
        if not desc_n:
            continue
        for idx, ln in enumerate(grounded):
            if _norm(ln) == desc_n or desc_n in _norm(ln):
                m = _NUM_RE.search(ln)
                if m:  # number embedded in the same line
                    item["value"] = m.group(0).strip(" .,")
                    break
                # Otherwise look at the nearest neighbouring numeric run.
                for j in (idx - 1, idx + 1):
                    if 0 <= j < len(grounded):
                        mm = _NUM_RE.fullmatch(grounded[j].strip())
                        if mm:
                            item["value"] = grounded[j].strip()
                            break
                break


def snap_payload(payload: dict[str, Any],
                 parsed_slide: dict[str, Any] | None) -> dict[str, Any]:
    """Re-ground all user-visible payload text. Mutates and returns payload."""
    if not parsed_slide:
        return payload
    grounded = _grounded_lines(parsed_slide)
    if not grounded:
        return payload

    _fix_numeric_title(payload, parsed_slide)
    if payload.get("title"):
        payload["title"] = _snap(str(payload["title"]), grounded)
    if payload.get("body"):
        # Numeric-only body lines are leaked page numbers, not content.
        payload["body"] = [
            _snap(str(b), grounded) for b in payload["body"]
            if not re.fullmatch(r"[\d\s.\-–—/]+", str(b).strip())
        ]
    if isinstance(payload.get("kpi"), dict):
        _fix_kpi(payload["kpi"], grounded)

    # Canon: a key_phrase that (near-)duplicates the slide title is never
    # rendered. Drop it deterministically — relying on the composer prompt
    # alone proved flaky (run2 s09: takeaway-dup overlapped the card grid).
    kp, title = _norm(str(payload.get("key_phrase") or "")), _norm(str(payload.get("title") or ""))
    if kp and title and (title in kp or
                         difflib.SequenceMatcher(None, kp, title).ratio() >= 0.80):
        payload["key_phrase"] = ""
    return payload
