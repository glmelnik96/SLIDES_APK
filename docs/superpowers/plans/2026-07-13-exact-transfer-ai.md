# Точный перенос — Этап 2 (ИИ-дизайн): план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ИИ красиво верстает каждый exact-слайд в бренд-стиле Cloud.ru, но текст остаётся дословным за счёт протокола меток-заполнителей `{{aK}}` (слова подставляет программа, ИИ их не печатает).

**Architecture:** Между `build_exact_plan` (детерминированный html = фолбэк) и `polish_plan` вставляется шаг `design_exact_deck`: атомизируем `Section` → ИИ отдаёт брендовую вёрстку только с метками `{{aK}}` → проверки чистоты/полноты → программа подставляет дословный текст. Провал проверки/сети → остаётся html Этапа 1. Ассемблер не трогаем.

**Tech Stack:** Python 3.11, Pydantic v2, OpenAI SDK (Cloud.ru FM, MiniMax-M3), pytest. Интерпретатор — `.venv/bin/python`.

**Ветка:** всё в `skill-dev` (не `main`).

**Про коммиты:** шаги `commit` ниже — часть TDD-дисциплины плана. По правилу пользователя реальный `git commit`/`git push` делаются ТОЛЬКО по её слову «закоммить»; исполнитель готовит изменения и может складывать их в один коммит по команде. Не пушить без явной команды.

**Спека:** `docs/superpowers/specs/2026-07-13-exact-transfer-ai-design.md` (Этап 2), продолжает `2026-07-08-exact-transfer-design.md` (Этап 1, MVP уже в коде).

---

## Структура файлов

**Создаём:**
- `htmlslides/pipeline/exact_designer.py` — весь Этап 2: `Atom`, `atomize`, системный промпт `EXACT_DESIGN_SYSTEM`, рендер меток (`_render`/`_atom_html`/`_emphasize`), проверки (`_verify`), оркестратор `design_exact_deck`.
- `tests/test_exact_designer.py` — юнит-тесты нового модуля.

**Меняем:**
- `htmlslides/pipeline/client.py` — `KimiClient` копит `usage` (для экономии/замера).
- `htmlslides/parsers/exact_text.py` — гибкое авто-распознавание границ слайдов (заменяет жёсткую ошибку Этапа 1).
- `htmlslides/pipeline/build.py::_build_exact` — вставка шага `design_exact_deck` + ленивый клиент с фолбэком без ключа.
- `tests/test_exact_text.py` — правка под гибкие границы (было: «нет метки → ошибка»).
- `tests/test_build_exact.py` — правка под гибкие границы + оффлайн-детерминизм + путь с ИИ.
- `tests/test_app.py` — добавить тест накопления `usage` рядом с прочими тестами клиента.

**Переиспользуем без изменений:** `assembler.py` (exact-зона/fit уже готовы), `exact_builder.py` (`_block_html`, `build_exact_plan` как фолбэк), `parsers/pptx.py` (`parse_pptx`), `linter.ALLOWED_CLASSES`/`_is_allowed_class`, `filler._FORBIDDEN_FRAGMENT`/`_extract_html`/`_FILL_MAX_TOKENS`/`_FILL_NO_THINK`/`_TRANSIENT_API_ERRORS`.

**Порядок задач:** Task 1 (usage) и Task 2 (границы) независимы. Task 3→4→5→6 строят новый модуль. Task 7 подключает всё в build. Каждая задача оставляет зелёные тесты.

---

## Task 1: KimiClient копит usage (замер экономии)

**Files:**
- Modify: `htmlslides/pipeline/client.py` (init + `chat`)
- Test: `tests/test_app.py` (добавить одну функцию рядом с `test_default_model_is_minimax_m3`)

**Контекст для инженера:** сейчас `chat()` берёт `resp.choices[0].message.content` и **выбрасывает `resp.usage`** — мы не видим реальный расход токенов и попадания в кэш. `chat()` вызывается из нескольких потоков (`design_exact_deck`/`fill_deck` параллельны), поэтому копим суммарно под локом. Транспорты-моки без `usage` не должны ломаться — берём поля через `getattr`.

- [ ] **Step 1: Написать падающий тест**

Добавь в `tests/test_app.py`:

```python
def test_chat_accumulates_usage():
    """chat() должен собирать resp.usage в client.usage_total (для замера экономии)."""
    import htmlslides.pipeline.client as clientmod

    class _Usage:
        prompt_tokens = 100
        completion_tokens = 20
        class prompt_tokens_details:      # noqa: N801  (мок SDK-структуры)
            cached_tokens = 40

    class _Resp:
        class _C:
            class _M:
                content = "hi"
            message = _M()
        choices = [_C()]
        usage = _Usage()

    class _Transport:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    return _Resp()

    c = clientmod.KimiClient(rps=1000, transport=_Transport())
    c.chat([{"role": "user", "content": "x"}])
    c.chat([{"role": "user", "content": "y"}])
    assert c.usage_total == {
        "prompt_tokens": 200, "completion_tokens": 40,
        "cached_tokens": 80, "calls": 2}


def test_chat_without_usage_does_not_break():
    """Транспорт без .usage (старые моки) не должен падать; счётчики остаются нулевыми."""
    import htmlslides.pipeline.client as clientmod

    class _Resp:
        class _C:
            class _M:
                content = "ok"
            message = _M()
        choices = [_C()]

    class _Transport:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    return _Resp()

    c = clientmod.KimiClient(rps=1000, transport=_Transport())
    c.chat([{"role": "user", "content": "x"}])
    assert c.usage_total == {
        "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "calls": 1}
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_app.py::test_chat_accumulates_usage -v`
Expected: FAIL с `AttributeError: 'KimiClient' object has no attribute 'usage_total'`.

- [ ] **Step 3: Реализовать накопление usage**

В `htmlslides/pipeline/client.py`, в `KimiClient.__init__`, сразу ПОСЛЕ строки `self._gate = _RateGate(rps)` (до ветки `if transport is not None:`), добавь:

```python
        # usage копим суммарно и потокобезопасно: chat() зовут параллельно
        # (design_exact_deck/fill_deck). cached_tokens покажет, включился ли
        # кэш общего префикса промпта (главный рычаг экономии Этапа 2).
        self._usage_lock = threading.Lock()
        self.usage_total = {"prompt_tokens": 0, "completion_tokens": 0,
                            "cached_tokens": 0, "calls": 0}
```

Добавь метод в класс `KimiClient` (например, сразу перед `def chat`):

