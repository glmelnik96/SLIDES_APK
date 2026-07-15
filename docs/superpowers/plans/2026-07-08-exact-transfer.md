# Точный перенос (exact transfer) — план реализации (Этап 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить отдельный пользовательский режим «Точный перенос»: 1 слайд источника → 1 слайд на выходе, текст слово-в-слово, картинки исходника как есть, сверху оформление Cloud.ru — без вызова ИИ-планировщика и ИИ-заполнителя.

**Architecture:** Новый build-режим `mode="exact"` в `build_deck` идёт в обход LLM: `parse` (pptx через python-pptx / текст через сплиттер по меткам «Слайд N:») → детерминированный билдер (N секций → N `SlidePlan`, каждый `freeform=True` с готовым html и меткой `exact`) → `polish_plan(vision=False, max_autofix=0)` (только assemble, без QA/autofix, без API-ключа). Влезаемость перегруженного слайда — JS-подгонка масштаба блока `.exact-fit` при загрузке в браузере. Дефолтный режим `auto`/`rebrand` не меняется.

**Tech Stack:** Python 3 (pydantic-модели `InputDoc/Section/Block`, python-pptx через существующий `parse_pptx`), Jinja2-ассемблер, self-contained HTML-дека (инлайн deck.css/deck.js), pytest (offline, без сети/браузера для ядра).

---

## Спека

Источник истины по решениям: [`docs/superpowers/specs/2026-07-08-exact-transfer-design.md`](../specs/2026-07-08-exact-transfer-design.md).

## Правило коммитов (важно, перекрывает шаг «Commit» навыка)

В этом проекте коммиты делаются **только по явной команде пользователя «закоммить»** (стандартное правило проекта). Поэтому в задачах ниже вместо шага «Commit» стоит **«Точка коммита»** — это подсказка, какие файлы логически коммитить вместе. **Не запускай `git commit` автоматически.** Когда пользователь скажет «закоммить» — по стандартному воркфлоу: спека изменений в чат + сообщение коммита + поимённый `git add` + пуш в `origin/skill-dev`.

## Команды окружения

- Python: `.venv/bin/python`
- Тесты: `.venv/bin/python -m pytest <путь> -v` (конфиг в `pyproject.toml`: `testpaths=["tests"]`, `asyncio_mode="auto"`).

## Область Этапа 1

- Форматы exact-режима: **`.pptx`, `.md`, `.txt`**. `.docx` в exact-режиме на Этапе 1 — понятная ошибка (у docx нет собственных границ слайдов; перенос — Этап 2).
- Картинки: растровые (PNG/JPEG/GIF/WebP) встраиваем base64. Векторные Office (EMF/WMF) — пропуск с предупреждением.
- pptx читаем существующим `parse_pptx` (дословный текст + картинки; 1 секция на слайд). Известное упрощение Этапа 1: многоабзацные текстовые фреймы pptx `parse_pptx` отдаёт как `ListBlock` — рендерим маркированным списком (текст дословный, отличается только наличие маркеров; более точная разбивка абзацев — Этап 2).

## Структура файлов

**Создаём:**
- `htmlslides/parsers/exact_text.py` — сплиттер текста по меткам «Слайд N:» → `list[Section]`.
- `htmlslides/pipeline/exact_builder.py` — детерминированный билдер: `Section` → html-фрагмент; `InputDoc` → `DeckPlan` (1-в-1).
- `tests/test_exact_text.py`, `tests/test_exact_builder.py`, `tests/test_assembler_exact.py`, `tests/test_build_exact.py`, `tests/test_exact_plumbing.py`.

**Меняем:**
- `htmlslides/assembler.py` — ветка exact в `_render_slide` (`data-template="exact"` + `.exact-zone`/`.exact-fit`).
- `htmlslides/engine/deck.css` — стили `.exact-zone`, `.exact-fit`, `.exact-cols`, `.exact-img`, `.exact-list`, `.exact-table`, `.exact-code`.
- `htmlslides/engine/deck.js` — функция `autofitExact()` + вызовы в `init()` и на `load`.
- `htmlslides/pipeline/build.py` — ранняя ветка `mode == "exact"` + функция `_build_exact`.
- `schemas/session.py` — поле `exact_transfer: bool` в `SessionInput`, `SessionState`, копирование в `from_input`.
- `worker/tasks/htmlnew.py` — выбор `mode="exact"` при `state.exact_transfer`.
- `webapp/app.py` — Form-поле `exact_transfer` в `create_job`.
- `webapp/static/index.html`, `webapp/static/app.js` — чекбокс «Точный перенос».

