# Детерминированные бренд-раскладки в exact-режиме — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** В exact-режиме раскладывать дословный текст слайда бренд-стилем (карточки, крупные числа, тезис) детерминированно кодом, без ИИ, с сохранением 1-в-1 и дословности; ИИ-вёрстку Этапа 2 выключить.

**Architecture:** В `exact_builder.py` добавляем классификатор `_choose_layout(section)` и строители flow-раскладок (`_build_cards`, `_build_hero_number`, `_build_statement`), плюс безопасный дефолт `_build_default` (нынешняя логика). `build_exact_slide` диспетчеризует секцию в нужный строитель. Все раскладки — flow-примитивы (`.row/.col/.card/.accent-block` + бренд-типографика `.t-*`), они корректно измеряются и ужимаются штатным `autofitExact()` внутри `.exact-fit`. В `build.py` из exact-пути убираем вызов `design_exact_deck` (Этап 2), клиент ИИ не создаём.

**Tech Stack:** Python 3, pytest, стандартная библиотека (`re`, `html.escape`, `base64`). Никаких новых зависимостей.

**Коммиты:** По правилу пользователя коммиты делаются ТОЛЬКО по команде «закоммить». Шаги «Commit» ниже — часть стандартного TDD-цикла; при исполнении не коммитим автоматически, а группируем изменения и ждём команду «закоммить».

---

## File Structure

- **Modify:** `htmlslides/pipeline/exact_builder.py`
  Добавляем: `import re`, константы `_NUMERIC_TOKEN`/`_HERO_MAX`/`_CARD_ITEM_MAX`,
  хелперы `_all_list_items`, `_plain_text`, `_head_html`; классификатор
  `_choose_layout`; строители `_build_default`, `_card_html`, `_build_cards`,
  `_build_hero_number`, `_build_statement`; переписываем `build_exact_slide` как
  диспетчер. `build_exact_plan` не трогаем (сигнатура та же).

- **Modify:** `htmlslides/pipeline/build.py`
  Из `_build_exact` убираем блок Этапа 2 (строки 114-120) и функцию
  `_exact_client_or_none` (строки 127-133). Импорты (`KimiClient`, `Optional`,
  `LLMFormatError`) остаются — используются в остальном коде.

- **Test:** `tests/test_exact_builder.py`
  Добавляем тесты классификатора и строителей; существующие 5 тестов остаются
  зелёными (проверено против новой диспетчеризации).

- **Test:** `tests/test_build_exact.py`
  Удаляем `test_build_exact_designs_with_client` (Этап 2 удалён); переписываем
  `test_build_exact_no_key_skips_ai` в `test_build_exact_no_ai_calls`; в
  `test_build_exact_from_text_offline` добавляем проверку бренд-типографики.

- **Не трогаем:** `htmlslides/assembler.py`, `htmlslides/engine/deck.js`,
  `htmlslides/engine/deck.css`, `htmlslides/pipeline/linter.py`,
  `htmlslides/pipeline/exact_designer.py` (файл остаётся в репо, но из exact-пути
  не вызывается), `tests/test_exact_designer.py` (тестирует designer напрямую).

---

### Task 1: Классификатор раскладки + хелперы

**Files:**
- Modify: `htmlslides/pipeline/exact_builder.py`
- Test: `tests/test_exact_builder.py`

- [ ] **Step 1: Написать падающие тесты классификатора**

Добавить в конец `tests/test_exact_builder.py` (импорт `_choose_layout` дописать в
верхний импорт из `exact_builder`):