```python
    def _record_usage(self, resp) -> None:
        """Сложить usage ответа в self.usage_total. Моки без usage: копим только calls."""
        usage = getattr(resp, "usage", None)
        details = getattr(usage, "prompt_tokens_details", None) if usage is not None else None
        cached = getattr(details, "cached_tokens", 0) if details is not None else 0
        with self._usage_lock:
            if usage is not None:
                self.usage_total["prompt_tokens"] += getattr(usage, "prompt_tokens", 0) or 0
                self.usage_total["completion_tokens"] += (
                    getattr(usage, "completion_tokens", 0) or 0)
                self.usage_total["cached_tokens"] += cached or 0
            self.usage_total["calls"] += 1        # calls растёт всегда (даже без usage)
```

В методе `chat`, замени концовку:

```python
        with _INFLIGHT:
            resp = self._client.chat.completions.create(
                model=self.model, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
                extra_body=body or None)
        return resp.choices[0].message.content or ""
```

на:

```python
        with _INFLIGHT:
            resp = self._client.chat.completions.create(
                model=self.model, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
                extra_body=body or None)
        self._record_usage(resp)
        return resp.choices[0].message.content or ""
```

(`threading` уже импортирован в client.py.)

- [ ] **Step 4: Запустить тесты — зелёные**

Run: `.venv/bin/python -m pytest tests/test_app.py::test_chat_accumulates_usage tests/test_app.py::test_chat_without_usage_does_not_break -v`
Expected: 2 passed.

- [ ] **Step 5: Прогнать соседей (регресс клиента)**

Run: `.venv/bin/python -m pytest tests/test_app.py -k "usage or model or semaphore or inflight" -v`
Expected: все passed (существующие тесты клиента не сломаны транспортами без usage).

- [ ] **Step 6: Commit (по правилу пользователя — только по «закоммить»)**

```bash
git add htmlslides/pipeline/client.py tests/test_app.py
git commit -m "feat(exact): KimiClient копит usage для замера токенов"
```

---

## Task 2: Гибкое авто-распознавание границ слайдов

**Files:**
- Modify (перезапись): `htmlslides/parsers/exact_text.py`
- Test: `tests/test_exact_text.py` (правка)

**Контекст для инженера:** сейчас `split_exact_text` требует метку `Слайд N:` и иначе бросает `ExactMarkerError`. Этап 2 делает распознавание гибким по приоритету: `Слайд N:` → строки-разделители `---` → markdown-заголовки `#`/`##` → весь текст = 1 слайд + подсказка в `progress`. Ошибку больше не бросаем (класс оставляем для совместимости импорта). `_chunk_to_section` не меняем.

- [ ] **Step 1: Обновить тесты под новое поведение**

В `tests/test_exact_text.py` замени импорт и тест `test_no_marker_raises`, добавь тесты разделителей. Итоговый файл:

```python
from htmlslides.parsers.base import Section, TextBlock
from htmlslides.parsers.exact_text import split_exact_text


def test_split_two_slides_verbatim():
    raw = (
        "Слайд 1: Введение\n"
        "Первый абзац.\n"
        "\n"
        "Второй абзац.\n"
        "\n"
        "Слайд 2:\n"
        "Заголовок два\n"
        "Тело два.\n"
    )
    sections = split_exact_text(raw)
    assert len(sections) == 2
    assert isinstance(sections[0], Section)
    assert sections[0].heading == "Введение"
    assert [b.text for b in sections[0].blocks] == ["Первый абзац.", "Второй абзац."]
    assert sections[1].heading == "Заголовок два"
    assert sections[1].blocks[0].text == "Тело два."


def test_marker_lowercase_and_dot():
    raw = "слайд 1. Один\nтело\nслайд 2. Два\nтело2"
    sections = split_exact_text(raw)
    assert [s.heading for s in sections] == ["Один", "Два"]


def test_multiline_paragraph_kept_verbatim():
    raw = "Слайд 1: Т\nстрока A\nстрока B\n"
    sections = split_exact_text(raw)
    assert sections[0].blocks[0].text == "строка A\nстрока B"


def test_preamble_before_first_marker_ignored():
    raw = "Заголовок документа\nвступление\n\nСлайд 1: A\nтело a\n"
    sections = split_exact_text(raw)
    assert len(sections) == 1
    assert sections[0].heading == "A"
    assert all("Заголовок документа" not in b.text for b in sections[0].blocks)
    assert sections[0].blocks[0].text == "тело a"


def test_no_boundaries_single_slide_with_hint():
    """Нет разделителей → 1 слайд (не ошибка) + подсказка в progress."""
    msgs = []
    sections = split_exact_text("Просто текст без меток.\nЕщё строка.",
                                progress=msgs.append)
    assert len(sections) == 1
    assert sections[0].heading == "Просто текст без меток."
    assert sections[0].blocks[0].text == "Ещё строка."
    assert any("один слайд" in m.lower() or "границ" in m.lower() for m in msgs)


def test_split_by_horizontal_rule():
    raw = "A\nтело a\n---\nB\nтело b\n"
    sections = split_exact_text(raw)
    assert [s.heading for s in sections] == ["A", "B"]
    assert sections[0].blocks[0].text == "тело a"
    assert sections[1].blocks[0].text == "тело b"


def test_split_by_markdown_top_level_only():
    """Режем по верхнему уровню (#); подзаголовки (##) остаются внутри слайда."""
    raw = "# Один\nтело\n## под\nещё\n# Два\nтело2\n"
    sections = split_exact_text(raw)
    assert [s.heading for s in sections] == ["Один", "Два"]
    assert any("под" in b.text for b in sections[0].blocks)


def test_marker_wins_over_dashes():
    """Приоритет: если есть «Слайд N:», разделители --- внутри не режут слайд."""
    raw = "Слайд 1: A\nтекст\n---\nещё\nСлайд 2: B\nтекст2\n"
    sections = split_exact_text(raw)
    assert [s.heading for s in sections] == ["A", "B"]
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_exact_text.py -v`
Expected: FAIL — `test_no_boundaries_single_slide_with_hint` и `test_split_by_*` падают (сейчас `split_exact_text` бросает `ExactMarkerError` / не знает про `---`/`#`; `progress`-параметра нет).

- [ ] **Step 3: Переписать exact_text.py под гибкие границы**

Полностью замени содержимое `htmlslides/parsers/exact_text.py` на:

```python
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


class ExactMarkerError(ValueError):
    """Больше НЕ бросается (границы распознаются гибко). Оставлен для совместимости импорта."""


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
```

- [ ] **Step 4: Запустить — зелёные**

Run: `.venv/bin/python -m pytest tests/test_exact_text.py -v`
Expected: все passed (8 тестов).

- [ ] **Step 5: Commit**

```bash
git add htmlslides/parsers/exact_text.py tests/test_exact_text.py
git commit -m "feat(exact): гибкое авто-распознавание границ слайдов в тексте"
```