---

## Task 1: Сплиттер текста по меткам «Слайд N:»

**Files:**
- Create: `htmlslides/parsers/exact_text.py`
- Test: `tests/test_exact_text.py`

- [ ] **Step 1: Написать падающий тест**

Create `tests/test_exact_text.py`:

```python
import pytest

from htmlslides.parsers.base import Section, TextBlock
from htmlslides.parsers.exact_text import ExactMarkerError, split_exact_text


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


def test_no_marker_raises():
    with pytest.raises(ExactMarkerError):
        split_exact_text("Просто текст без меток.\nЕщё строка.")


def test_marker_lowercase_and_dot():
    raw = "слайд 1. Один\nтело\nслайд 2. Два\nтело2"
    sections = split_exact_text(raw)
    assert [s.heading for s in sections] == ["Один", "Два"]


def test_multiline_paragraph_kept_verbatim():
    raw = "Слайд 1: Т\nстрока A\nстрока B\n"
    sections = split_exact_text(raw)
    # две подряд непустые строки без пустой между ними = один абзац (дословно, с переводом строки)
    assert sections[0].blocks[0].text == "строка A\nстрока B"
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_exact_text.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'htmlslides.parsers.exact_text'`.

- [ ] **Step 3: Реализовать сплиттер**

Create `htmlslides/parsers/exact_text.py`:

```python
"""Разрез текста/txt на пер-слайдовые Section по меткам «Слайд N:» (exact-режим).

parse_md режет по markdown-заголовкам — для точного переноса не годится: границы
слайдов задаёт пользователь явными метками «Слайд 1:», «Слайд 2:» и т. д.
"""
from __future__ import annotations

import re

from .base import Section, TextBlock

# Метка в начале строки: «Слайд 1:» / «Слайд 1.» / регистр любой / табы-пробелы.
_MARKER = re.compile(r"^[ \t]*слайд[ \t]+\d+[ \t]*[:.]", re.IGNORECASE | re.MULTILINE)


class ExactMarkerError(ValueError):
    """В тексте нет ни одной метки «Слайд N:» — точный перенос невозможен."""


def split_exact_text(raw: str) -> list[Section]:
    """Текст → список Section (1 метка = 1 слайд). Дословно, без переписывания.

    Первая непустая строка куска — заголовок, остальное — тело (абзацы,
    разделённые пустыми строками, → TextBlock). Нет ни одной метки → ExactMarkerError.
    """
    matches = list(_MARKER.finditer(raw))
    if not matches:
        raise ExactMarkerError(
            "не найдено ни одной метки «Слайд N:». Разметь текст: перед каждым "
            "слайдом поставь строку вида «Слайд 1:», «Слайд 2:» и так далее."
        )
    sections: list[Section] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        chunk = raw[m.end():end]              # всё после метки до следующей метки
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

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `.venv/bin/python -m pytest tests/test_exact_text.py -v`
Expected: PASS (4 passed).

- [ ] **Точка коммита:** `htmlslides/parsers/exact_text.py`, `tests/test_exact_text.py` (не коммить без команды «закоммить»).

---

## Task 2: Детерминированный билдер (Section → html, InputDoc → DeckPlan 1-в-1)

**Files:**
- Create: `htmlslides/pipeline/exact_builder.py`
- Test: `tests/test_exact_builder.py`

- [ ] **Step 1: Написать падающий тест**

Create `tests/test_exact_builder.py`:

```python
from htmlslides.parsers.base import (ImageBlock, InputDoc, ListBlock, Section,
                                     TextBlock)
from htmlslides.pipeline.exact_builder import build_exact_plan, build_exact_slide