```python
from htmlslides.pipeline.exact_builder import (build_exact_plan, build_exact_slide,
                                               _choose_layout)


def test_choose_layout_table_is_default():
    from htmlslides.parsers.base import TableBlock
    s = Section(heading="T", blocks=[TableBlock(rows=[["a", "b"], ["c", "d"]])])
    assert _choose_layout(s) == "default"


def test_choose_layout_image_is_default():
    s = Section(heading="I", blocks=[ImageBlock(data=_PNG, mime="image/png")])
    assert _choose_layout(s) == "default"


def test_choose_layout_three_items_is_cards():
    s = Section(heading="C", blocks=[ListBlock(items=["раз", "два", "три"])])
    assert _choose_layout(s) == "cards"


def test_choose_layout_eight_items_is_default():
    s = Section(heading="L", blocks=[ListBlock(items=[str(i) for i in range(8)])])
    assert _choose_layout(s) == "default"


def test_choose_layout_long_list_items_is_default():
    long_item = "слово " * 40           # >120 символов — это абзац, не карточка
    s = Section(heading="P", blocks=[ListBlock(items=[long_item, long_item])])
    assert _choose_layout(s) == "default"


def test_choose_layout_numeric_is_hero_number():
    s = Section(heading="N", blocks=[TextBlock(text="99.9% аптайм")])
    assert _choose_layout(s) == "hero-number"


def test_choose_layout_short_text_is_statement():
    s = Section(heading="S", blocks=[TextBlock(text="Мы строим облако")])
    assert _choose_layout(s) == "statement"


def test_choose_layout_long_prose_is_default():
    prose = "Длинный абзац прозы. " * 20     # >120 символов
    s = Section(heading="D", blocks=[TextBlock(text=prose)])
    assert _choose_layout(s) == "default"


def test_choose_layout_empty_is_default():
    assert _choose_layout(Section(heading="E", blocks=[])) == "default"
```

Добавить импорт `TableBlock` в тестовый файл (в верхнюю строку `from
htmlslides.parsers.base import ...`).

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_exact_builder.py -q`
Expected: FAIL — `ImportError: cannot import name '_choose_layout'`.

- [ ] **Step 3: Реализовать хелперы и классификатор**

В `htmlslides/pipeline/exact_builder.py` добавить `import re` в шапку импортов и
вставить перед `def build_exact_slide` следующий код:

```python
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
```

- [ ] **Step 4: Запустить тесты — убедиться, что проходят**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_exact_builder.py -q`
Expected: PASS (все тесты классификатора зелёные; старые 5 тестов ещё используют
старую `build_exact_slide` — они тоже должны быть зелёными).

- [ ] **Step 5: Commit** (по команде «закоммить»)

```bash
git add htmlslides/pipeline/exact_builder.py tests/test_exact_builder.py
git commit -m "feat(exact): классификатор flow-раскладок _choose_layout"
```

---

### Task 2: Рефактор дефолтного строителя (`_head_html`, `_build_default`)

**Files:**
- Modify: `htmlslides/pipeline/exact_builder.py`
- Test: `tests/test_exact_builder.py` (существующие тесты — регресс)

- [ ] **Step 1: Убедиться, что существующие тесты зелёные (базовая линия)**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_exact_builder.py -q`
Expected: PASS. Это базовая линия перед чистым рефактором.

- [ ] **Step 2: Извлечь `_head_html` и `_build_default`, переписать `build_exact_slide`**

Заменить текущую функцию `build_exact_slide` (строки 50-73) на три функции:

```python
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


def build_exact_slide(section: Section) -> tuple[str, list[str]]:
    """Section → (html-фрагмент, предупреждения). Диспетчер flow-раскладок:
    по _choose_layout выбираем бренд-строитель, иначе — безопасный default."""
    return _build_default(section)
```

Примечание: диспетчеризация по `_choose_layout` подключается в Task 3-5; пока
`build_exact_slide` делегирует в `_build_default` без изменения поведения.

- [ ] **Step 3: Запустить тесты — убедиться, что всё зелёное**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_exact_builder.py -q`
Expected: PASS (поведение не изменилось; чистый рефактор).

- [ ] **Step 4: Commit** (по команде «закоммить»)

```bash
git add htmlslides/pipeline/exact_builder.py
git commit -m "refactor(exact): выделить _head_html и _build_default"
```

---

### Task 3: Строитель `cards`

**Files:**
- Modify: `htmlslides/pipeline/exact_builder.py`
- Test: `tests/test_exact_builder.py`

- [ ] **Step 1: Написать падающие тесты `cards`**

Добавить в `tests/test_exact_builder.py`:

