"""Детерминированный рендер exact-режима: Section → html-фрагмент, InputDoc → DeckPlan.

Без LLM: текст переносим дословно, картинки встраиваем base64. Каждый слайд —
freeform=True с готовым html и меткой content["exact"]=True (ассемблер по ней
кладёт контент в .exact-zone и включает JS-подгонку масштаба).
"""
from __future__ import annotations

import base64
from html import escape

from ..models import DeckPlan, SlidePlan
from ..parsers.base import (CodeBlock, ImageBlock, InputDoc, ListBlock, Section,
                            TableBlock, TextBlock)

_RASTER = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}


def _img_html(block: ImageBlock) -> tuple[str, list[str]]:
    mime = (block.mime or "").lower()
    alt = escape(block.alt or "")
    if block.data and mime in _RASTER:
        b64 = base64.b64encode(block.data).decode("ascii")
        return f'<img class="exact-img" alt="{alt}" src="data:{mime};base64,{b64}">', []
    if block.src:                              # md: путь/URL
        return f'<img class="exact-img" alt="{alt}" src="{escape(block.src)}">', []
    fmt = block.mime or "неизвестен"
    return "", [f"картинка пропущена (формат {fmt} не поддержан)"]


def _block_html(block) -> tuple[str, list[str]]:
    if isinstance(block, TextBlock):
        return f'<p class="t-body-30">{escape(block.text).replace(chr(10), "<br>")}</p>', []
    if isinstance(block, ListBlock):
        tag = "ol" if block.ordered else "ul"
        items = "".join(f'<li class="t-body-30">{escape(it)}</li>' for it in block.items)
        return f'<{tag} class="exact-list">{items}</{tag}>', []
    if isinstance(block, TableBlock):
        rows = "".join(
            "<tr>" + "".join(f'<td class="t-body-30">{escape(c)}</td>' for c in row) + "</tr>"
            for row in block.rows)
        return f'<table class="exact-table">{rows}</table>', []
    if isinstance(block, ImageBlock):
        return _img_html(block)
    if isinstance(block, CodeBlock):
        return f'<pre class="exact-code">{escape(block.text)}</pre>', []
    return "", []


def build_exact_slide(section: Section) -> tuple[str, list[str]]:
    """Section → (html-фрагмент, предупреждения). html содержит .content-head
    (шапку ассемблер поднимет на уровень слайда) + тело; картинки — сбоку."""
    warnings: list[str] = []
    head = ""
    if section.heading:
        head = ('<div class="content-head"><h3 class="content-head-title t-head-42">'
                f'{escape(section.heading)}</h3></div>')
    text_parts: list[str] = []
    img_parts: list[str] = []
    for block in section.blocks:
        html, warns = _block_html(block)
        warnings.extend(warns)
        if not html:
            continue
        (img_parts if isinstance(block, ImageBlock) else text_parts).append(html)
    text_html = "".join(text_parts)
    if img_parts:
        body = ('<div class="exact-cols">'
                f'<div class="exact-text">{text_html}</div>'
                f'<div class="exact-media">{"".join(img_parts)}</div></div>')
    else:
        body = f'<div class="exact-text">{text_html}</div>'
    return head + body, warnings


def build_exact_plan(doc: InputDoc) -> tuple[DeckPlan, list[str]]:
    """InputDoc → (DeckPlan 1-в-1, предупреждения). N секций → N SlidePlan."""
    slides: list[SlidePlan] = []
    all_warnings: list[str] = []
    for i, section in enumerate(doc.sections, start=1):
        html, warns = build_exact_slide(section)
        all_warnings.extend(f"слайд {i}: {w}" for w in warns)
        slides.append(SlidePlan(index=i, type="exact", freeform=True,
                                content={"html": html, "exact": True}))
    return DeckPlan(title=doc.title, slides=slides), all_warnings
