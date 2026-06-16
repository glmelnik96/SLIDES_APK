#!/usr/bin/env python3
"""Map a PPTX typeface name (+bold) to a concrete SB Sans Display OTF file.

The Cloud.ru template uses the SB Sans Display family throughout (theme major
*and* minor fonts), with weight conveyed in the typeface string ("SB Sans
Display Semibold") or via the bold run-property. ``textfit`` needs the actual
font file to measure rendered width, so this resolver turns the donor's
declared typeface into a path under one of the known font directories:

  1. ``$SLIDESBOT_FONTS_DIR``      (explicit override)
  2. ``/usr/share/fonts/truetype/sbsans``  (baked into the worker image)
  3. ``<repo>/docker/fonts``       (host / unit tests)

Returns ``None`` whenever no usable file is found so callers can fall back to
the legacy char-count heuristics instead of crashing the build.
"""
from __future__ import annotations

import os
from functools import lru_cache

_REPO_FONTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "docker",
    "fonts",
)

_CANDIDATE_DIRS = (
    os.environ.get("SLIDESBOT_FONTS_DIR"),
    "/usr/share/fonts/truetype/sbsans",
    _REPO_FONTS,
)


@lru_cache(maxsize=1)
def _fonts_dir() -> str | None:
    for d in _CANDIDATE_DIRS:
        if d and os.path.isdir(d):
            return d
    return None


def _weight(family: str, bold: bool) -> str:
    """Pick an SB Sans Display weight from the typeface string / bold flag."""
    fam = (family or "").lower()
    if bold:
        return "Bold"
    if "semibold" in fam or "semi bold" in fam:
        return "SemiBold"
    if "medium" in fam:
        return "Medium"
    if "light" in fam:
        return "Light"
    if "thin" in fam:
        return "Thin"
    return "Regular"


@lru_cache(maxsize=64)
def resolve(family: str | None, bold: bool = False) -> str | None:
    """Return an OTF path for ``family``/``bold``, or ``None`` if unavailable."""
    d = _fonts_dir()
    if not d:
        return None
    cand = os.path.join(d, f"SBSansDisplay-{_weight(family, bold)}.otf")
    if os.path.isfile(cand):
        return cand
    fallback = os.path.join(d, "SBSansDisplay-Regular.otf")
    return fallback if os.path.isfile(fallback) else None
