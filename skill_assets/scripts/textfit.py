#!/usr/bin/env python3
"""Geometric text fitting via real font metrics (Pillow).

The legacy build-time fitters estimate width from character counts and the
longest word, which misses short-but-wide titles, over-shrinks Cyrillic, and
can't reason about block height at all. This module measures the *rendered*
width of each line with the actual brand OTF, simulates word-wrap against the
box's real geometry, and computes the wrapped block height — so the size we
pick reflects what LibreOffice/PowerPoint will actually draw.

``fit_text`` is the single entry point. It returns ``None`` on any failure
(missing Pillow, unreadable font, bad geometry) so the caller can fall back to
the heuristic fitters; the new path can therefore never render worse than the
old one.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

EMU_PER_PX = 9525            # python-pptx EMU per pixel at 96 DPI
PT_TO_PX = 96.0 / 72.0       # points -> pixels at 96 DPI
# LibreOffice renders SB Sans a touch wider than Pillow measures; nudge widths
# up so we shrink a hair early rather than clip at the edge. Calibrated against
# the Cloud.ru template cover/divider donors.
LO_WIDTH_CAL = 1.04
_LINE_SPACING = 1.20         # PowerPoint single line-spacing factor


@dataclass
class FitResult:
    size_pt: float
    lines: int
    anchor_middle: bool


@lru_cache(maxsize=256)
def _font(font_path: str, px: int):
    from PIL import ImageFont
    return ImageFont.truetype(font_path, max(1, px))


def _wrap_lines(text: str, font, max_w_px: float, wrap: bool) -> list[str]:
    """Greedy word-wrap honouring hard newlines. With ``wrap=False`` only hard
    newlines split (single-line-per-segment, e.g. KPI numbers)."""
    out: list[str] = []
    normalised = text.replace("\v", "\n").replace("\x0b", "\n")
    for segment in normalised.split("\n"):
        if not wrap:
            out.append(segment)
            continue
        words = segment.split()
        if not words:
            out.append("")
            continue
        cur = words[0]
        for w in words[1:]:
            if font.getlength(f"{cur} {w}") * LO_WIDTH_CAL <= max_w_px:
                cur = f"{cur} {w}"
            else:
                out.append(cur)
                cur = w
        out.append(cur)
    return out


def _measure(text: str, font_path: str, pt: float, max_w_px: float, wrap: bool):
    """Return (lines, longest_line_px, block_height_px) at ``pt``."""
    px = int(round(pt * PT_TO_PX))
    font = _font(font_path, px)
    lines = _wrap_lines(text, font, max_w_px, wrap)
    longest = max((font.getlength(ln) for ln in lines), default=0.0) * LO_WIDTH_CAL
    height = len(lines) * px * _LINE_SPACING
    return lines, longest, height


def fit_text(
    text: str,
    *,
    box_w_emu: int,
    box_h_emu: int | None,
    font_path: str,
    base_pt: float,
    min_pt: float,
    wrap: bool = True,
    balance: bool = False,
    width_target: float = 0.95,
    height_target: float = 0.92,
    balance_floor: float = 0.55,
) -> FitResult | None:
    """Largest size (<= ``base_pt``) whose wrapped text fits width and height.

    Overflow protection: shrink from ``base_pt`` toward ``min_pt`` until the
    longest wrapped line fits ``box_w`` and the block fits ``box_h``. If nothing
    fits down to ``min_pt`` we clamp at ``min_pt`` (better legible-and-tight
    than illegibly small).

    Soft balance: when ``balance`` is set and the text underfills the box
    (block height < ``balance_floor`` x box height), request vertical centring
    via ``anchor_middle`` — used for title/subtitle so short text sits centred
    instead of stuck to the top. Geometry is never moved.

    Returns ``None`` on any failure so the caller can fall back to heuristics.
    """
    try:
        text = str(text or "")
        if not text.strip():
            return None
        if not box_w_emu or box_w_emu <= 0:
            return None
        box_w_px = (box_w_emu / EMU_PER_PX) * width_target
        box_h_raw = (box_h_emu / EMU_PER_PX) if box_h_emu and box_h_emu > 0 else None
        box_h_limit = box_h_raw * height_target if box_h_raw else None
        base_pt = float(base_pt)
        min_pt = float(min_pt)

        def fits(pt: float) -> bool:
            _, longest, height = _measure(text, font_path, pt, box_w_px, wrap)
            if longest > box_w_px:
                return False
            if box_h_limit is not None and height > box_h_limit:
                return False
            return True

        pt = base_pt
        while pt > min_pt and not fits(pt):
            pt -= 1.0
        if pt < min_pt:
            pt = min_pt

        anchor_middle = False
        if balance and box_h_raw is not None:
            _, _, height = _measure(text, font_path, pt, box_w_px, wrap)
            if height < box_h_raw * balance_floor:
                anchor_middle = True

        lines, _, _ = _measure(text, font_path, pt, box_w_px, wrap)
        return FitResult(size_pt=round(pt, 1), lines=len(lines), anchor_middle=anchor_middle)
    except Exception:
        return None