---

## Task 3: Новый модуль exact_designer — Atom + atomize

**Files:**
- Create: `htmlslides/pipeline/exact_designer.py`
- Test: `tests/test_exact_designer.py`

**Контекст для инженера:** `atomize` режет `Section` на «атомы» — минимальные куски дословного текста с id `a1..aN` (заголовок, если есть, всегда `a1`). Один атом = один `{{aK}}`. Структурные блоки (таблица/картинка/код) становятся одним атомом со ссылкой на исходный блок (их развернёт программа целиком, ИИ не переписывает).

- [ ] **Step 1: Написать падающий тест**

Создай `tests/test_exact_designer.py`:

```python
from htmlslides.parsers.base import (CodeBlock, ImageBlock, ListBlock, Section,
                                     TableBlock, TextBlock)
from htmlslides.pipeline.exact_designer import Atom, atomize


def test_atomize_heading_is_a1():
    atoms = atomize(Section(heading="Заголовок", blocks=[TextBlock(text="абзац")]))
    assert atoms[0] == Atom(id="a1", kind="heading", text="Заголовок")
    assert atoms[1].id == "a2"
    assert atoms[1].kind == "paragraph"
    assert atoms[1].text == "абзац"


def test_atomize_no_heading_starts_at_a1():
    atoms = atomize(Section(heading="", blocks=[TextBlock(text="первый")]))
    assert [a.id for a in atoms] == ["a1"]
    assert atoms[0].kind == "paragraph"
    assert atoms[0].text == "первый"


def test_atomize_list_items_are_separate_atoms():
    atoms = atomize(Section(heading="H", blocks=[
        ListBlock(items=["раз", "два", "три"])]))
    assert [a.kind for a in atoms] == ["heading", "list_item", "list_item", "list_item"]
    assert [a.text for a in atoms[1:]] == ["раз", "два", "три"]
    assert [a.id for a in atoms] == ["a1", "a2", "a3", "a4"]


def test_atomize_structural_blocks_keep_block_reference():
    table = TableBlock(rows=[["a", "b"]])
    image = ImageBlock(src="p.png", alt="pic")
    code = CodeBlock(text="x=1")
    atoms = atomize(Section(heading="", blocks=[table, image, code]))
    assert [a.kind for a in atoms] == ["table", "image", "code"]
    assert atoms[0].block is table
    assert atoms[1].block is image
    assert atoms[2].block is code


def test_atomize_verbatim_text_char_for_char():
    atoms = atomize(Section(heading="", blocks=[
        TextBlock(text="строка A\nстрока B")]))
    assert atoms[0].text == "строка A\nстрока B"
```

- [ ] **Step 2: Запустить — падает (модуля нет)**

Run: `.venv/bin/python -m pytest tests/test_exact_designer.py -v`
Expected: FAIL с `ModuleNotFoundError: No module named 'htmlslides.pipeline.exact_designer'`.

- [ ] **Step 3: Создать модуль с импортами, Atom и atomize**

Создай `htmlslides/pipeline/exact_designer.py`:

```python
"""Этап 2 точного переноса: ИИ-вёрстка exact-слайда с гарантией дословности.

Схема: Section → атомы (дословный текст) → ИИ отдаёт брендовую вёрстку ТОЛЬКО с
метками {{aK}} → проверки чистоты/полноты → программа подставляет дословный текст.
ИИ физически не печатает слова источника, поэтому исказить их не может. Провал
проверки/сети → остаётся детерминированный html Этапа 1 (build_exact_plan).
"""
from __future__ import annotations

import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from html import escape, unescape
from typing import Optional

from ..brand import brand_rules
from ..models import DeckPlan, SlidePlan
from ..parsers.base import (CodeBlock, ImageBlock, InputDoc, ListBlock, Section,
                            TableBlock, TextBlock)
from .exact_builder import _block_html
from .filler import (_FILL_MAX_TOKENS, _FILL_NO_THINK, _FORBIDDEN_FRAGMENT,
                     _TRANSIENT_API_ERRORS, _extract_html)
from .linter import ALLOWED_CLASSES, _is_allowed_class


@dataclass(frozen=True)
class Atom:
    """Минимальный дословный кусок слайда. block — исходный блок для структурных
    видов (table/image/code), которые программа разворачивает целиком."""
    id: str
    kind: str          # heading | paragraph | list_item | table | image | code
    text: str
    block: object = None


def atomize(section: Section) -> list[Atom]:
    """Section → список Atom (порядок сохраняем). Заголовок, если есть, всегда a1."""
    atoms: list[Atom] = []
    counter = 0

    def _next_id() -> str:
        nonlocal counter
        counter += 1
        return f"a{counter}"

    if section.heading:
        atoms.append(Atom(_next_id(), "heading", section.heading))
    for block in section.blocks:
        if isinstance(block, TextBlock):
            atoms.append(Atom(_next_id(), "paragraph", block.text))
        elif isinstance(block, ListBlock):
            for item in block.items:
                atoms.append(Atom(_next_id(), "list_item", item))
        elif isinstance(block, TableBlock):
            atoms.append(Atom(_next_id(), "table", "", block))
        elif isinstance(block, ImageBlock):
            atoms.append(Atom(_next_id(), "image", block.alt or "", block))
        elif isinstance(block, CodeBlock):
            atoms.append(Atom(_next_id(), "code", block.text, block))
    return atoms
```

- [ ] **Step 4: Запустить — зелёные**

Run: `.venv/bin/python -m pytest tests/test_exact_designer.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add htmlslides/pipeline/exact_designer.py tests/test_exact_designer.py
git commit -m "feat(exact): атомизация Section для ИИ-дизайна"
```

---

## Task 4: Рендер меток → дословный html

**Files:**
- Modify: `htmlslides/pipeline/exact_designer.py` (дописать в конец)
- Test: `tests/test_exact_designer.py` (дописать)

**Контекст для инженера:** `_render(raw_html, atoms)` заменяет каждую метку `{{aK}}` на HTML-escaped дословный текст атома. Диапазон `{{aK:i-j}}` = обернуть слова с i-го по j-е в `<b>` (1-based, включительно), сохраняя точные символы. Структурные атомы (table/image/code) разворачиваются через уже готовый `_block_html`. Абзацы: переводы строк → `<br>`. Регэксп метки — единый (`_MARK_RE`), группы: id, i, j.

- [ ] **Step 1: Написать падающий тест**

Дописать в `tests/test_exact_designer.py`:

