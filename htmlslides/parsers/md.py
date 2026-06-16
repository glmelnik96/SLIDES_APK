"""Markdown -> InputDoc: заголовки делят на секции, контент -> блоки."""
from __future__ import annotations

from markdown_it import MarkdownIt
from markdown_it.token import Token

from .base import (CodeBlock, ImageBlock, InputDoc, ListBlock, Section,
                   TableBlock, TextBlock)


def parse_md(text: str) -> InputDoc:
    tokens = MarkdownIt("commonmark").enable("table").parse(text)
    doc = InputDoc()
    section = Section()
    title_taken = False

    def flush() -> None:
        nonlocal section
        if section.heading or section.blocks:
            doc.sections.append(section)
        section = Section()

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == "heading_open":
            level = int(tok.tag[1])
            heading = _inline_text(tokens[i + 1]).strip()
            flush()
            if level == 1 and not title_taken:
                doc.title = heading
                title_taken = True
            else:
                section = Section(heading=heading, level=level)
            i += 3
            continue
        if tok.type == "paragraph_open":
            inline = tokens[i + 1]
            section.blocks.extend(_inline_images(inline))
            text_part = _inline_text(inline).strip()
            if text_part:
                section.blocks.append(TextBlock(text=text_part))
            i += 3
            continue
        if tok.type in ("bullet_list_open", "ordered_list_open"):
            items, i = _collect_list(tokens, i)
            section.blocks.append(
                ListBlock(items=items, ordered=tok.type == "ordered_list_open"))
            continue
        if tok.type == "fence":
            section.blocks.append(
                CodeBlock(text=tok.content.rstrip("\n"), lang=tok.info.strip()))
            i += 1
            continue
        if tok.type == "table_open":
            rows, i = _collect_table(tokens, i)
            section.blocks.append(TableBlock(rows=rows))
            continue
        i += 1
    flush()
    return doc


def _inline_text(inline: Token) -> str:
    parts: list[str] = []
    for child in inline.children or []:
        if child.type in ("text", "code_inline"):
            parts.append(child.content)
        elif child.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return "".join(parts)


def _inline_images(inline: Token) -> list[ImageBlock]:
    images: list[ImageBlock] = []
    for child in inline.children or []:
        if child.type == "image":
            images.append(ImageBlock(
                src=child.attrs.get("src"), alt=child.content))
    return images


def _collect_list(tokens: list[Token], start: int) -> tuple[list[str], int]:
    """Собрать все пункты списка (вложенные — плоско, по порядку документа).

    Абзацы одного loose-пункта склеиваются пробелом в один item.
    """
    depth = 0
    slots: list[list[str]] = []          # текстовые части каждого пункта
    stack: list[int] = []                # индексы открытых пунктов в slots
    j = start
    while j < len(tokens):
        t = tokens[j]
        if t.type in ("bullet_list_open", "ordered_list_open"):
            depth += 1
        elif t.type in ("bullet_list_close", "ordered_list_close"):
            depth -= 1
            if depth == 0:
                j += 1
                break
        elif t.type == "list_item_open":
            stack.append(len(slots))
            slots.append([])
        elif t.type == "list_item_close":
            stack.pop()
        elif t.type == "inline" and stack:
            item_text = _inline_text(t).strip()
            if item_text:
                slots[stack[-1]].append(item_text)
        j += 1
    items = [" ".join(parts) for parts in slots if parts]
    return items, j


def _collect_table(tokens: list[Token], start: int) -> tuple[list[list[str]], int]:
    rows: list[list[str]] = []
    row: list[str] = []
    j = start
    while j < len(tokens):
        t = tokens[j]
        if t.type == "tr_open":
            row = []
        elif t.type == "inline":
            row.append(_inline_text(t).strip())
        elif t.type == "tr_close":
            rows.append(row)
        elif t.type == "table_close":
            return rows, j + 1
        j += 1
    return rows, j
