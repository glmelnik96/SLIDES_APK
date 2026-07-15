"""Разрез текста/txt на пер-слайдовые Section (exact-режим): гибкое авто-распознавание.

Приоритет границ (первый сработавший уровень задаёт разрез):
1) явные метки «Слайд N:»;  2) строки-разделители «---»;
3) markdown-заголовки (#/##, верхний уровень);  4) весь текст = 1 слайд + подсказка.
Дословно, без переписывания. Заменяет строгую ошибку Этапа 1.
"""
from __future__ import annotations

import re

from .base import Section, TextBlock

# Метка в начале строки: «Слайд 1:» / «Слайд 1.» / регистр любой / табы-пробелы.
_MARKER = re.compile(r"^[ \t]*слайд[ \t]+\d+[ \t]*[:.]", re.IGNORECASE | re.MULTILINE)
# Горизонтальная черта: строка только из 3+ дефисов (табы/пробелы по краям допустимы).
_HR = re.compile(r"^[ \t]*-{3,}[ \t]*$", re.MULTILINE)
# Markdown-заголовок в начале строки: 1-6 решёток, пробел, непустой текст.
_MD_HEADING = re.compile(r"^(#{1,6})[ \t]+\S", re.MULTILINE)


def split_exact_text(raw: str, *, progress=lambda message: None) -> list[Section]:
    """Текст → список Section (дословно). Границы — по приоритету (см. модульный docstring).

    Нет ни одного разделителя → весь текст в один слайд + подсказка через progress
    (пустого результата и ошибки не бывает).
    """
    if _MARKER.search(raw):
        return _split_by_marker(raw)
    if _HR.search(raw):
        return _split_by_separators(raw, _HR)
    if _MD_HEADING.search(raw):
        return _split_by_markdown(raw)
    progress("подсказка: не нашёл границы слайдов — собрал всё в один слайд. "
             "Размечай их строками «Слайд 1:», «Слайд 2:» или «---», чтобы разбить.")
    return [_chunk_to_section(raw)]


def _split_by_marker(raw: str) -> list[Section]:
    matches = list(_MARKER.finditer(raw))
    sections: list[Section] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        sections.append(_chunk_to_section(raw[m.end():end]))
    return sections


def _split_by_separators(raw: str, sep: re.Pattern) -> list[Section]:
    chunks = [c for c in sep.split(raw) if c.strip()]
    return [_chunk_to_section(c) for c in chunks] or [_chunk_to_section(raw)]


def _split_by_markdown(raw: str) -> list[Section]:
    heads = list(_MD_HEADING.finditer(raw))
    top = min(len(m.group(1)) for m in heads)          # режем по верхнему уровню
    starts = [m.start() for m in heads if len(m.group(1)) == top]
    sections: list[Section] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(raw)
        chunk = re.sub(r"^#{1,6}[ \t]+", "", raw[start:end], count=1)  # снять решётки
        sections.append(_chunk_to_section(chunk))
    return sections


def _chunk_to_section(chunk: str) -> Section:
    lines = chunk.splitlines()
    heading = ""
    body_start = 0
    for idx, line in enumerate(lines):
        if line.strip():
            heading = line.strip()
            body_start = idx + 1
            break
    blocks: list[TextBlock] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            text = "\n".join(para).strip()
            if text:
                blocks.append(TextBlock(text=text))
            para.clear()

    for line in lines[body_start:]:
        if line.strip():
            para.append(line)
        else:
            flush()
    flush()
    return Section(heading=heading, blocks=blocks)