```python
from htmlslides.pipeline.exact_designer import _render, _MARK_RE  # noqa: E402


def _atoms_from(section):
    from htmlslides.pipeline.exact_designer import atomize
    return atomize(section)


def test_render_substitutes_verbatim_escaped():
    atoms = _atoms_from(Section(heading="Заголовок", blocks=[
        TextBlock(text="a < b & c")]))
    html = _render('<h3>{{a1}}</h3><p>{{a2}}</p>', atoms)
    assert "<h3>Заголовок</h3>" in html
    assert "a &lt; b &amp; c" in html
    assert "{{a" not in html                      # все метки заменены


def test_render_paragraph_newlines_to_br():
    atoms = _atoms_from(Section(heading="", blocks=[
        TextBlock(text="строка A\nстрока B")]))
    html = _render("<p>{{a1}}</p>", atoms)
    assert "строка A<br>строка B" in html


def test_render_word_range_bolds_exact_words():
    atoms = _atoms_from(Section(heading="", blocks=[
        TextBlock(text="один два три четыре")]))
    html = _render("<p>{{a1:2-3}}</p>", atoms)
    assert html == "<p>один <b>два три</b> четыре</p>"


def test_render_word_range_single_word():
    atoms = _atoms_from(Section(heading="", blocks=[
        TextBlock(text="альфа бета")]))
    html = _render("<p>{{a1:1-1}}</p>", atoms)
    assert html == "<p><b>альфа</b> бета</p>"


def test_render_invalid_range_falls_back_to_plain_text():
    atoms = _atoms_from(Section(heading="", blocks=[
        TextBlock(text="одно слово-мало")]))
    html = _render("<p>{{a1:5-9}}</p>", atoms)          # слов меньше, чем 5
    assert "<b>" not in html
    assert "одно слово-мало" in html


def test_render_table_atom_expands_via_block_html():
    atoms = _atoms_from(Section(heading="", blocks=[
        TableBlock(rows=[["к1", "к2"]])]))
    html = _render("<div>{{a1}}</div>", atoms)
    assert "<table" in html and "к1" in html and "к2" in html


def test_mark_re_matches_plain_and_range():
    assert [m.group(1) for m in _MARK_RE.finditer("{{a1}} x {{a12:3-4}}")] == ["a1", "a12"]
```

- [ ] **Step 2: Запустить — падает**

Run: `.venv/bin/python -m pytest tests/test_exact_designer.py -k "render or mark_re" -v`
Expected: FAIL с `ImportError: cannot import name '_render'`.

- [ ] **Step 3: Реализовать рендер**

Дописать в конец `htmlslides/pipeline/exact_designer.py`:

```python
# Метка-заполнитель: {{aK}} либо {{aK:i-j}} (i,j — 1-based номера слов для <b>).
_MARK_RE = re.compile(r"\{\{\s*(a\d+)(?::(\d+)-(\d+))?\s*\}\}")


def _bold_word_range(text: str, i: int, j: int) -> str:
    """Escaped-текст с <b> вокруг слов i..j (1-based). Символы сохраняем дословно:
    <b> охватывает ровно эти слова и разделители между ними. Неверный диапазон →
    просто escaped-текст без акцента (текст важнее акцента)."""
    tokens = re.split(r"(\s+)", text)                  # чётные — слова, нечётные — пробелы
    word_positions = [k for k, tok in enumerate(tokens) if tok and not tok.isspace()]
    if i < 1 or j < i or j > len(word_positions):
        return escape(text)
    start, end = word_positions[i - 1], word_positions[j - 1]
    out: list[str] = []
    for k, tok in enumerate(tokens):
        piece = escape(tok)
        if k == start:
            piece = "<b>" + piece
        if k == end:
            piece = piece + "</b>"
        out.append(piece)
    return "".join(out)


def _emphasize(text: str, i: Optional[int], j: Optional[int], *, br: bool) -> str:
    html = escape(text) if i is None else _bold_word_range(text, i, j)
    return html.replace("\n", "<br>") if br else html


def _atom_html(atom: Atom, i: Optional[int], j: Optional[int]) -> str:
    """Один атом → дословный html. Структурные развернём через _block_html
    (диапазон слов к ним неприменим)."""
    if atom.kind in ("table", "image", "code"):
        html, _ = _block_html(atom.block)
        return html
    return _emphasize(atom.text, i, j, br=(atom.kind == "paragraph"))


def _render(raw_html: str, atoms: list[Atom]) -> str:
    """Заменить все метки {{aK}}/{{aK:i-j}} на дословный html атомов."""
    by_id = {a.id: a for a in atoms}

    def _sub(m: re.Match) -> str:
        atom = by_id[m.group(1)]                       # id гарантирован проверкой полноты
        i = int(m.group(2)) if m.group(2) else None
        j = int(m.group(3)) if m.group(3) else None
        return _atom_html(atom, i, j)

    return _MARK_RE.sub(_sub, raw_html)
```

- [ ] **Step 4: Запустить — зелёные**

