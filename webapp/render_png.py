"""Render a saved deck.html to per-slide PNGs (1920x1080) and pack into a ZIP.

Reuses htmlslides' Playwright screenshotter. _screenshot_slides is an indirection
point so tests don't need Chromium.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Iterable

from webapp.deck_edit import count_slides


def _screenshot_slides(html_path, indices, out_dir, viewport=(1920, 1080)):
    from htmlslides.pipeline.screenshot import screenshot_slides
    return screenshot_slides(html_path, indices, out_dir, viewport=viewport)


def render_slides(deck_html: Path) -> dict[int, Path]:
    """Render every slide of the deck to a 1920x1080 PNG. Shared expensive step —
    PNG-ZIP and PPTX exports both reuse it (screenshotting drives Chromium)."""
    n = count_slides(deck_html.read_text("utf-8"))
    indices: Iterable[int] = range(1, n + 1)
    out_dir = deck_html.parent / "png"
    out_dir.mkdir(parents=True, exist_ok=True)
    return _screenshot_slides(deck_html, indices, out_dir, viewport=(1920, 1080))


def zip_pngs(pngs: dict[int, Path], out_zip: Path) -> Path:
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for index in sorted(pngs):
            zf.write(pngs[index], arcname=f"slide-{index:02d}.png")
    return out_zip


def export_zip(deck_html: Path, out_zip: Path) -> Path:
    return zip_pngs(render_slides(deck_html), out_zip)