```python
def test_cards_five_items_yield_five_cards():
    s = Section(heading="Услуги", blocks=[
        ListBlock(items=["A", "B", "C", "D", "E"])])
    html, warns = build_exact_slide(s)
    assert html.count('class="card"') == 5
    assert warns == []


def test_cards_name_dash_description_split():
    s = Section(heading="X", blocks=[
        ListBlock(items=["IaaS — вычисления по запросу", "PaaS — платформа"])])
    html, _ = build_exact_slide(s)
    assert '<p class="t-head-36">IaaS</p>' in html
    assert '<p class="t-body-30">вычисления по запросу</p>' in html


def test_cards_plain_item_is_body():
    s = Section(heading="X", blocks=[ListBlock(items=["просто пункт", "второй"])])
    html, _ = build_exact_slide(s)
    assert '<div class="card"><p class="t-body-30">просто пункт</p></div>' in html


def test_cards_preserve_intro_text_verbatim():
    s = Section(heading="X", blocks=[
        TextBlock(text="Вводный абзац."),
        ListBlock(items=["раз", "два", "три"])])
    html, _ = build_exact_slide(s)
    assert "Вводный абзац." in html          # дословный intro не теряется
    assert html.count('class="card"') == 3


def test_cards_escape_html():
    s = Section(heading="X", blocks=[ListBlock(items=["a < b — c & d", "y"])])
    html, _ = build_exact_slide(s)
    assert "a &lt; b" in html
    assert "c &amp; d" in html
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_exact_builder.py -k cards -q`
Expected: FAIL (нет `.card`-разметки — `build_exact_slide` ещё отдаёт `.exact-list`).

- [ ] **Step 3: Реализовать `_card_html` + `_build_cards`, подключить диспетчер**

Добавить в `exact_builder.py` (перед `build_exact_slide`):

```python
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
```

Обновить `build_exact_slide`:

```python
def build_exact_slide(section: Section) -> tuple[str, list[str]]:
    """Section → (html-фрагмент, предупреждения). Диспетчер flow-раскладок:
    по _choose_layout выбираем бренд-строитель, иначе — безопасный default."""
    layout = _choose_layout(section)
    if layout == "cards":
        return _build_cards(section)
    return _build_default(section)
```

- [ ] **Step 4: Запустить тесты — убедиться, что проходят**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_exact_builder.py -q`
Expected: PASS (тесты cards зелёные; старые тесты — `test_plan_one_to_one_verbatim`
секция B «раз/два» теперь рендерится карточками, «раз» по-прежнему в html).

- [ ] **Step 5: Commit** (по команде «закоммить»)

```bash
git add htmlslides/pipeline/exact_builder.py tests/test_exact_builder.py
git commit -m "feat(exact): строитель карточек _build_cards + диспетчер"
```

---

### Task 4: Строитель `hero-number`

**Files:**
- Modify: `htmlslides/pipeline/exact_builder.py`
- Test: `tests/test_exact_builder.py`

- [ ] **Step 1: Написать падающие тесты `hero-number`**

Добавить в `tests/test_exact_builder.py`:

```python
def test_hero_single_number_is_number_320():
    s = Section(heading="Аптайм", blocks=[TextBlock(text="99.9%")])
    html, _ = build_exact_slide(s)
    assert '<p class="t-number-320">99.9%</p>' in html


def test_hero_number_with_caption_uses_body_for_caption():
    s = Section(heading="X", blocks=[
        TextBlock(text="40%"), TextBlock(text="рост выручки")])
    html, _ = build_exact_slide(s)
    assert '<p class="t-hero-156">40%</p>' in html        # два блока → не 320
    assert '<p class="t-body-30">рост выручки</p>' in html


def test_hero_number_escapes_html():
    s = Section(heading="X", blocks=[TextBlock(text="<b> 5 ₽")])   # ₽ → numeric
    html, _ = build_exact_slide(s)
    assert "&lt;b&gt;" in html
    assert "<b>" not in html.replace('class="content-head"', "")
```

Примечание: текст «&lt;b&gt; 5 ₽» содержит числовой токен (`5 ₽`) → секция
классифицируется как `hero-number`, единственный числовой блок → `.t-number-320`.

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_exact_builder.py -k hero -q`
Expected: FAIL (нет `.t-number-320`/`.t-hero-156` — секция уходит в default).

- [ ] **Step 3: Реализовать `_build_hero_number`, подключить ветку**

Добавить в `exact_builder.py` (перед `build_exact_slide`):

```python
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
```

Обновить `build_exact_slide` — добавить ветку перед `return _build_default`:

```python
    if layout == "hero-number":
        return _build_hero_number(section)
```

