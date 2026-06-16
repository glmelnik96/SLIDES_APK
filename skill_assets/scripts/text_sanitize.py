#!/usr/bin/env python3
"""text_sanitize.py — shared text cleaner for the PPTX write chokepoints.

Fixes two rendering artifacts observed in live decks (session 81673):

  A. ``_X000B_`` — a vertical-tab (\\x0b) inside a donor TITLE renders literally
     as the text ``_X000B_`` because python-pptx XML-escapes control chars.
  B. ``**bold**`` markdown leaks into donor body/title and renderer text — the
     model emits markdown bold that nothing strips.

``sanitize_text`` removes control characters (keeping \\n \\t \\r) and,
optionally, markdown emphasis markers.

CRITICAL INTERACTION — kpi_emphasis.py uses ``**…**`` as its OWN emphasis
markers (apply_kpi_emphasis bolds the span, then strips the ``**``). Any text
path that later feeds apply_kpi_emphasis MUST call sanitize_text with
``strip_markdown=False`` so the markers survive for kpi_emphasis to consume.
Donor body/title written via build_v5.replace_text_with_style is such a path.
Renderer text (flow/table/infographic/chart) and generic titles are leak-only
and use the default ``strip_markdown=True``.
"""
from __future__ import annotations

import re

# Control characters to strip. We keep \n (0x0a), \t (0x09), \r (0x0d) because
# they are legitimate text formatting. Everything else in the C0 range plus DEL
# is removed. NOTE: \x0b (vertical tab) and \x0c (form feed) ARE removed here —
# callers that want \x0b→\n line-break semantics (e.g. build_v5) perform that
# replace BEFORE calling sanitize_text, so by the time we run only stray control
# chars remain, and those should simply vanish (else python-pptx emits _X000B_).
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Conservative single-asterisk emphasis: ``*word*`` or ``*a few words*`` where
# the span is bounded by non-space on both inner edges. This avoids corrupting
# multiplication (``2*3``) or a leading bullet marker (``* item``) — both have a
# space (or string edge) on at least one inner side and so won't match.
_SINGLE_EMPHASIS_RE = re.compile(r"\*(\S[^*]*?\S|\S)\*")


def sanitize_text(text: str, *, strip_markdown: bool = True) -> str:
    """Clean text destined for a PPTX run.

    Args:
        text: the text to clean. Non-str / falsy input is returned unchanged.
        strip_markdown: when True (default) remove markdown bold/italic markers
            (``**`` globally, and ``*word*`` emphasis spans conservatively).
            Set False on any path that feeds kpi_emphasis.apply_kpi_emphasis,
            which consumes ``**…**`` itself — stripping early would break the
            intentional KPI/phrase emphasis. Control chars are stripped either
            way.

    Returns:
        Cleaned text. Preserves \\n, \\t, \\r and ordinary characters.
    """
    if not text or not isinstance(text, str):
        return text

    if strip_markdown:
        # ``**`` mirrors kpi_emphasis's own ``.replace("**", "")`` approach.
        text = text.replace("**", "")
        # Lone-asterisk emphasis only when it tightly wraps a word/phrase;
        # leaves math and bullet asterisks untouched (see regex comment).
        text = _SINGLE_EMPHASIS_RE.sub(r"\1", text)

    text = _CONTROL_CHARS_RE.sub("", text)
    return text