Run: `.venv/bin/python -m pytest tests/test_exact_designer.py -k "render or mark_re" -v`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add htmlslides/pipeline/exact_designer.py tests/test_exact_designer.py
git commit -m "feat(exact): детерминированный рендер меток в дословный html"
```

---

## Task 5: Проверки дословности (_verify)

**Files:**
- Modify: `htmlslides/pipeline/exact_designer.py` (дописать)
- Test: `tests/test_exact_designer.py` (дописать)

**Контекст для инженера:** `_verify(raw_html, atoms)` возвращает строку-замечание для ретрая или `None`, если всё ок. Порядок проверок: (1) **полнота** — множество id в метках == все атомы, каждый ровно один раз; (2) **чистота** — сняв метки и теги и раскодировав HTML-сущности, в остатке нет ни одной буквы/цифры (иначе ИИ напечатал прозу); (3) запрещённые приёмы (`_FORBIDDEN_FRAGMENT`); (4) классы — только `_is_allowed_class`. Полнота раньше чистоты: кривая метка `{{a1` не матчится → ловится полнотой.

- [ ] **Step 1: Написать падающий тест**

Дописать в `tests/test_exact_designer.py`:

```python
from htmlslides.pipeline.exact_designer import _verify  # noqa: E402


def _two_atoms():
    return _atoms_from(Section(heading="Шапка", blocks=[TextBlock(text="тело")]))


def test_verify_ok_when_all_markers_present_and_clean():
    atoms = _two_atoms()
    raw = ('<section class="slide" data-template="freeform">'
           '<div class="content-head"><h3 class="content-head-title t-head-42">'
           '{{a1}}</h3></div><div class="row"><div class="col">{{a2}}</div></div></section>')
    assert _verify(raw, atoms) is None


def test_verify_fails_when_prose_typed_outside_markers():
    atoms = _two_atoms()
    raw = '<h3>{{a1}}</h3><p>лишний текст {{a2}}</p>'   # «лишний текст» — проза ИИ
    assert _verify(raw, atoms) is not None


def test_verify_fails_on_missing_atom():
    atoms = _two_atoms()
    raw = '<h3>{{a1}}</h3>'                              # a2 потерян
    assert _verify(raw, atoms) is not None


def test_verify_fails_on_duplicate_atom():
    atoms = _two_atoms()
    raw = '<h3>{{a1}}</h3><p>{{a2}}</p><p>{{a2}}</p>'    # a2 дважды
    assert _verify(raw, atoms) is not None


def test_verify_allows_word_range_marker_as_one_use():
    atoms = _two_atoms()
    raw = '<h3>{{a1}}</h3><p>{{a2:1-1}}</p>'             # a2 через диапазон — одно использование
    assert _verify(raw, atoms) is None


def test_verify_fails_on_forbidden_technique():
    atoms = _two_atoms()
    raw = '<h3>{{a1}}</h3><p style="x">{{a2}}</p>'       # style= запрещён
    assert _verify(raw, atoms) is not None


def test_verify_fails_on_unknown_class():
    atoms = _two_atoms()
    raw = '<h3>{{a1}}</h3><div class="totally-unknown">{{a2}}</div>'
    assert _verify(raw, atoms) is not None


def test_verify_ignores_html_entities_in_purity():
    """Сущности (&nbsp;) между метками не считаем прозой — иначе ложный провал."""
    atoms = _two_atoms()
    raw = '<h3>{{a1}}</h3><p>{{a2}}&nbsp;</p>'
    assert _verify(raw, atoms) is None
```

- [ ] **Step 2: Запустить — падает**

Run: `.venv/bin/python -m pytest tests/test_exact_designer.py -k verify -v`
Expected: FAIL с `ImportError: cannot import name '_verify'`.

- [ ] **Step 3: Реализовать проверки**

Дописать в конец `htmlslides/pipeline/exact_designer.py`:

```python
_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_CLASS_ATTR_RE = re.compile(r'class="([^"]*)"')
_ALNUM_RE = re.compile(r"[^\W_]")                       # любая буква/цифра (в т.ч. кириллица)


def _verify(raw_html: str, atoms: list[Atom]) -> Optional[str]:
    """None — ок; иначе строка-замечание для ретрая (см. порядок в docstring задачи)."""
    used = [m.group(1) for m in _MARK_RE.finditer(raw_html)]
    if Counter(used) != Counter(a.id for a in atoms):
        return ("Метки не совпадают с атомами: используй КАЖДУЮ {{aK}} ровно один раз "
                "и не выдумывай лишних.")
    stripped = _TAG_RE.sub("", _MARK_RE.sub("", raw_html))
    if _ALNUM_RE.search(unescape(stripped)):
        return ("Есть текст вне меток — печатать слова запрещено. Ставь ТОЛЬКО метки "
                "{{aK}}, весь текст подставит программа.")
    if _FORBIDDEN_FRAGMENT.search(raw_html):
        return ("Запрещённые приёмы (style= / i / em / u / script / тени / градиенты / "
                "скругления). Перепиши без них.")
    for attr in _CLASS_ATTR_RE.findall(raw_html):
        for name in attr.split():
            if not _is_allowed_class(name):
                return f"Неизвестный класс «{name}» — используй только разрешённые классы."
    return None
```

- [ ] **Step 4: Запустить — зелёные**

Run: `.venv/bin/python -m pytest tests/test_exact_designer.py -k verify -v`
Expected: 8 passed.

- [ ] **Step 5: Прогнать весь модульный файл**

Run: `.venv/bin/python -m pytest tests/test_exact_designer.py -v`
Expected: все passed (atomize + render + verify).

- [ ] **Step 6: Commit**

```bash
git add htmlslides/pipeline/exact_designer.py tests/test_exact_designer.py
git commit -m "feat(exact): проверки чистоты/полноты меток"
```

---

## Task 6: Промпт + _design_slide + оркестратор design_exact_deck

**Files:**
- Modify: `htmlslides/pipeline/exact_designer.py` (дописать)
- Test: `tests/test_exact_designer.py` (дописать)

**Контекст для инженера:** системный промпт `EXACT_DESIGN_SYSTEM` — родственник `FREEFORM_SYSTEM`, но задача иная: раскладывать метки `{{aK}}`, не печатая слов. **Важно:** промпт содержит литералы `{{a1}}` и подставляет список классов через `__CLASSES__` + `str.replace` (НЕ `.format`/f-string), иначе двойные скобки съедаются. `_design_slide` делает 1 вызов + при провале проверки 1 ретрай + фолбэк на исходный html слайда. `design_exact_deck` — параллельно по слайдам, мягкая деградация (как `fill_deck`), в конце лог `usage`. Сопоставление слайд↔секция — по порядку (оба 1-в-1).

- [ ] **Step 1: Написать падающий тест**

Дописать в `tests/test_exact_designer.py`:

```python
from htmlslides.models import DeckPlan, SlidePlan               # noqa: E402
from htmlslides.parsers.base import InputDoc                    # noqa: E402
from htmlslides.pipeline.exact_designer import (                # noqa: E402
    EXACT_DESIGN_SYSTEM, design_exact_deck)


class _FakeClient:
    """Мок клиента: возвращает заранее заданные ответы chat() по очереди."""
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0
        self.usage_total = {"prompt_tokens": 0, "completion_tokens": 0,
                            "cached_tokens": 0, "calls": 0}

    def chat(self, messages, *, max_tokens=4096, extra_body=None):
        self.calls += 1
        return self._replies.pop(0) if self._replies else self._replies_default()

    def _replies_default(self):
        raise AssertionError("chat вызван больше раз, чем задано ответов")


def _one_slide_doc_and_plan():
    doc = InputDoc(title="Дека", sections=[
        Section(heading="Шапка", blocks=[TextBlock(text="дословное тело")])])
    plan = DeckPlan(title="Дека", slides=[SlidePlan(
        index=1, type="exact", freeform=True,
        content={"html": '<div class="content-head">ФОЛБЭК</div>'
                          '<div class="exact-text">дословное тело</div>',
                 "exact": True})])
    return doc, plan


def test_design_system_prompt_is_plain_not_format():
    """Промпт держит литералы {{a1}} и плейсхолдер __CLASSES__ (иначе .format их съест)."""
    assert "{{a1}}" in EXACT_DESIGN_SYSTEM
    assert "__CLASSES__" in EXACT_DESIGN_SYSTEM


def test_design_success_produces_branded_verbatim_html():
    doc, plan = _one_slide_doc_and_plan()
    good = ('```html\n<section class="slide" data-template="freeform">'
            '<div class="content-head"><h3 class="content-head-title t-head-42">'
            '{{a1}}</h3></div><div class="row"><div class="col">{{a2}}</div>'
            '</div></section>\n```')
    client = _FakeClient([good])
    out = design_exact_deck(client, doc, plan, workers=1)
    html = out.slides[0].content["html"]
    assert 'class="row"' in html and 'class="col"' in html   # брендовая вёрстка
    assert "Шапка" in html and "дословное тело" in html      # дословный текст
    assert out.slides[0].content["exact"] is True
    assert client.calls == 1


def test_design_falls_back_when_model_types_prose():
    """ИИ «врёт» (печатает свой текст) оба раза → остаётся фолбэк-html, текст цел."""
    doc, plan = _one_slide_doc_and_plan()
    liar = ('```html<section data-template="freeform"><p>отсебятина {{a1}} {{a2}}</p>'
            '</section>```')
    client = _FakeClient([liar, liar])                       # ответ + ретрай
    out = design_exact_deck(client, doc, plan, workers=1)
    html = out.slides[0].content["html"]
    assert "ФОЛБЭК" in html                                  # исходный html Этапа 1
    assert "дословное тело" in html                          # дословность сохранена
    assert client.calls == 2                                 # была попытка + ретрай


def test_design_falls_back_when_atom_missing():
    doc, plan = _one_slide_doc_and_plan()
    missing = '```html<section data-template="freeform"><h3>{{a1}}</h3></section>```'
    client = _FakeClient([missing, missing])
    out = design_exact_deck(client, doc, plan, workers=1)
    assert "ФОЛБЭК" in out.slides[0].content["html"]


def test_design_transient_api_error_keeps_fallback():
    import httpx
    from openai import APIConnectionError

    doc, plan = _one_slide_doc_and_plan()

    class _Boom:
        usage_total = {}
        def chat(self, messages, *, max_tokens=4096, extra_body=None):
            raise APIConnectionError(request=httpx.Request("POST", "http://x"))

    out = design_exact_deck(_Boom(), doc, plan, workers=1)
    assert "ФОЛБЭК" in out.slides[0].content["html"]         # дека не упала


def test_design_retry_succeeds_second_try():
    doc, plan = _one_slide_doc_and_plan()
    bad = '```html<section><p>проза {{a1}} {{a2}}</p></section>```'
    good = ('```html<section data-template="freeform">'
            '<div class="content-head"><h3 class="content-head-title t-head-42">'
            '{{a1}}</h3></div><p class="t-body-30">{{a2}}</p></section>```')
    client = _FakeClient([bad, good])
    out = design_exact_deck(client, doc, plan, workers=1)
    assert "дословное тело" in out.slides[0].content["html"]
    assert 't-body-30' in out.slides[0].content["html"]
    assert client.calls == 2
```

- [ ] **Step 2: Запустить — падает**

Run: `.venv/bin/python -m pytest tests/test_exact_designer.py -k "design or system_prompt" -v`
Expected: FAIL с `ImportError: cannot import name 'EXACT_DESIGN_SYSTEM'`.

- [ ] **Step 3: Реализовать промпт, _design_slide и оркестратор**

Дописать в конец `htmlslides/pipeline/exact_designer.py`:

```python
# Бренд-правила: тот же источник, что у filler. Скобок {} в rules.md нет, но мы и
# так подставляем классы через .replace (не .format) — двойные {{ }} в промпте цели.
_BRAND_BLOCK = f"\n\nБРЕНД-ПРАВИЛА:\n{brand_rules()}"

# ВНИМАНИЕ: обычная (НЕ f/format) строка — {{aK}} остаются литералами; список
# классов подставляется через .replace("__CLASSES__", ...). Не переводить на .format!
EXACT_DESIGN_SYSTEM = ("""\
Ты — дизайнер-верстальщик ОДНОГО слайда презентации Cloud.ru. Текст менять НЕЛЬЗЯ:
слова даны как атомы, ты только КРАСИВО раскладываешь их по бренд-вёрстке.

Дано: список атомов слайда — метка {{aK}}, вид [kind] и текст (текст показан ТОЛЬКО
для понимания раскладки; печатать его запрещено).

Верни ТОЛЬКО код в блоке ```html ... ```.
Требования:
- Корень: <section class="slide" data-template="freeform"> ...контент... </section>.
- Текст ставь ТОЛЬКО метками {{aK}}. НЕ печатай слова из атомов. Каждый атом —
  РОВНО один раз.
- Если есть атом-заголовок (первый, вид [heading]) — ПЕРВЫМ элементом шапка:
  <div class="content-head"><h3 class="content-head-title t-head-42">{{a1}}</h3></div>.
- Дальше — ОБЫЧНЫМ ПОТОКОМ сверху вниз: колонки .row>.col, карточки .card,
  число-герой .t-number-320, крупный тезис .t-hero-156; максимум один .accent-block.
  Пункт вида «Имя — описание» удобно оформить карточкой (метку целиком в .card).
- Акценты БЕЗ печати слов: целый атом можно обернуть в .t-number-320/.t-hero-156/
  .accent-block или в <b> (например <b>{{a4}}</b>). Часть фразы — метка с диапазоном
  слов {{aK:i-j}} (жирными станут слова с i-го по j-е). Печатай только id и номера.
- Используй ТОЛЬКО классы: __CLASSES__.
- Запрещено: атрибут style, теги <i>/<em>/<u>/<script>, тени, градиенты, скругления,
  неизвестные классы. Контент должен умещаться в зону — лучше меньше и крупнее.""" + _BRAND_BLOCK)


def _atoms_prompt(atoms: list[Atom]) -> str:
    lines: list[str] = []
    for atom in atoms:
        if atom.kind in ("table", "image", "code"):
            desc = {"table": "таблица", "image": "картинка", "code": "код"}[atom.kind]
            lines.append(f"{{{{{atom.id}}}}} [{atom.kind}] — {desc} "
                         "(вставится целиком как есть)")
        else:
            lines.append(f"{{{{{atom.id}}}}} [{atom.kind}] {atom.text}")
    return ("Атомы этого слайда — используй КАЖДУЮ метку ровно один раз, "
            "сам текст не печатай:\n" + "\n".join(lines))


def _design_slide(client, section: Section, slide: SlidePlan) -> SlidePlan:
    """Один слайд: ИИ-вёрстка по протоколу меток. Провал проверки после ретрая →
    вернуть slide как есть (фолбэк-html Этапа 1). Дословность не страдает никогда."""
    atoms = atomize(section)
    if not atoms:
        return slide
    system = EXACT_DESIGN_SYSTEM.replace(
        "__CLASSES__", ", ".join(sorted(ALLOWED_CLASSES)))
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": _atoms_prompt(atoms)}]
    reply = client.chat(messages, max_tokens=_FILL_MAX_TOKENS, extra_body=_FILL_NO_THINK)
    raw = _extract_html(reply)
    problem = _verify(raw, atoms)
    if problem:
        retry = messages + [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": problem +
             " Верни ТОЛЬКО ```html-блок: та же брендовая вёрстка, но текст — только "
             "метками {{aK}}, каждый атом ровно один раз, без своих слов."}]
        reply = client.chat(retry, max_tokens=_FILL_MAX_TOKENS, extra_body=_FILL_NO_THINK)
        raw = _extract_html(reply)
        problem = _verify(raw, atoms)
        if problem:
            return slide
    designed = _render(raw, atoms)
    return slide.model_copy(update={"content": {"html": designed, "exact": True}})


def design_exact_deck(client, doc: InputDoc, plan: DeckPlan, *,
                      workers: int = 8, progress=lambda message: None) -> DeckPlan:
    """ИИ-вёрстка всех exact-слайдов параллельно. Сбой одного (проверка/сеть) не
    валит деку — остаётся его детерминированный html. Слайд↔секция по порядку."""
    sections = doc.sections
    total = len(plan.slides)
    done_lock = threading.Lock()
    done = 0

    def _tick() -> None:
        nonlocal done
        with done_lock:
            done += 1
            k = done
        progress(f"design: слайд {k}/{total}")

    def one(pos: int, slide: SlidePlan) -> SlidePlan:
        if pos >= len(sections):
            return slide
        try:
            result = _design_slide(client, sections[pos], slide)
        except _TRANSIENT_API_ERRORS as exc:
            progress(f"warn: слайд {slide.index} — сбой API ({type(exc).__name__}); "
                     "оставляю простой вид (как Этап 1)")
            result = slide
        _tick()
        return result

    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(one, pos, s) for pos, s in enumerate(plan.slides)]
        slides = [f.result() for f in futures]
    finally:
        pool.shutdown()
    if getattr(client, "usage_total", None):
        progress(f"design: токены {client.usage_total}")
    return plan.model_copy(update={"slides": slides})