# 1x1 прозрачный PNG
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_plan_one_to_one_verbatim():
    doc = InputDoc(title="Дека", sections=[
        Section(heading="A", blocks=[TextBlock(text="текст слово в слово")]),
        Section(heading="B", blocks=[ListBlock(items=["раз", "два"])]),
        Section(heading="C", blocks=[TextBlock(text="ещё")]),
    ])
    plan, warnings = build_exact_plan(doc)
    assert plan.title == "Дека"
    assert len(plan.slides) == 3
    assert all(s.freeform for s in plan.slides)
    assert all(s.content.get("exact") is True for s in plan.slides)
    assert [s.index for s in plan.slides] == [1, 2, 3]
    assert "текст слово в слово" in plan.slides[0].content["html"]
    assert "раз" in plan.slides[1].content["html"]
    assert warnings == []


def test_heading_rendered_as_content_head():
    html, _ = build_exact_slide(Section(heading="Заголовок", blocks=[]))
    assert '<div class="content-head">' in html
    assert "t-head-42" in html
    assert "Заголовок" in html


def test_raster_image_embedded_base64():
    section = Section(heading="Пик", blocks=[
        ImageBlock(data=_PNG, mime="image/png", alt="pic")])
    html, warnings = build_exact_slide(section)
    assert "data:image/png;base64," in html
    assert warnings == []


def test_vector_image_skipped_with_warning():
    section = Section(heading="V", blocks=[
        ImageBlock(data=b"\x01\x02", mime="image/x-emf")])
    html, warnings = build_exact_slide(section)
    assert "<img" not in html
    assert warnings and "пропущена" in warnings[0]


def test_html_escaped():
    html, _ = build_exact_slide(
        Section(heading="<b>", blocks=[TextBlock(text="a < b & c")]))
    assert "<b>" not in html.replace('class="content-head"', "")
    assert "a &lt; b &amp; c" in html
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_exact_builder.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'htmlslides.pipeline.exact_builder'`.

- [ ] **Step 3: Реализовать билдер**

Create `htmlslides/pipeline/exact_builder.py`:

```python
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
        head = ('<div class="content-head"><h2 class="content-head-title t-head-42">'
                f'{escape(section.heading)}</h2></div>')
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
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `.venv/bin/python -m pytest tests/test_exact_builder.py -v`
Expected: PASS (5 passed).

- [ ] **Точка коммита:** `htmlslides/pipeline/exact_builder.py`, `tests/test_exact_builder.py`.

---

## Task 3: Ветка exact в ассемблере (`data-template="exact"` + `.exact-zone`/`.exact-fit`)

**Files:**
- Modify: `htmlslides/assembler.py:106-113`
- Test: `tests/test_assembler_exact.py`

- [ ] **Step 1: Написать падающий тест**

Create `tests/test_assembler_exact.py`:

```python
from htmlslides.assembler import assemble
from htmlslides.models import DeckPlan, SlidePlan


def _exact_plan():
    html = ('<div class="content-head"><h2 class="content-head-title t-head-42">'
            'Шапка</h2></div><div class="exact-text">'
            '<p class="t-body-30">дословный текст</p></div>')
    return DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="exact", freeform=True,
                  content={"html": html, "exact": True})])


def test_assemble_exact_marks_and_zone():
    out = assemble(_exact_plan(), theme="dark")
    assert 'data-template="exact"' in out
    assert 'class="exact-zone"' in out
    assert 'class="exact-fit"' in out
    assert "дословный текст" in out
    # шапка поднята на уровень слайда (вне .exact-zone)
    assert '<div class="content-head">' in out


def test_exact_both_themes():
    for theme in ("dark", "light"):
        out = assemble(_exact_plan(), theme=theme)
        assert 'data-template="exact"' in out


def test_freeform_still_freeform():
    plan = DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="content", freeform=True,
                  content={"html": '<div class="exact-text">x</div>'})])
    out = assemble(plan, theme="dark")
    assert 'data-template="freeform"' in out
    assert 'data-template="exact"' not in out
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_assembler_exact.py -v`
Expected: FAIL — `test_assemble_exact_marks_and_zone` (нет `data-template="exact"`; exact-слайд пока рендерится как freeform).

- [ ] **Step 3: Внести правку**

In `htmlslides/assembler.py`, replace the tail of the `if slide.freeform:` branch (currently lines 106-113):