- [ ] **Step 4: Запустить тесты — убедиться, что проходят**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_exact_builder.py -q`
Expected: PASS.

- [ ] **Step 5: Commit** (по команде «закоммить»)

```bash
git add htmlslides/pipeline/exact_builder.py tests/test_exact_builder.py
git commit -m "feat(exact): строитель крупных чисел _build_hero_number"
```

---

### Task 5: Строитель `statement`

**Files:**
- Modify: `htmlslides/pipeline/exact_builder.py`
- Test: `tests/test_exact_builder.py`

- [ ] **Step 1: Написать падающие тесты `statement`**

Добавить в `tests/test_exact_builder.py`:

```python
def test_statement_short_text_is_hero_156():
    s = Section(heading="Миссия", blocks=[TextBlock(text="Мы строим облако")])
    html, _ = build_exact_slide(s)
    assert '<p class="t-hero-156">Мы строим облако</p>' in html


def test_statement_two_blocks_each_rendered():
    s = Section(heading="X", blocks=[
        TextBlock(text="Первый тезис"), TextBlock(text="Второй тезис")])
    html, _ = build_exact_slide(s)
    assert '<p class="t-hero-156">Первый тезис</p>' in html
    assert '<p class="t-hero-156">Второй тезис</p>' in html


def test_statement_escapes_html():
    s = Section(heading="X", blocks=[TextBlock(text="a < b & c")])
    html, _ = build_exact_slide(s)
    assert "a &lt; b &amp; c" in html
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_exact_builder.py -k statement -q`
Expected: FAIL (текст уходит в default `.t-body-30`, а не `.t-hero-156`).

- [ ] **Step 3: Реализовать `_build_statement`, подключить ветку**

Добавить в `exact_builder.py` (перед `build_exact_slide`):

```python
def _build_statement(section: Section) -> tuple[str, list[str]]:
    """Короткий тезис (1-2 текст-блока без чисел) крупно в .t-hero-156. Каждый
    блок — своим абзацем (дословность per-block, переносы строк → <br>)."""
    head = _head_html(section)
    parts = [f'<p class="t-hero-156">{escape(b.text).replace(chr(10), "<br>")}</p>'
             for b in section.blocks if isinstance(b, TextBlock)]
    body = f'<div class="exact-text">{"".join(parts)}</div>'
    return head + body, []
```

Обновить `build_exact_slide` — добавить ветку перед `return _build_default`:

```python
    if layout == "statement":
        return _build_statement(section)
```

- [ ] **Step 4: Запустить весь файл тестов — убедиться, что всё зелёное**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_exact_builder.py -q`
Expected: PASS. Проверить, что `test_html_escaped` (секция с одним TextBlock)
теперь идёт через statement и по-прежнему зелёный.

- [ ] **Step 5: Commit** (по команде «закоммить»)

```bash
git add htmlslides/pipeline/exact_builder.py tests/test_exact_builder.py
git commit -m "feat(exact): строитель тезиса _build_statement"
```

---

### Task 6: Выключить Этап 2 (ИИ) в `build.py` + обновить тесты

**Files:**
- Modify: `htmlslides/pipeline/build.py:114-133`
- Test: `tests/test_build_exact.py`

- [ ] **Step 1: Обновить тесты `test_build_exact.py` (сначала тест)**

1. Удалить целиком `test_build_exact_designs_with_client` (строки 72-95) — Этап 2
   удалён, `_exact_client_or_none` больше не существует.

2. Заменить `test_build_exact_no_key_skips_ai` (строки 56-69) на:

```python
def test_build_exact_no_ai_calls(tmp_path, monkeypatch):
    """exact-путь детерминированный: ИИ-клиент не создаётся вовсе."""
    monkeypatch.delenv("CLOUDRU_API_KEY", raising=False)
    import htmlslides.pipeline.build as buildmod

    def _boom(*a, **k):
        raise AssertionError("exact не должен создавать ИИ-клиент")

    monkeypatch.setattr(buildmod, "KimiClient", _boom)
    src = _write_text_deck(tmp_path / "deck.txt")
    out = tmp_path / "deck.html"
    build_deck(src, out, mode="exact")                       # не падает без ИИ
    assert out.read_text(encoding="utf-8").count('data-template="exact"') == 2
```

3. В `test_build_exact_from_text_offline` (после строки 26) добавить проверку
   бренд-типографики (короткий текст → statement → `.t-hero-156`):

```python
    assert "t-hero-156" in html                              # бренд-раскладка, не голый текст
```