```

- [ ] **Step 4: Запустить — зелёные**

Run: `.venv/bin/python -m pytest tests/test_exact_designer.py -k "design or system_prompt" -v`
Expected: 6 passed.

- [ ] **Step 5: Прогнать весь модульный файл**

Run: `.venv/bin/python -m pytest tests/test_exact_designer.py -v`
Expected: все passed.

- [ ] **Step 6: Commit**

```bash
git add htmlslides/pipeline/exact_designer.py tests/test_exact_designer.py
git commit -m "feat(exact): промпт, ретрай и оркестратор design_exact_deck"
```

---

## Task 7: Подключение в build.py + обновление интеграционных тестов

**Files:**
- Modify: `htmlslides/pipeline/build.py` (`_build_exact` + новый `_exact_client_or_none`)
- Test: `tests/test_build_exact.py` (правка + новые тесты)

**Контекст для инженера:** `_build_exact` сейчас: parse → `build_exact_plan` → `polish_plan`. Вставляем шаг `design_exact_deck` между планом и `polish_plan`, но только если есть клиент. Клиент строим лениво: нет ключа (`RuntimeError` из `KimiClient()`) → `None` + warn, дека собирается детерминированно (как Этап 1). `split_exact_text` теперь принимает `progress`. Оффлайн-тесты фиксируют отсутствие ключа через `monkeypatch.delenv`, чтобы не зависеть от окружения.

- [ ] **Step 1: Обновить и дополнить тесты**

Замени содержимое `tests/test_build_exact.py` на:

```python
import pytest

