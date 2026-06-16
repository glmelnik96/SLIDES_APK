"""DOCX -> InputDoc: стили заголовков -> секции; абзацы/списки/таблицы/картинки."""
from __future__ import annotations

from pathlib import Path

import docx as docx_lib
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from .base import ImageBlock, InputDoc, ListBlock, Section, TableBlock, TextBlock


def parse_docx(path: str | Path) -> InputDoc:
    document = docx_lib.Document(str(path))
    doc = InputDoc()
    section = Section()
    items: list[str] = []
    items_ordered = False

    def flush_list() -> None:
        nonlocal items
        if items:
            section.blocks.append(ListBlock(items=items, ordered=items_ordered))
            items = []

    def flush_section() -> None:
        nonlocal section
        flush_list()
        if section.heading or section.blocks:
            doc.sections.append(section)
        section = Section()

    for element in document.iter_inner_content():
        if isinstance(element, Table):
            flush_list()
            section.blocks.append(TableBlock(
                rows=[[c.text.strip() for c in r.cells] for r in element.rows]))
            continue
        paragraph: Paragraph = element
        style = paragraph.style.name if paragraph.style is not None else ""
        text = paragraph.text.strip()
        section.blocks.extend(_images(paragraph, document))
        if style == "Title":
            if not doc.title:
                doc.title = text
            continue
        if style.startswith("Heading"):
            flush_section()
            tail = style.split()[-1]
            level = int(tail) if tail.isdigit() else 1
            section = Section(heading=text, level=level)
            continue
        if _is_list_paragraph(paragraph, style):
            ordered = "Number" in style
            if items and ordered != items_ordered:
                flush_list()           # смежные bullet/number — разные списки
            items_ordered = ordered
            if text:
                items.append(text)
            continue
        flush_list()
        if text:
            section.blocks.append(TextBlock(text=text))
    flush_section()
    return doc


def _is_list_paragraph(paragraph: Paragraph, style: str) -> bool:
    if "List" in style:
        return True
    p_pr = paragraph._p.pPr
    return p_pr is not None and p_pr.numPr is not None


def _images(paragraph: Paragraph, document) -> list[ImageBlock]:
    blocks: list[ImageBlock] = []
    for blip in paragraph._p.iterfind(".//" + qn("a:blip")):
        rid = blip.get(qn("r:embed"))
        part = document.part.related_parts.get(rid) if rid else None
        if part is not None:
            blocks.append(ImageBlock(data=part.blob, mime=part.content_type))
    return blocks