```python
        head = ""
        m = re.match(r'^\s*(<div class="content-head">.*?</div>)(.*)$',
                     inner, re.DOTALL)
        if m:
            head, inner = m.group(1), m.group(2)
        chrome = env.get_template("_chrome.html").render()
        return ('<section class="slide slide--chrome-sm" data-template="freeform">'
                f'{chrome}{head}<div class="freeform-zone">{inner}</div></section>')
```

with:

```python
        head = ""
        m = re.match(r'^\s*(<div class="content-head">.*?</div>)(.*)$',
                     inner, re.DOTALL)
        if m:
            head, inner = m.group(1), m.group(2)
        chrome = env.get_template("_chrome.html").render()
        if slide.content.get("exact"):
            # exact-слайд: контент в .exact-fit (JS ужимает масштаб под .exact-zone),
            # отдельный маркер data-template="exact" — для QA/отладки.
            return ('<section class="slide slide--chrome-sm" data-template="exact">'
                    f'{chrome}{head}<div class="exact-zone">'
                    f'<div class="exact-fit">{inner}</div></div></section>')
        return ('<section class="slide slide--chrome-sm" data-template="freeform">'
                f'{chrome}{head}<div class="freeform-zone">{inner}</div></section>')
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `.venv/bin/python -m pytest tests/test_assembler_exact.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Регресс ассемблера/линтера**

Run: `.venv/bin/python -m pytest tests/test_assembler.py tests/test_linter.py -v` (если файлы есть; иначе пропустить).
Expected: PASS — существующие freeform/template-слайды не задеты.

- [ ] **Точка коммита:** `htmlslides/assembler.py`, `tests/test_assembler_exact.py`.

---

## Task 4: Стили `.exact-zone`/`.exact-fit` и компонентов

**Files:**
- Modify: `htmlslides/engine/deck.css` (добавить блок после `.freeform-zone`, ~строка 111)

- [ ] **Step 1: Добавить CSS**

In `htmlslides/engine/deck.css`, after the `.freeform-zone{...}` rule (line 111), add:

```css
/* ---- exact (точный перенос 1-в-1): safe-зона + JS-подгонка масштаба блока ---- */
.exact-zone{position:absolute;left:60px;top:300px;width:1800px;height:720px;overflow:hidden;}
.exact-fit{transform-origin:top left;width:1800px;}   /* JS ставит transform:scale(k) */
.exact-text>*+*{margin-top:18px;}
.exact-cols{display:flex;gap:36px;align-items:flex-start;}
.exact-cols .exact-text{flex:1;min-width:0;}
.exact-cols .exact-media{flex:0 0 auto;display:flex;flex-direction:column;gap:18px;}
.exact-img{display:block;max-width:760px;max-height:680px;width:auto;height:auto;}
.exact-list{padding-left:1.2em;}
.exact-list li+li{margin-top:12px;}
.exact-table{border-collapse:collapse;}
.exact-table td{border:1px solid var(--line-frame);padding:12px 18px;}
.exact-code{font-family:var(--font-text);white-space:pre-wrap;}
```

- [ ] **Step 2: Проверить, что CSS попадает в собранную деку**

Run: `.venv/bin/python -m pytest tests/test_assembler_exact.py -v`
Expected: PASS — `assemble` инлайнит deck.css; тест из Task 3 уже проверяет наличие `class="exact-zone"`/`class="exact-fit"` в выводе. (CSS-правила проверяются визуально в браузере на Task 6/UI.)

- [ ] **Точка коммита:** `htmlslides/engine/deck.css`.

---

## Task 5: JS auto-fit (`autofitExact`)

**Files:**
- Modify: `htmlslides/engine/deck.js` (новая функция + вызовы в `init()` и на `load`)

- [ ] **Step 1: Добавить функцию `autofitExact`**

In `htmlslides/engine/deck.js`, add this function right before `function init() {` (line 119):

