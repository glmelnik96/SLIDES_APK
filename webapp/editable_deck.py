"""Open finished decks in the same editor without re-running the model.

New template slides carry their final slot values. Older/custom slides remain
HTML snapshots with editable text; never infer lossy template data from them.
"""
from __future__ import annotations

import hashlib
import base64
import json
import re

from lxml import html as lhtml

from webapp import deck_edit, draft
from webapp.paths import session_dir

SECTIONS = re.compile(r'<section\b[^>]*\bclass="[^"]*\bslide\b[^\"]*"[^>]*>.*?</section>', re.S | re.I)
_META = re.compile(r' data-editor-plan="([^"]*)"')
_MARKER = "<!--slides-editor-content-->"


def shell_path(session_id):
    return session_dir(session_id) / "editor-shell.html"


def import_html(session_id: str, source: str) -> draft.DraftPlan:
    matches = list(SECTIONS.finditer(source))
    if not matches:
        raise ValueError("В презентации не найдены слайды")
    doc = lhtml.fromstring(source)
    title = doc.findtext(".//title") or "Презентация"
    slides = []
    for match in matches:
        section = match.group()
        node = lhtml.fromstring(section)
        tid = node.get("data-template")
        meta = _META.search(section)
        content = None
        if meta and tid:
            try:
                payload = json.loads(base64.b64decode(meta.group(1), validate=True))
                original = _META.sub("", section, count=1)
                if hashlib.sha256(original.encode()).hexdigest() == payload["sha256"]:
                    from htmlslides.library import TemplateLibrary
                    tid = payload["template_id"]
                    if not TemplateLibrary.load().validate_content(tid, payload["content"]):
                        content = payload["content"]
            except (ValueError, KeyError, TypeError):
                pass  # stale/malformed metadata must not cost the visible content
        if content is not None:
            slides.append(draft.DraftSlide(template_id=tid, content=content, filled=True,
                                          brief=payload.get("brief", "")))
        else:
            slides.append(draft.DraftSlide(freeform=True, preserve_html=True,
                                          content={"html": section}, filled=True))
        # Diagrams already carry their data in legacy HTML, too. Restore the
        # existing node/edge form rather than exposing a JSON string as text.
        hosts = node.xpath('.//*[@data-diagram]')
        if tid == "diagram" and hosts:
            from webapp import slide_types
            try:
                fields = {"heading": "".join(node.xpath('.//*[@data-slot="title"]//text()')),
                          "subtitle": "".join(node.xpath('.//*[@data-slot="subtitle"]//text()')),
                          "diagram": json.loads(hosts[0].get("data-diagram"))}
                norm, errors = slide_types.validate_fields_verbose("diagram", fields)
                if norm is not None:
                    slides[-1] = draft.DraftSlide(template_id="diagram", slide_type="diagram",
                                                  fields=norm, filled=True)
            except (ValueError, TypeError):
                pass
    # Keep the document's CSS/scripts, including custom layouts from older builds.
    shell = source[:matches[0].start()] + _MARKER + source[matches[-1].end():]
    shell_path(session_id).write_text(shell, encoding="utf-8")
    return draft.DraftPlan(title=title, theme=deck_edit.get_theme(source), slides=slides)


def preserve_document(session_id: str, rendered: str) -> str:
    path = shell_path(session_id)
    if not path.is_file():
        return rendered
    shell = path.read_text("utf-8")
    sections = "\n".join(SECTIONS.findall(rendered))
    title = re.search(r"<title>.*?</title>", rendered, re.S)
    if title:
        shell = re.sub(r"<title>.*?</title>", lambda _: title.group(), shell, count=1, flags=re.S)
    return deck_edit.set_theme(shell.replace(_MARKER, sections), deck_edit.get_theme(rendered))