from htmlslides.pipeline.build import build_deck


def _write_text_deck(path):
    path.write_text(
        "Слайд 1: Первый\n"
        "Дословный текст один.\n"
        "\n"
        "Слайд 2: Второй\n"
        "Дословный текст два.\n",
        encoding="utf-8")
    return path


def test_build_exact_from_text_offline(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOUDRU_API_KEY", raising=False)     # нет ключа → без ИИ
    src = _write_text_deck(tmp_path / "deck.txt")
    out = tmp_path / "deck.html"
    result = build_deck(src, out, mode="exact")
    assert result == out
    html = out.read_text(encoding="utf-8")
    assert html.count('data-template="exact"') == 2
    assert "Дословный текст один." in html
    assert "Дословный текст два." in html


def test_build_exact_light_theme(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOUDRU_API_KEY", raising=False)
    src = _write_text_deck(tmp_path / "deck.txt")
    out = tmp_path / "deck.html"
    build_deck(src, out, mode="exact", theme="light")
    assert 'data-theme="light"' in out.read_text(encoding="utf-8")


def test_build_exact_no_marker_single_slide(tmp_path, monkeypatch):
    """Текст без разделителей → 1 слайд (не ошибка), дословно."""
    monkeypatch.delenv("CLOUDRU_API_KEY", raising=False)
    src = tmp_path / "bad.txt"
    src.write_text("текст без меток слайдов", encoding="utf-8")
    out = tmp_path / "o.html"
    build_deck(src, out, mode="exact")
    html = out.read_text(encoding="utf-8")
    assert html.count('data-template="exact"') == 1
    assert "текст без меток слайдов" in html


def test_build_exact_docx_rejected(tmp_path):
    src = tmp_path / "x.docx"
    src.write_bytes(b"PK\x03\x04stub")
    with pytest.raises(ValueError):
        build_deck(src, tmp_path / "o.html", mode="exact")


def test_build_exact_no_key_skips_ai(tmp_path, monkeypatch):
    """Без ключа design_exact_deck НЕ зовётся — детерминированный результат."""
    monkeypatch.delenv("CLOUDRU_API_KEY", raising=False)

    import htmlslides.pipeline.exact_designer as designer

    def _boom(*a, **k):
        raise AssertionError("ИИ не должен вызываться без ключа")

    monkeypatch.setattr(designer, "design_exact_deck", _boom)
    src = _write_text_deck(tmp_path / "deck.txt")
    out = tmp_path / "deck.html"
    build_deck(src, out, mode="exact")                       # не падает
    assert out.read_text(encoding="utf-8").count('data-template="exact"') == 2


def test_build_exact_designs_with_client(tmp_path, monkeypatch):
    """Есть клиент → слайды проходят через ИИ-вёрстку; текст остаётся дословным."""
    import htmlslides.pipeline.build as buildmod

    good = ('```html<section class="slide" data-template="freeform">'
            '<div class="content-head"><h3 class="content-head-title t-head-42">'
            '{{a1}}</h3></div><div class="row"><div class="col">{{a2}}</div>'
            '</div></section>```')

    class _FakeClient:
        model = "fake"
        usage_total = {}
        def chat(self, messages, *, max_tokens=4096, extra_body=None):
            return good

    monkeypatch.setattr(buildmod, "_exact_client_or_none",
                        lambda progress: _FakeClient())
    src = _write_text_deck(tmp_path / "deck.txt")
    out = tmp_path / "deck.html"
    build_deck(src, out, mode="exact")
    html = out.read_text(encoding="utf-8")
    assert 'class="col"' in html                             # брендовая вёрстка от ИИ
    assert "Дословный текст один." in html                   # дословность
    assert html.count('data-template="exact"') == 2
```

- [ ] **Step 2: Запустить — падает**

Run: `.venv/bin/python -m pytest tests/test_build_exact.py -v`
Expected: FAIL — `test_build_exact_no_marker_single_slide` (сейчас бросается ValueError), `test_build_exact_designs_with_client` (нет `_exact_client_or_none`).

- [ ] **Step 3: Подключить design_exact_deck в _build_exact**

В `htmlslides/pipeline/build.py` замени тело функции `_build_exact` (строки от `progress(f"plan: ...")` до `return polish_plan(...)`), а также передай `progress` в `split_exact_text`.

Найди:

```python
        raw = src.read_text(encoding="utf-8")
        doc = InputDoc(title=src.stem, sections=split_exact_text(raw))
```

замени на:

```python
        raw = src.read_text(encoding="utf-8")
        doc = InputDoc(title=src.stem,
                       sections=split_exact_text(raw, progress=progress))
```

Найди концовку функции:

```python
    progress(f"plan: {len(doc.sections)} слайдов 1-в-1 (без ИИ)")
    plan, warnings = build_exact_plan(doc)
    for w in warnings:
        progress(f"warn: {w}")
    # polish без vision/autofix: только assemble (ИИ не зовётся, ключ не нужен).
    return polish_plan(plan, out, theme=theme, vision=False, max_autofix=0,
                       progress=progress)
```

замени на:

```python
    progress(f"plan: {len(doc.sections)} слайдов 1-в-1")
    plan, warnings = build_exact_plan(doc)
    for w in warnings:
        progress(f"warn: {w}")

    # Этап 2: ИИ-вёрстка каждого слайда по протоколу меток. Нет ключа → пропускаем,
    # остаётся детерминированный html Этапа 1 (дека собирается всегда).
    from .exact_designer import design_exact_deck
    client = _exact_client_or_none(progress)
    if client is not None:
        progress("design: ИИ-вёрстка exact-слайдов")
        plan = design_exact_deck(client, doc, plan, progress=progress)

    # polish без vision/autofix: только assemble.
    return polish_plan(plan, out, theme=theme, vision=False, max_autofix=0,
                       progress=progress)
```

Добавь новую функцию сразу ПОСЛЕ `_build_exact` (перед `def polish_plan`):

```python
def _exact_client_or_none(progress: Progress) -> Optional[KimiClient]:
    """Клиент для ИИ-вёрстки exact-слайдов; нет ключа → None (собираем как Этап 1)."""
    try:
        return KimiClient()
    except RuntimeError as exc:
        progress(f"warn: нет ключа к ИИ ({exc}); точный перенос без дизайна (как Этап 1)")
        return None
```

(`KimiClient`, `Optional`, `Progress` уже импортированы в build.py.)

- [ ] **Step 4: Запустить — зелёные**

Run: `.venv/bin/python -m pytest tests/test_build_exact.py -v`
Expected: 6 passed.

- [ ] **Step 5: Полный прогон затронутого (регресс)**

Run: `.venv/bin/python -m pytest tests/test_build_exact.py tests/test_exact_text.py tests/test_exact_designer.py tests/test_exact_builder.py tests/test_assembler_exact.py tests/test_exact_plumbing.py tests/test_filler.py -v`
Expected: все passed (Этап 1 не сломан, Этап 2 зелёный).

- [ ] **Step 6: Commit**

```bash
git add htmlslides/pipeline/build.py tests/test_build_exact.py
git commit -m "feat(exact): включить ИИ-вёрстку exact-слайдов в пайплайн"
```

---

## Финальная проверка (после всех задач)

- [ ] **Полный тест-сьют**

Run: `.venv/bin/python -m pytest -q`
Expected: всё зелёное (или только заранее известные `slow`/`playwright`-скипы).

- [ ] **Дымовой прогон вручную (оффлайн, без ключа)**

```bash
printf 'Слайд 1: Привет\nТело один.\n\nСлайд 2: Пока\nТело два.\n' > /tmp/exact_smoke.txt
CLOUDRU_API_KEY= .venv/bin/python -c "from htmlslides.pipeline.build import build_deck; print(build_deck('/tmp/exact_smoke.txt','/tmp/exact_smoke.html',mode='exact'))"
```
Expected: печатает путь; в `/tmp/exact_smoke.html` — два `data-template="exact"`, дословный текст на месте (детерминированный фолбэк, т.к. ключ пуст).

---

## Соответствие спеке (self-review покрытия)

- Протокол меток `{{aK}}` + дословная подстановка — Task 4 (`_render`), Task 6 (`_design_slide`).
- Атомизация всех видов блоков — Task 3 (`atomize`).
- Акценты: целый атом (`<b>{{aK}}</b>`, `.t-number-320`) и диапазон слов `{{aK:i-j}}` — Task 4 (`_bold_word_range`), промпт Task 6.
- Проверки чистоты/полноты/запретов/классов + 1 ретрай + фолбэк — Task 5 (`_verify`), Task 6 (`_design_slide`).
- Оркестратор параллельно + мягкая деградация — Task 6 (`design_exact_deck`).
- Гибкие границы (Слайд N: / --- / #/## / 1 слайд) — Task 2.
- Экономия: `usage` замер — Task 1; кэш-дружелюбный префикс (`EXACT_DESIGN_SYSTEM` константа + классы через `.replace`, атомы в user-сообщении) — Task 6; без планировщика/vision, `thinking` off — унаследовано (`_FILL_NO_THINK`, `_build_exact` не зовёт `plan_deck`).
- Точки входа: `build.py` правка, лениво-клиент с фолбэком — Task 7.
- Ассемблер/`exact_builder`/`parse_pptx` — переиспользованы без изменений.
- Крайние случаи (нет ключа, провал проверки, только заголовок/картинка, текст без разделителей) — покрыты тестами Task 5/6/7.