```javascript
  /* Точный перенос: ужать блок .exact-fit, пока не влезет в .exact-zone. Текст НЕ
     сокращаем — масштабируем весь блок (transform:scale). Нижний предел 0.35: ниже
     нечитаемо — оставляем как есть (текст цел, но может подрезаться зоной). */
  function autofitExact() {
    var zones = document.querySelectorAll(".exact-zone");
    for (var i = 0; i < zones.length; i++) {
      var zone = zones[i];
      var fit = zone.querySelector(".exact-fit");
      if (!fit) continue;
      fit.style.transform = "none";
      var needH = fit.scrollHeight, needW = fit.scrollWidth;
      if (!needH || !needW) continue;
      var scale = Math.min(zone.clientHeight / needH, zone.clientWidth / needW, 1);
      if (scale < 0.35) scale = 0.35;
      if (scale < 1) fit.style.transform = "scale(" + scale + ")";
    }
  }
```

- [ ] **Step 2: Вызвать в `init()`**

In `init()`, after the `rescale();` line (line 124), add:

```javascript
    autofitExact();
    window.addEventListener("load", autofitExact);   // пересчёт после подгрузки шрифтов/картинок
```

So the block becomes:

```javascript
    rescale();
    autofitExact();
    window.addEventListener("load", autofitExact);   // пересчёт после подгрузки шрифтов/картинок
    window.addEventListener("resize", rescale);
    document.addEventListener("visibilitychange", rescale);
```

- [ ] **Step 3: Структурная проверка (сборка не сломана, функция в деке)**

Run: `.venv/bin/python -m pytest tests/test_assembler_exact.py -v`
Expected: PASS. (Поведение auto-fit — рантайм браузера; проверяется на Task 6 через готовый .html в браузере / preview, юнит-тестом DOM-раскладку не берём.)

- [ ] **Step 4: Ручная проверка в браузере (на Task 6, после сборки .html)**

После сборки exact-деки (Task 6) открыть .html: перегруженный слайд должен уместиться в зелёную safe-зону, текст не выходить за границы, номер/логотип/колонтитул на месте.

- [ ] **Точка коммита:** `htmlslides/engine/deck.js`.

---

## Task 6: Ветка `mode="exact"` в `build_deck`

**Files:**
- Modify: `htmlslides/pipeline/build.py` (ранняя ветка в `build_deck` + функция `_build_exact`)
- Test: `tests/test_build_exact.py`

- [ ] **Step 1: Написать падающий тест**

Create `tests/test_build_exact.py`:

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


def test_build_exact_from_text_offline(tmp_path):
    src = _write_text_deck(tmp_path / "deck.txt")
    out = tmp_path / "deck.html"
    result = build_deck(src, out, mode="exact")     # без сети/ключа
    assert result == out
    html = out.read_text(encoding="utf-8")
    assert html.count('data-template="exact"') == 2
    assert "Дословный текст один." in html
    assert "Дословный текст два." in html


def test_build_exact_light_theme(tmp_path):
    src = _write_text_deck(tmp_path / "deck.txt")
    out = tmp_path / "deck.html"
    build_deck(src, out, mode="exact", theme="light")
    assert 'data-theme="light"' in out.read_text(encoding="utf-8")


def test_build_exact_no_marker_errors(tmp_path):
    src = tmp_path / "bad.txt"
    src.write_text("текст без меток слайдов", encoding="utf-8")
    with pytest.raises(ValueError):
        build_deck(src, tmp_path / "o.html", mode="exact")


def test_build_exact_docx_rejected(tmp_path):
    src = tmp_path / "x.docx"
    src.write_bytes(b"PK\x03\x04stub")
    with pytest.raises(ValueError):
        build_deck(src, tmp_path / "o.html", mode="exact")
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_build_exact.py -v`
Expected: FAIL — `mode="exact"` не обрабатывается (падает в планировщике / на отсутствии ключа).

- [ ] **Step 3: Добавить ветку exact в `build_deck` + `_build_exact`**

In `htmlslides/pipeline/build.py`, at the start of `build_deck` body, right after `src, out = Path(input_path), Path(out_path)` (line 43), add:

```python
    if mode == "exact":
        return _build_exact(src, out, theme=theme, progress=progress)
