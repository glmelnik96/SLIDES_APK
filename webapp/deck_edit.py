"""Persist the browser-edited deck HTML and count its slides."""
from __future__ import annotations

import re
from pathlib import Path

from webapp.paths import session_dir

_SLIDE_RE = re.compile(r'<section[^>]*\bclass="[^"]*\bslide\b', re.IGNORECASE)


def count_slides(html: str) -> int:
    return len(_SLIDE_RE.findall(html))


def deck_path(session_id: str) -> Path:
    return session_dir(session_id) / "deck.html"


def save_deck(session_id: str, html: str) -> Path:
    if not html or not html.strip():
        raise ValueError("empty deck HTML")
    path = deck_path(session_id)
    path.write_text(html, encoding="utf-8")
    return path


def ensure_deck(session_id: str, source_path: str | None) -> Path | None:
    """Return the session's deck.html. If it doesn't exist yet, seed it by copying
    the engine's produced deck (source_path, an absolute path from the terminal
    `done` event). Returns None if there is no deck to serve."""
    p = deck_path(session_id)
    if p.is_file():
        return p
    if source_path and Path(source_path).is_file():
        p.write_text(Path(source_path).read_text(encoding="utf-8"), encoding="utf-8")
        return p
    return None
