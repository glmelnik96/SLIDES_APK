"""DOCX -> InputDoc: стили заголовков -> секции; абзацы/списки/таблицы/картинки.

Если стилями документ не размечен вовсе (типичный клиентский файл: всё `normal`,
заголовки набраны жирным/капсом, списки — символом «·» с клавиатуры), структуру
восстанавливаем по оформлению. См.
docs/superpowers/specs/2026-08-05-docx-structure-heuristics.md.
"""
from __future__ import annotations

import re
from pathlib import Path

import docx as docx_lib
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from .base import ImageBlock, InputDoc, ListBlock, Section, TableBlock, TextBlock

_HEADING_MAX_LEN = 90
_SENTENCE_END = ".,;:!?"
_BULLET_RE = re.compile(r"^[·•▪‣–—*-]\s+")


def parse_docx(path: str | Path) -> InputDoc:
    document = docx_lib.Document(str(path))
    # Эвристики — только для документов без разметки: там, где автор пользовался
    # стилями, жирный акцент внутри абзаца рвал бы раздел пополам.
    guess = not _has_explicit_structure(document)
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
        bullet = _bullet_item(text) if guess else None
        if bullet is not None:
            if items and items_ordered:
                flush_list()
            items_ordered = False
            items.append(bullet)
            continue
        listed = _is_list_paragraph(paragraph, style)
        # Нумерация Word заголовку не мешает: в реальном кейсе автор пронумеровал
        # автосписком только ПЕРВЫЙ заголовок, дальше набирал «2.», «3.» руками.
        # Но внутри списка верим только КАПСУ: жирный пункт списка — обычное дело.
        if guess and _looks_like_heading(paragraph, text) and (
                not listed or _is_caps(text)):
            # Уровень всегда 1: настоящей иерархии в таком документе нет, а
            # выдуманная соврала бы планировщику.
            flush_section()
            section = Section(heading=text, level=1)
            continue
        if listed:
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


def _has_explicit_structure(document) -> bool:
    """Размечен ли документ заголовками — стилем или уровнем структуры."""
    for paragraph in document.paragraphs:
        style = paragraph.style.name if paragraph.style is not None else ""
        if style == "Title" or style.startswith("Heading"):
            return True
        p_pr = paragraph._p.pPr
        if p_pr is not None and p_pr.find(qn("w:outlineLvl")) is not None:
            return True
    return False


def _looks_like_heading(paragraph: Paragraph, text: str) -> bool:
    """Короткая строка без завершающей пунктуации, выделенная жирным или капсом."""
    if not text or len(text) > _HEADING_MAX_LEN or text[-1] in _SENTENCE_END:
        return False
    return _all_bold(paragraph) or _is_caps(text)


def _all_bold(paragraph: Paragraph) -> bool:
    """Жирная ВСЯ строка. Жирное первое слово — акцент внутри абзаца, не заголовок."""
    runs = [r for r in paragraph.runs if r.text.strip()]
    return bool(runs) and all(r.bold for r in runs)


def _is_caps(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 2 and text == text.upper()


def _bullet_item(text: str) -> str | None:
    """Пункт списка, набранный маркером с клавиатуры (Word о нём ничего не знает)."""
    match = _BULLET_RE.match(text)
    if match is None:
        return None
    item = text[match.end():].strip()
    return item or None


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