- [ ] **Step 2: Запустить тесты — убедиться, что падают/ломаются как надо**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_build_exact.py::test_build_exact_no_ai_calls -q`
Expected: FAIL с `AssertionError: exact не должен создавать ИИ-клиент`. Старый
`_build_exact` зовёт `_exact_client_or_none` → `KimiClient()` (подменён `_boom`),
и `_exact_client_or_none` ловит только `RuntimeError`, поэтому `AssertionError`
пробрасывается. Это доказывает, что старый путь ещё трогает ИИ-клиент.

- [ ] **Step 3: Убрать Этап 2 из `_build_exact` и удалить `_exact_client_or_none`**

В `htmlslides/pipeline/build.py` заменить строки 114-120:

```python
    # Этап 2: ИИ-вёрстка каждого слайда по протоколу меток. Нет ключа → пропускаем,
    # остаётся детерминированный html Этапа 1 (дека собирается всегда).
    from .exact_designer import design_exact_deck
    client = _exact_client_or_none(progress)
    if client is not None:
        progress("design: ИИ-вёрстка exact-слайдов")
        plan = design_exact_deck(client, doc, plan, progress=progress)
```

на:

```python
    # Этап 2 (ИИ-вёрстка) отключён намеренно: вёрстка теперь детерминированная
    # (build_exact_plan → flow-раскладки), ноль ИИ-вызовов — нет таймаутов и
    # заглушек. exact_designer.py остаётся в репо, но из exact-пути не зовётся.
```

Удалить функцию `_exact_client_or_none` целиком (строки 127-133):

```python
def _exact_client_or_none(progress: Progress) -> Optional[KimiClient]:
    """Клиент для ИИ-вёрстки exact-слайдов; нет ключа → None (собираем как Этап 1)."""
    try:
        return KimiClient()
    except RuntimeError as exc:
        progress(f"warn: нет ключа к ИИ ({exc}); точный перенос без дизайна (как Этап 1)")
        return None
```

- [ ] **Step 4: Запустить тесты — убедиться, что проходят**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_build_exact.py -q`
Expected: PASS (все тесты; `test_build_exact_no_ai_calls` зелёный — `KimiClient`
в exact-пути не создаётся).

- [ ] **Step 5: Полный прогон и проверка импортов**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_exact_builder.py tests/test_build_exact.py tests/test_exact_text.py tests/test_exact_designer.py -q`
Expected: PASS. `test_exact_designer.py` зелёный (designer не удалён, тестируется
напрямую). Проверить отсутствие `NameError`/`ImportError` по `_exact_client_or_none`.

- [ ] **Step 6: Commit** (по команде «закоммить»)

```bash
git add htmlslides/pipeline/build.py tests/test_build_exact.py
git commit -m "feat(exact): выключить ИИ-вёрстку Этапа 2 (детерминированный путь)"
```

---

## Финальная проверка (после всех задач)

- [ ] **Полный прогон тестов**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest -q`
Expected: PASS (весь набор; никаких регрессов в auto-режиме).

- [ ] **Ручная проверка на реальном pptx (если есть под рукой)**

Собрать exact-деку из тестового .pptx и глазами убедиться: карточки/числа/тезисы
на слайдах, текст дословный, ничего не обрезано (autofit ужал по высоте).

- [ ] **REQUIRED SUB-SKILL:** после завершения — `superpowers:finishing-a-development-branch`.

---

## Заметки для исполнителя

- **exact_builder автономен** — НЕ импортировать `planner`. `_NUMERIC_TOKEN`
  сознательно продублирован (одна строка регэкспа), чтобы не тянуть зависимость.
- **Дословность — инвариант №1.** Любой строитель обязан прогонять текст через
  `escape(...)` и не терять ни одного блока. `max_chars`/`max_items` НЕ применяем.
- **Классы уже разрешены линтером** (`ALLOWED_CLASSES`: `t-hero-156`,
  `t-number-320`, `t-head-36`, `t-body-30`, `row`, `col`, `card`, `accent-block`,
  `content-head`) — новых классов в CSS/линтере заводить не нужно.
- **Вход .txt/.md даёт только TextBlock** (парсер `exact_text.py` не делает
  ListBlock) → на тексте сработают `statement`/`hero-number`/`default`, а `cards`
  — на .pptx (там мультипараграфный текст-фрейм → ListBlock, `pptx.py:123`).
  Юнит-тесты `cards` используют вручную собранные `Section` с `ListBlock` —
  надёжно независимо от парсера.