```

Then add this function after `build_deck` (before `polish_plan`, ~line 78):

```python
def _build_exact(src: Path, out: Path, *, theme: str,
                 progress: Progress) -> Path:
    """Точный перенос: 1 слайд источника → 1 слайд, текст дословно, без ИИ.

    pptx читаем существующим parse_pptx (дословный текст + картинки, 1 секция на
    слайд); .md/.txt режем по меткам «Слайд N:». docx на Этапе 1 не поддержан.
    """
    from ..parsers import parse_file
    from ..parsers.base import InputDoc
    from ..parsers.exact_text import split_exact_text
    from .exact_builder import build_exact_plan

    progress(f"parse: {src.name}")
    suffix = src.suffix.lower()
    if suffix == ".pptx":
        doc = parse_file(src)
    elif suffix in (".md", ".txt"):
        raw = src.read_text(encoding="utf-8")
        doc = InputDoc(title=src.stem, sections=split_exact_text(raw))
    else:
        raise ValueError(
            "точный перенос на Этапе 1 поддерживает .pptx/.md/.txt, "
            f"не {suffix or 'без расширения'}")
    if not doc.sections:
        raise LLMFormatError(
            "источник пуст (0 слайдов) — точную деку не собираем")

    progress(f"plan: {len(doc.sections)} слайдов 1-в-1 (без ИИ)")
    plan, warnings = build_exact_plan(doc)
    for w in warnings:
        progress(f"warn: {w}")
    # polish без vision/autofix: только assemble (ИИ не зовётся, ключ не нужен).
    return polish_plan(plan, out, theme=theme, vision=False, max_autofix=0,
                       progress=progress)
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `.venv/bin/python -m pytest tests/test_build_exact.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Проверить деку в браузере (auto-fit из Task 5)**

Собрать перегруженную exact-деку и открыть .html (двойной клик / preview): на плотном слайде контент ужимается в safe-зону, текст не вылезает, хром на месте. Если контент всё же подрезан на нижнем пределе 0.35 — это ожидаемое поведение (текст цел), зафиксировать как известный предел.

- [ ] **Точка коммита:** `htmlslides/pipeline/build.py`, `tests/test_build_exact.py`.

---

## Task 7: Проброс `exact_transfer` через схемы и раннер

**Files:**
- Modify: `schemas/session.py` (поле в `SessionInput`, `SessionState`, копирование в `from_input`)
- Modify: `worker/tasks/htmlnew.py` (выбор режима)
- Test: `tests/test_exact_plumbing.py`

- [ ] **Step 1: Написать падающий тест**

Create `tests/test_exact_plumbing.py`:

```python
from pathlib import Path

from schemas.session import Mode, SessionInput, SessionState


def _input(**kw):
    base = dict(session_id="s1", user_id=1, chat_id=0, progress_message_id=0,
                mode=Mode.HTMLNEW, input_s3_key="/tmp/x.pptx",
                source_filename="x.pptx")
    base.update(kw)
    return SessionInput(**base)


def test_session_input_default_false():
    assert _input().exact_transfer is False


def test_from_input_carries_exact():
    state = SessionState.from_input(_input(exact_transfer=True))
    assert state.exact_transfer is True


def test_run_htmlnew_picks_exact_mode(monkeypatch, tmp_path):
    import worker.tasks.htmlnew as htmlnew

    captured = {}

    def fake_build_deck(inp, out, **kw):
        captured["mode"] = kw.get("mode")
        Path(out).write_text("<html></html>", encoding="utf-8")
        return Path(out)

    monkeypatch.setattr("htmlslides.pipeline.build.build_deck", fake_build_deck)
    monkeypatch.setattr(htmlnew.progress, "stage", lambda *a, **k: None)
    monkeypatch.setattr(htmlnew.progress, "done", lambda *a, **k: None)
    monkeypatch.setattr(htmlnew, "session_dir", lambda sid: tmp_path)

    state = SessionState.from_input(
        _input(input_s3_key=str(tmp_path / "d.pptx"),
               source_filename="d.pptx", exact_transfer=True))
    htmlnew.run_htmlnew(state)
    assert captured["mode"] == "exact"


