"""Раздел больше слайда → части размером в слайд. Без модели, текст дословно.

ЗАЧЕМ. Парсер делит документ на разделы только по заголовкам, а стеклянная сборка
делает слайд на раздел, так что документ без разметки превращался в титул и один
слайд, в бриф которого уходил весь текст (на проде — 3 из 7 текстовых загрузок за
14 дней). Замер на 88 деках (docs/superpowers/specs/2026-09-11-doc-preparation-
research.md): одним разделом до слайдов доходит 10% мыслей автора и 39% его чисел,
нарезкой по ~700 символов — 69% и 86%, вровень с разметкой моделью. С потолком
1500 было 49%: решает размер раздела, а не модель.

ЧТО РЕЖЕМ. Раздел, который не влезает в слайд (длиннее SLIDE_CHARS), и единственный
раздел документа длиннее CHUNK_CHARS — маленький документ без разметки иначе так и
остаётся одним слайдом. Размеченные разделы размером в слайд не трогаем, короткие не
склеиваем: на маленьком документе склейка «тонких» разделов роняла долю мыслей с 89%
до 56%. pptx сюда не попадает — там раздел это слайд автора (см. parse_file).

КАК. Единица — блок. Абзац — по строкам (в .txt строка — это заголовок или пункт, см.
parse_md(keep_lines)), строка длиннее LINE_CHARS — по фразам, фраза длиннее части —
по словам. Список — по пунктам, нумерация продолжается. Код, таблица и картинка
неделимы: разрезанный блок кода — это открытый ``` в одной части и закрытый в
другой. Части жадно набираются до CHUNK_CHARS. Заголовок части — начало её первой
фразы, а не «Часть N»: _section_to_text ставит заголовок первой строкой брифа, и
филлер берёт из неё название слайда.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .base import (Block, CodeBlock, ImageBlock, InputDoc, ListBlock, Section,
                   TableBlock, TextBlock)

#: Раздел длиннее этого на слайд не влезает.
SLIDE_CHARS = 1400
#: Размер части: 700 — по замеру (69% мыслей против 49% у 1500).
CHUNK_CHARS = 700
#: Строку длиннее этого режем по фразам.
LINE_CHARS = 300
#: Заголовок части — не длиннее этого, по границе слова.
HEADING_CHARS = 60

#: Граница фразы — знак конца и пробел после него: «99.95%» фразу не рвёт.
_SENTENCE = re.compile(r"(?<=[.!?…;])\s+")
_PHRASE_END = re.compile(r"[.!?…:;](?=\s|$)")


@dataclass
class _Unit:
    """Неделимый кусок раздела: строка или фраза абзаца, пункт списка, блок."""
    src: int                          # индекс блока в разделе
    size: int
    text: str = ""
    line: int = 0                     # номер строки абзаца: фразы одной строки
    #                                   склеиваются обратно пробелом, строки — переводом
    item: int = -1                    # номер пункта списка
    block: Optional[Block] = None     # код, таблица, картинка — как есть


def split_oversized(doc: InputDoc) -> InputDoc:
    """Документ с разделами размером в слайд. Новый объект; входной не меняется."""
    lone = sum(1 for s in doc.sections if s.blocks) == 1
    limit = CHUNK_CHARS if lone else SLIDE_CHARS
    sections: list[Section] = []
    for section in doc.sections:
        units = _units(section)
        if len(section.heading) + sum(u.size for u in units) <= limit:
            sections.append(section)
            continue
        chunks = _pack(units)
        if len(chunks) < 2:           # один неделимый блок — резать нечего
            sections.append(section)
            continue
        for k, chunk in enumerate(chunks):
            blocks = _rebuild(section, chunk)
            own = section.heading if k == 0 else ""
            sections.append(Section(
                heading=own or _first_phrase(blocks) or section.heading or "Раздел",
                level=section.level or 2,
                blocks=blocks,
                notes=section.notes if k == 0 else ""))
    return doc.model_copy(update={"sections": sections})


def _units(section: Section) -> list[_Unit]:
    units: list[_Unit] = []
    for b, block in enumerate(section.blocks):
        if isinstance(block, TextBlock):
            for n, line in enumerate(block.text.split("\n")):
                for piece in _pieces(line.strip()):
                    units.append(_Unit(b, len(piece) + 1, text=piece, line=n))
        elif isinstance(block, ListBlock):
            for k, item in enumerate(block.items):
                units.append(_Unit(b, len(item) + 3, text=item, item=k))
        else:
            units.append(_Unit(b, _block_size(block), block=block))
    return units


def _pieces(line: str) -> list[str]:
    """Строка → куски: длинная по фразам, фраза длиннее части — по словам."""
    if not line:
        return []
    if len(line) <= LINE_CHARS:
        return [line]
    out: list[str] = []
    for phrase in _SENTENCE.split(line):
        phrase = phrase.strip()
        while len(phrase) > CHUNK_CHARS:
            cut = phrase.rfind(" ", 0, CHUNK_CHARS)
            cut = cut if cut > 0 else CHUNK_CHARS
            out.append(phrase[:cut].strip())
            phrase = phrase[cut:].strip()
        if phrase:
            out.append(phrase)
    return out


def _block_size(block: Block) -> int:
    if isinstance(block, TableBlock):
        return sum(len(" | ".join(row)) + 4 for row in block.rows)
    if isinstance(block, CodeBlock):
        return len(block.text) + 8
    if isinstance(block, ImageBlock):
        return len(block.alt or block.src or "") + 14
    return 0


def _pack(units: list[_Unit]) -> list[list[_Unit]]:
    chunks: list[list[_Unit]] = []
    current: list[_Unit] = []
    size = 0
    for unit in units:
        if current and size + unit.size > CHUNK_CHARS:
            chunks.append(current)
            current, size = [], 0
        current.append(unit)
        size += unit.size
    if current:
        chunks.append(current)
    return chunks


def _rebuild(section: Section, chunk: list[_Unit]) -> list[Block]:
    """Куски части → блоки: соседние куски одного блока снова становятся одним."""
    blocks: list[Block] = []
    last: Optional[_Unit] = None
    for unit in chunk:
        source = section.blocks[unit.src]
        same = last is not None and last.src == unit.src and unit.block is None
        if unit.block is not None:
            blocks.append(unit.block)
        elif isinstance(source, ListBlock):
            if same:
                blocks[-1].items.append(unit.text)
            else:
                blocks.append(ListBlock(items=[unit.text], ordered=source.ordered,
                                        start=source.start + unit.item))
        elif same:
            glue = " " if last.line == unit.line else "\n"
            blocks[-1] = TextBlock(text=blocks[-1].text + glue + unit.text)
        else:
            blocks.append(TextBlock(text=unit.text))
        last = unit
    return blocks


def _first_phrase(blocks: list[Block]) -> str:
    """Начало первой фразы части — заголовок, из которого филлер возьмёт название."""
    for block in blocks:
        if isinstance(block, TextBlock):
            text = block.text.split("\n", 1)[0]
        elif isinstance(block, ListBlock):
            text = block.items[0] if block.items else ""
        elif isinstance(block, TableBlock):
            text = " · ".join(c for c in (block.rows[0] if block.rows else [])
                              if c.strip())
        else:
            continue
        text = " ".join(text.split())
        end = _PHRASE_END.search(text)
        if end and end.start() > 0:
            text = text[:end.start()]
        if len(text) > HEADING_CHARS:
            cut = text[:HEADING_CHARS]
            text = cut[:cut.rfind(" ")] if " " in cut else cut
        text = text.rstrip(" ,.;:—–-")
        if text:
            return text
    return ""
