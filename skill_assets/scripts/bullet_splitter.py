"""D7 (2026-06-05): split wall-of-text body slots into bullets.

Live run produced run1.slide5 with a 480-char single-paragraph BODY
slot. The donor renders that as one long line that overflows the
slot and gets clipped. Distributor prompt asks for "1 абзац = 1
буллет" but the LLM doesn't always split — and Copy Editor is
forbidden from semantic edits.

This module is a deterministic safety net: if a BODY/CONTENT slot's
content is one long paragraph above ``MAX_BULLET_CHARS``, split it at
sentence boundaries (.?!) into multiple short paragraphs that the
donor's bullet formatting will render as separate items. Pure plumbing
— no rewriting, just newlines.
"""
from __future__ import annotations

import re

# Paragraph above this length (chars, including spaces) becomes a split
# candidate. 220 fits two normal Russian sentences on a 14-16pt body slot.
MAX_BULLET_CHARS = 220

# Body-like slot names that should be split. Title/subtitle/captions are
# never split — they're inherently single-line typographic items.
_BODY_SLOT_NAMES = {
    "body", "content", "caption_body", "lead_body",
    "col1_body", "col2_body", "col3_body", "col4_body",
    "bullets", "list", "description",
}

# Sentence terminator + a following whitespace + capital letter / digit / quote.
_SENTENCE_BREAK_RE = re.compile(r"(?<=[\.\?\!…])\s+(?=[«\"A-ZА-ЯЁ0-9])")


def _is_body_slot(slot_name: str) -> bool:
    """Heuristic: body-ish names are split, the rest are left alone."""
    if not slot_name:
        return False
    name = slot_name.lower()
    if name in _BODY_SLOT_NAMES:
        return True
    # Catch col1_body / body_2 / lead-body etc.
    return "body" in name or "content" in name or name.startswith("bullet")


def split_long_bullet(text: str,
                      *,
                      max_chars: int = MAX_BULLET_CHARS) -> str:
    """Return text with paragraphs longer than max_chars split at sentence ends.

    Already-multi-paragraph text is untouched; each line is evaluated
    independently. Splitting falls back to no-op when no sentence break
    exists (e.g. one continuous run with no full-stops) — better to
    overflow than mangle the message.
    """
    if not text or len(text) <= max_chars:
        return text
    out_lines: list[str] = []
    for line in text.split("\n"):
        if len(line) <= max_chars:
            out_lines.append(line)
            continue
        # Find sentence breaks; if none, keep as-is (no signal where to split).
        pieces = _SENTENCE_BREAK_RE.split(line)
        if len(pieces) <= 1:
            out_lines.append(line)
            continue
        # Greedily pack sentences into buckets ≤ max_chars.
        bucket = ""
        for sent in pieces:
            sent = sent.strip()
            if not sent:
                continue
            if not bucket:
                bucket = sent
            elif len(bucket) + 1 + len(sent) <= max_chars:
                bucket = f"{bucket} {sent}"
            else:
                out_lines.append(bucket)
                bucket = sent
        if bucket:
            out_lines.append(bucket)
    return "\n".join(out_lines)


def split_slot_if_body(slot_name: str, text: str) -> str:
    """Conditional wrapper: split only when slot looks body-like."""
    if not _is_body_slot(slot_name):
        return text
    return split_long_bullet(text)