def test_run_htmlnew_default_mode_unchanged(monkeypatch, tmp_path):
    import worker.tasks.htmlnew as htmlnew

    captured = {}

    def fake_build_deck(inp, out, **kw):
        captured["mode"] = kw.get("mode")
        Path(out).write_text("<html></html>", encoding="utf-8")
        return Path(out)

    monkeypatch.setattr("htmlslides.pipeline.build.build_deck", fake_build_deck)
    monkeypatch.setattr(htmlnew.progress, "stage", lambda *a, **k: None)
    monkeypatch.setattr(htmlnew.progress, "done", lambda *a, **k: None)
    monkeypatch.setattr(htmlnew, "session_dir", lambda sid: tmp_path)

    state = SessionState.from_input(
        _input(input_s3_key=str(tmp_path / "d.pptx"), source_filename="d.pptx"))
    htmlnew.run_htmlnew(state)
    assert captured["mode"] == "rebrand"   # pptx по умолчанию
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_exact_plumbing.py -v`
Expected: FAIL — `SessionInput` не знает поля `exact_transfer` (pydantic `extra="forbid"` / отсутствует поле).

- [ ] **Step 3: Добавить поле в `schemas/session.py`**

In `schemas/session.py`, add to `SessionInput` after `created_at` (line 56):

```python
    exact_transfer: bool = False        # точный перенос 1-в-1 (в обход ИИ)
```

Add to `SessionState` after `source_filename` in the «Inputs / outputs» block (line 85):

```python
    exact_transfer: bool = False        # точный перенос 1-в-1 (в обход ИИ)
```

In `SessionState.from_input` (classmethod, lines 108-117), add the field to the copied kwargs (after `source_filename=inp.source_filename,` on line 116, before the closing `)`):

```python
            exact_transfer=inp.exact_transfer,
```

- [ ] **Step 4: Прокинуть режим в `worker/tasks/htmlnew.py`**

In `run_htmlnew` (`htmlslides/../worker/tasks/htmlnew.py`), replace the build call block (lines 82-91):

```python
    progress.stage(session_id, Stage.PARSING, 5, detail="старт сборки HTML")
    log.info("htmlnew.start", input=str(input_path), mode=pick_mode(input_path))
    result = build_deck(
        input_path,
        out,
        mode=pick_mode(input_path),
        vision=True,
        freeform_ok=True,        # включён управляемый freeform (вариант B)
        progress=on_progress,
    )
```

with:

```python
    mode_arg = "exact" if state.exact_transfer else pick_mode(input_path)
    progress.stage(session_id, Stage.PARSING, 5, detail="старт сборки HTML")
    log.info("htmlnew.start", input=str(input_path), mode=mode_arg)
    result = build_deck(
        input_path,
        out,
        mode=mode_arg,
        vision=True,
        freeform_ok=True,        # включён управляемый freeform (вариант B); в exact игнорируется
        progress=on_progress,
    )
```

- [ ] **Step 5: Запустить тест — убедиться, что проходит**

Run: `.venv/bin/python -m pytest tests/test_exact_plumbing.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Регресс схем/раннера**

Run: `.venv/bin/python -m pytest tests/test_htmlnew_progress.py tests/test_pipeline_bridge.py tests/test_runner.py -v`
Expected: PASS — существующий путь не задет (новое поле имеет дефолт `False`).

- [ ] **Точка коммита:** `schemas/session.py`, `worker/tasks/htmlnew.py`, `tests/test_exact_plumbing.py`.

---

## Task 8: Форма загрузки — Form-поле и чекбокс

**Files:**
- Modify: `webapp/app.py` (`create_job`: Form-поле `exact_transfer`)
- Modify: `webapp/static/index.html` (чекбокс)
- Modify: `webapp/static/app.js` (добавить поле в FormData)
- Test: `tests/test_app.py` (приём поля)

- [ ] **Step 1: Написать падающий тест (по идиоме `_client`/`H`/capture из `tests/test_app.py`)**

Add to `tests/test_app.py` (файл уже импортирует `asyncio`, `appmod`; хелперы `_client`, `H` определены сверху):

