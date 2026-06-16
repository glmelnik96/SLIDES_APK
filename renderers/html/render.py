"""HTML → PNG renderer for the HTML-render pipeline (Path B).

Headless Chromium (Playwright) rasterises one LLM-authored slide body at a time
against ``brand.css``. Fonts are loaded from ``docker/fonts/`` by rewriting the
relative ``url("SBSans*.otf")`` references to absolute ``file://`` URIs at render
time (Chromium will not load relative font URLs from an inline <style>).

Canvas is 1280×720 CSS px at deviceScaleFactor=2 → 2560×1440 PNG, matching the
16:9 slide and giving crisp text when packed full-bleed into a .pptx.

Pure back-end: imports no project code, mutates nothing outside the browser.
"""
from __future__ import annotations

import pathlib
import re

from playwright.sync_api import sync_playwright

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
_FONT_DIR = _REPO / "docker" / "fonts"

W, H, SCALE = 1280, 720, 2

_FONT_URL_RE = re.compile(r'url\("([^"]+\.otf)"\)')


def load_brand_css() -> str:
    """Brand stylesheet with font URLs absolutised to file:// so Chromium loads
    the bundled OTFs regardless of the page's own base URL."""
    css = (_HERE / "brand.css").read_text(encoding="utf-8")
    font_base = _FONT_DIR.as_uri()  # file:///.../docker/fonts

    def _repl(m: re.Match[str]) -> str:
        return f'url("{font_base}/{m.group(1)}")'

    return _FONT_URL_RE.sub(_repl, css)


def _doc(css: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<style>{css}</style></head><body>{body}</body></html>"
    )


def render_pages(bodies: list[str], css: str | None = None) -> list[bytes]:
    """Rasterise a list of slide-body HTML fragments to PNG bytes.

    One browser/page is reused across all slides for speed. Each body is the
    inner markup of a single ``<div class="slide ...">…</div>`` (no <html>/<head>).
    """
    if css is None:
        css = load_brand_css()
    out: list[bytes] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": W, "height": H}, device_scale_factor=SCALE,
        )
        for body in bodies:
            page.set_content(_doc(css, body), wait_until="networkidle")
            page.wait_for_timeout(200)  # let webfonts settle before the shot
            out.append(page.screenshot(
                clip={"x": 0, "y": 0, "width": W, "height": H},
            ))
        browser.close()
    return out


def render_one(body: str, css: str | None = None) -> bytes:
    """Convenience wrapper — rasterise a single slide body to PNG bytes."""
    return render_pages([body], css)[0]


class SlideRenderer:
    """Persistent Chromium page for interleaved render→QA→repair loops.

    Keeps one browser/page alive across many ``render()`` calls so per-slide
    repair re-renders don't pay browser startup each time. Use as a context
    manager::

        with SlideRenderer() as r:
            png = r.render(body)
    """

    def __init__(self, css: str | None = None) -> None:
        self._css = css if css is not None else load_brand_css()
        self._pw = None
        self._browser = None
        self._page = None

    def __enter__(self) -> "SlideRenderer":
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        self._page = self._browser.new_page(
            viewport={"width": W, "height": H}, device_scale_factor=SCALE,
        )
        return self

    def __exit__(self, *exc: object) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        finally:
            if self._pw is not None:
                self._pw.stop()
        self._pw = self._browser = self._page = None

    def render(self, body: str) -> bytes:
        assert self._page is not None, "SlideRenderer used outside `with` block"
        self._page.set_content(_doc(self._css, body), wait_until="networkidle")
        self._page.wait_for_timeout(200)  # let webfonts settle before the shot
        return self._page.screenshot(clip={"x": 0, "y": 0, "width": W, "height": H})
