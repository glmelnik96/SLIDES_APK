"""Детерминированный рендер exact-режима: Section → html-фрагмент, InputDoc → DeckPlan.

Без LLM: текст переносим дословно, картинки встраиваем base64. Каждый слайд —
freeform=True с готовым html и меткой content["exact"]=True (ассемблер по ней
кладёт контент в .exact-zone и включает JS-подгонку масштаба).
"""
from __future__ import annotations

import base64
import re
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


# Число с единицей/суффиксом — сигнал «здесь данные, а не проза» (совпадает с
# planner._NUMERIC_TOKEN; дублируем, чтобы exact_builder оставался автономным).
_NUMERIC_TOKEN = re.compile(
    r"\d[\d.,]*\s*(?:%|млрд|млн|тыс|₽|руб|x|×|раз|ГБ|ТБ|TB|GB|Tier|ФЗ|сек|мс|ч|сут)",
    re.IGNORECASE)
_HERO_MAX = 120       # «короткий» тезис/число — для крупной подачи
_CARD_ITEM_MAX = 120  # пункт списка ещё «карточный», а не абзац


def _all_list_items(section: Section) -> list[str]:
    return [it for b in section.blocks if isinstance(b, ListBlock) for it in b.items]


def _plain_text(section: Section) -> str:
    parts: list[str] = []
    for block in section.blocks:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, ListBlock):
            parts.extend(block.items)
    return " ".join(parts)


def _choose_layout(section: Section) -> str:
    """Секция → имя flow-раскладки: cards | hero-number | statement | default.

    Порядок проб важен: таблицы и картинки уходят в безопасный default (текущий
    рендер), затем короткие списки → cards, короткий текст → числа/тезис.
    """
    blocks = section.blocks
    if any(isinstance(b, TableBlock) for b in blocks):
        return "default"
    if any(isinstance(b, ImageBlock) for b in blocks):
        return "default"
    items = _all_list_items(section)
    if 2 <= len(items) <= 6 and all(len(it) <= _CARD_ITEM_MAX for it in items):
        return "cards"
    text_blocks = [b for b in blocks if isinstance(b, TextBlock)]
    joined = _plain_text(section)
    if not items and 1 <= len(text_blocks) <= 2 and 0 < len(joined) <= _HERO_MAX:
        return "hero-number" if _NUMERIC_TOKEN.search(joined) else "statement"
    return "default"


def _head_html(section: Section) -> str:
    if not section.heading:
        return ""
    return ('<div class="content-head"><h3 class="content-head-title t-head-42">'
            f'{escape(section.heading)}</h3></div>')


def _build_default(section: Section) -> tuple[str, list[str]]:
    """Безопасный дефолт: .content-head + .exact-text (проза/списки/таблицы),
    картинки — сбоку в .exact-cols. Прежнее поведение build_exact_slide."""
    warnings: list[str] = []
    head = _head_html(section)
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


def _card_html(item: str) -> str:
    if " — " in item:
        name, desc = item.split(" — ", 1)
        inner = (f'<p class="t-head-36">{escape(name)}</p>'
                 f'<p class="t-body-30">{escape(desc)}</p>')
    else:
        inner = f'<p class="t-body-30">{escape(item)}</p>'
    return f'<div class="col"><div class="card">{inner}</div></div>'


def _build_cards(section: Section) -> tuple[str, list[str]]:
    """Список 2-6 пунктов → бренд-карточки в .row/.col по 3 в ряд. Не-списочный
    текст (вводный абзац) сохраняем дословно перед сеткой."""
    warnings: list[str] = []
    head = _head_html(section)
    intro_parts: list[str] = []
    for block in section.blocks:
        if isinstance(block, ListBlock):
            continue
        html, warns = _block_html(block)
        warnings.extend(warns)
        if html:
            intro_parts.append(html)
    intro = (f'<div class="exact-text">{"".join(intro_parts)}</div>'
             if intro_parts else "")
    cards = [_card_html(it) for it in _all_list_items(section)]
    rows = [cards[i:i + 3] for i in range(0, len(cards), 3)]
    grid = "".join(f'<div class="row">{"".join(row)}</div>' for row in rows)
    return head + intro + grid, warnings


def _build_hero_number(section: Section) -> tuple[str, list[str]]:
    """1-2 коротких текст-блока с числом-героем: число крупно (.t-number-320 для
    единственного числа, иначе .t-hero-156), подпись — .t-body-30. В один .row."""
    head = _head_html(section)
    blocks = [b for b in section.blocks if isinstance(b, TextBlock)]
    numeric_count = sum(1 for b in blocks if _NUMERIC_TOKEN.search(b.text))
    cols: list[str] = []
    for b in blocks:
        text = escape(b.text).replace(chr(10), "<br>")
        if _NUMERIC_TOKEN.search(b.text):
            cls = "t-number-320" if numeric_count == 1 else "t-hero-156"
        else:
            cls = "t-body-30"
        cols.append(f'<div class="col"><p class="{cls}">{text}</p></div>')
    body = f'<div class="row">{"".join(cols)}</div>'
    return head + body, []


def _build_statement(section: Section) -> tuple[str, list[str]]:
    """Короткий тезис (1-2 текст-блока без чисел) крупно в .t-hero-156. Каждый
    блок — своим абзацем (дословность per-block, переносы строк → <br>)."""
    head = _head_html(section)
    parts = [f'<p class="t-hero-156">{escape(b.text).replace(chr(10), "<br>")}</p>'
             for b in section.blocks if isinstance(b, TextBlock)]
    body = f'<div class="exact-text">{"".join(parts)}</div>'
    return head + body, []


def build_exact_slide(section: Section) -> tuple[str, list[str]]:
    """Section → (html-фрагмент, предупреждения). Диспетчер flow-раскладок:
    по _choose_layout выбираем бренд-строитель, иначе — безопасный default."""
    layout = _choose_layout(section)
    if layout == "cards":
        return _build_cards(section)
    if layout == "hero-number":
        return _build_hero_number(section)
    if layout == "statement":
        return _build_statement(section)
    return _build_default(section)


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