```python
def test_create_job_forwards_exact_transfer(monkeypatch, tmp_path):
    started = {}
    monkeypatch.setattr(appmod.runner, "start",
                        lambda inp, **kw: started.update(exact=inp.exact_transfer)
                        or asyncio.Queue())
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/api/jobs",
                   data={"mode": "htmlnew", "exact_transfer": "true"},
                   files={"file": ("x.md", b"# hi", "text/markdown")}, headers=H())
        assert r.status_code == 200
        assert started["exact"] is True


def test_create_job_exact_defaults_false(monkeypatch, tmp_path):
    started = {}
    monkeypatch.setattr(appmod.runner, "start",
                        lambda inp, **kw: started.update(exact=inp.exact_transfer)
                        or asyncio.Queue())
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/api/jobs", data={"mode": "htmlnew"},
                   files={"file": ("x.md", b"# hi", "text/markdown")}, headers=H())
        assert r.status_code == 200
        assert started["exact"] is False
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_app.py -k exact -v`
Expected: FAIL — `422 Unprocessable Entity` (неизвестное Form-поле) до правки `create_job`.

- [ ] **Step 3: Добавить Form-поле в `create_job`**

In `webapp/app.py`, extend the `create_job` signature (lines 151-153) to add the parameter:

```python
@app.post("/api/jobs")
async def create_job(request: Request, mode: str = Form(...),
                     exact_transfer: str = Form(default="false"),
                     file: UploadFile = File(...),
                     user=Depends(get_current_user)) -> JSONResponse:
```

In the body where `SessionInput(...)` is built (lines 170-172), add the parsed flag:

```python
    inp = SessionInput(user_id=user.id, chat_id=0, progress_message_id=0,
                       mode=Mode(mode), input_s3_key=None,
                       source_filename=file.filename,
                       exact_transfer=exact_transfer.lower() in ("1", "true", "on", "yes"))
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `.venv/bin/python -m pytest tests/test_app.py -k exact -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Чекбокс в форме**

In `webapp/static/index.html`, inside `#uploadFlow`, after the file-drop `<label class="drop" id="drop">…</label>` (closes at line 74) and before the `<button id="create" …>` button (line 80), add:

```html
      <label class="opt-row">
        <input type="checkbox" id="exactTransfer">
        <span>Точный перенос (1 в 1): каждый слайд и текст исходника — без изменений</span>
      </label>
```

- [ ] **Step 6: Добавить поле в FormData**

In `webapp/static/app.js`, in the `$("#create").onclick` handler (lines 210-216), after `fd.append("file", selectedFile);` (line 214) and before `$("#create").disabled = true;` (line 215), add:

```javascript
  const ex = document.getElementById("exactTransfer");
  if (ex && ex.checked) fd.append("exact_transfer", "true");
```

- [ ] **Step 7: Проверка в браузере**

Запусти webapp локально, открой форму: чекбокс виден; при включённом чекбоксе и загрузке .pptx/.txt с метками — собирается exact-дека (число слайдов = числу исходных, текст дословно). Дефолт (чекбокс выключен) собирает как раньше.

- [ ] **Точка коммита:** `webapp/app.py`, `webapp/static/index.html`, `webapp/static/app.js`, `tests/test_app.py`.

---

## Финальная проверка (после всех задач)

- [ ] **Весь набор exact-тестов:**

Run: `.venv/bin/python -m pytest tests/test_exact_text.py tests/test_exact_builder.py tests/test_assembler_exact.py tests/test_build_exact.py tests/test_exact_plumbing.py -v`
Expected: PASS (все).

- [ ] **Регресс всего проекта (дефолтный режим не задет):**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (или столько же зелёных, сколько до задачи; новых падений нет).

- [ ] **Ручная сборка обоих типов входа в браузере:** .pptx-черновик (с картинками) и .txt с метками «Слайд N:» → число слайдов совпадает 1-в-1, текст дословно, картинки на месте, перегруженный слайд ужат в safe-зону, оформление Cloud.ru (логотип/номер/колонтитул) на всех слайдах, тёмная и светлая темы рендерятся.

---

## Известные упрощения Этапа 1 (в Этап 2)

- «Апгрейд» простых exact-слайдов в нарядные бренд-шаблоны (полный «вариант 3»).
- Конвертация векторных картинок EMF/WMF в PNG (на Этапе 1 — пропуск с предупреждением).
- `.docx` в exact-режиме (нет собственных границ слайдов; на Этапе 1 — понятная ошибка).
- Более точная разбивка абзацев pptx (сейчас многоабзацный фрейм → `ListBlock`/маркированный список; текст дословный).
