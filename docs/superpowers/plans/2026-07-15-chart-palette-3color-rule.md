# Chart Palette + «≤3 цвета на слайд» — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Раскрасить композиционные графики (donut / stacked-bar / bar-chart) семантической серией `--chart-1..6` (зелёный-герой + 2 приглушённых доп-цвета + серая шкала) по правилу «≤3 хроматических цвета на слайд», убрав opacity-лесенки и мёртвые бренд-токены.

**Architecture:** Токены `--chart-*` объявляются в `deck.css` (`--chart-1 = var(--cl-green)` в `:root`; `--chart-2..6` — литералами в блоках `html[data-theme="dark"|"light"]`). Шаблоны графиков потребляют их по индексу сегмента: `fill="var(--chart-{{ loop.index0 + 1 }})"`. Метрики/тренды (`kpi`, `stats-row`, `kpi-rings`, `line-chart`) не трогаем — остаются зелёными. Верификация: pytest-ассерты на выводе `assembler.assemble()` (быстрые, без браузера) + визуальный рендер обеих тем через `screenshot_slides` (Playwright/Chromium из `.venv`).

**Tech Stack:** Jinja2-шаблоны (`htmlslides/templates/slides/*.html`), CSS custom properties (`htmlslides/engine/deck.css`), pydantic-модели `DeckPlan`/`SlidePlan`, pytest, Playwright (`.venv/bin/python`).

**Спека:** `docs/superpowers/specs/2026-07-15-chart-palette-3color-rule-design.md`

---

## File Structure

**Изменяем:**
- `htmlslides/engine/deck.css` — добавить `--chart-1..6`; удалить мёртвые токены (magenta, aqua, dk2, pastel-*, mid-gray, dark-gray); пометить vivid как «резерв»; переписать комменты `:root` и CSS-блоков графиков (donut/stacked/bar).
- `htmlslides/templates/slides/donut-chart.html` — `--chart-N` по индексу вместо `--accent`+opacity-лесенка; свотчи легенды — те же токены.
- `htmlslides/templates/slides/stacked-bar.html` — то же для сегментов и свотчей легенды; фикс контраста %-метки (`var(--bg)` → `var(--cl-graphite)`).
- `htmlslides/templates/slides/bar-chart.html` — бар `--chart-{index+1}` вместо `--accent` (в рабочем дереве уже есть незакоммиченная правка переноса подписей — она ортогональна цвету, её сохраняем).

**Создаём:**
- `tests/test_chart_palette.py` — регресс-ассерты на токены и раскраску шаблонов.
- `docs/brand/chart-colors.md` — человекочитаемый справочник палитры.
- `_qa_charts.py` — **временный** харнесс рендера (удаляется в Task 7).

**Не трогаем:** `line-chart.html`, `kpi-rings.html`, `kpi.html`, `stats-row.html` и их места в `deck.css`; `skill_assets/brand/palette.json` и PPTX-скилл.

**Значения токенов (единый источник для всех задач):**

| Токен | Роль | dark | light |
|---|---|---|---|
| `--chart-1` | зелёный-герой | `var(--cl-green)` (#26D07C) | `var(--cl-green)` (#26D07C) |
| `--chart-2` | ultramarine приглушённый | `#5D779F` | `#9CADCE` |
| `--chart-3` | purple приглушённый | `#8372A1` | `#BDB0D2` |
| `--chart-4` | серый gray3 | `#989898` | `#A6A6A6` |
| `--chart-5` | серый gray4 | `#737373` | `#8C8C8C` |
| `--chart-6` | серый gray5 | `#525252` | `#737373` |

Раскраска по индексу: donut → `--chart-1..5` (макс 5 сегментов); stacked-bar → `--chart-1..4` (макс 4 сегмента v1..v4, свотчи легенды те же); bar-chart → `--chart-1..6` (макс 6 баров).

---

## Task 1: Токены `--chart-*` в deck.css + чистка мёртвых + резерв vivid

**Files:**
- Test: `tests/test_chart_palette.py` (создать)
- Modify: `htmlslides/engine/deck.css:11-26` (блок доп-палитры + пастельные тинты), `:34-39` (dark), `:40-45` (light)

- [ ] **Step 1: Написать падающий тест токенов**

Создать `tests/test_chart_palette.py`:

```python
"""Регресс палитры графиков: --chart-* определены, мёртвые токены удалены,
шаблоны красят сегменты по индексу без opacity-лесенок."""
from importlib import resources

from htmlslides.assembler import assemble
from htmlslides.models import DeckPlan, SlidePlan


def _deck_css() -> str:
    return (resources.files("htmlslides") / "engine" / "deck.css").read_text("utf-8")


def test_chart_tokens_defined_root_and_themes():
    css = _deck_css()
    # --chart-1 = зелёный в :root (общий для обеих тем)
    assert "--chart-1:var(--cl-green)" in css
    # --chart-2..6 объявлены литералами (по разу в dark и по разу в light → 2)
    for n, dark, light in [
        (2, "#5D779F", "#9CADCE"), (3, "#8372A1", "#BDB0D2"),
        (4, "#989898", "#A6A6A6"), (5, "#737373", "#8C8C8C"),
        (6, "#525252", "#737373"),
    ]:
        assert f"--chart-{n}:{dark}" in css, f"dark --chart-{n} missing"
        assert f"--chart-{n}:{light}" in css, f"light --chart-{n} missing"


def test_dead_tokens_removed():
    css = _deck_css()
    for dead in ["--cl-magenta", "--cl-aqua", "--cl-dk2", "--cl-pastel-mint",
                 "--cl-pastel-yellow", "--cl-pastel-green", "--cl-mid-gray",
                 "--cl-dark-gray"]:
        assert dead not in css, f"{dead} should be removed"


def test_vivid_reserve_and_used_tokens_kept():
    css = _deck_css()
    for keep in ["--cl-ultramarine", "--cl-purple", "--cl-carrot",
                 "--cl-coral", "--cl-yellow", "--cl-blue-lt"]:
        assert keep in css, f"{keep} must stay (reserve/used)"
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_chart_palette.py -v`
Expected: FAIL — `test_chart_tokens_defined_root_and_themes` падает на `--chart-1:var(--cl-green)` (токенов ещё нет); `test_dead_tokens_removed` падает (токены ещё на месте).

- [ ] **Step 3: Переписать блок доп-палитры в `:root` (deck.css:11-26)**

Заменить текущие строки 11-26:

```css
  /* Дополнительная палитра — эталон Cloud.ru_Template_2026.pptx (theme accent2..6) +
     palette.json (единый источник истины). Green — ГЛАВНЫЙ акцент; эти цвета
     ДОПОЛНИТЕЛЬНЫЕ: по «иерархии масс» каждый ≤ green, применять редко/точечно.
     Графики остаются одноцветными (зелёный + opacity-лесенка) — «без радуги» инвариант. */
  --cl-yellow:#CFF500;        /* base.Yellow — лаймовый акцент */
  --cl-purple:#A068FF;        /* base.Purple */
  --cl-aqua:#18F4CF;          /* extended.Aquamarine */
  --cl-ultramarine:#0063FF;   /* extended.Ultramarine — насыщенный синий (= pptx accent2) */
  --cl-blue-lt:#C0E0FC;       /* base.Blue — светлый (фон изображений) */
  --cl-magenta:#FF00FF;       /* extended.Magenta */
  --cl-carrot:#FF4517;        /* extended.Carrot */
  --cl-coral:#FF0642;         /* extended.Coral */
  --cl-dk2:#4E4E4E;           /* pptx dk2 — средний графит */
  /* Пастельные тинты — ТОЛЬКО для доп. серий графиков (palette.chart_extension) */
  --cl-pastel-mint:#C9F2EA; --cl-pastel-yellow:#E8F5C7; --cl-pastel-green:#A8E5C9;
  --cl-mid-gray:#BDBDBD; --cl-dark-gray:#666666;
```

на:

```css
  /* Резерв «живых» доп-цветов (vivid) — ТОЛЬКО обводки/точечные акценты, НЕ заливка
     графиков. Заливку композиционных графиков делают семантические --chart-* (ниже),
     по правилу «≤3 хроматических цвета на слайд + оттенки серого». Green — цвет-герой,
     всегда доминирует по массе; vivid применять редко/точечно. */
  --cl-yellow:#CFF500;        /* base.Yellow — лаймовый акцент */
  --cl-purple:#A068FF;        /* base.Purple */
  --cl-ultramarine:#0063FF;   /* extended.Ultramarine — насыщенный синий (= pptx accent2) */
  --cl-carrot:#FF4517;        /* extended.Carrot */
  --cl-coral:#FF0642;         /* extended.Coral */
  --cl-blue-lt:#C0E0FC;       /* base.Blue — светлый (фон изображений) */
  /* Серия графиков --chart-1..6 (композиционные: donut/stacked-bar/bar-chart).
     Правило: зелёный-герой + до 2 приглушённых доп-цветов (ultramarine/purple),
     дальше только серая шкала gray3–5. --chart-1 = зелёный в обеих темах;
     --chart-2..6 резолвятся темой (см. блоки html[data-theme]). Метрики/тренды
     (kpi/stats-row/kpi-rings/line-chart) остаются зелёными — цвет там не нёс бы смысла. */
  --chart-1:var(--cl-green);
```

- [ ] **Step 4: Добавить `--chart-2..6` в блок dark**

В блоке `html[data-theme="dark"]{…}` (deck.css:34-39) добавить строку перед закрывающей `}` (после `--image-bg:var(--cl-blue-lt);`):

```css
  --chart-2:#5D779F; --chart-3:#8372A1;   /* ultramarine/purple приглушённые */
  --chart-4:#989898; --chart-5:#737373; --chart-6:#525252;   /* серые gray3–5 */
```

- [ ] **Step 5: Добавить `--chart-2..6` в блок light**

В блоке `html[data-theme="light"]{…}` (deck.css:40-45) добавить строку перед закрывающей `}` (после `--image-bg:var(--cl-blue-lt);`):

```css
  --chart-2:#9CADCE; --chart-3:#BDB0D2;   /* ultramarine/purple пастельные */
  --chart-4:#A6A6A6; --chart-5:#8C8C8C; --chart-6:#737373;   /* серые gray3–5 (рецессивные) */
```

- [ ] **Step 6: Запустить тесты — убедиться, что проходят**

Run: `.venv/bin/python -m pytest tests/test_chart_palette.py -v`
Expected: PASS — все три теста Task 1 зелёные.

- [ ] **Step 7: Коммит**

```bash
git add tests/test_chart_palette.py htmlslides/engine/deck.css
git commit -m "$(cat <<'EOF'
feat(charts): токены --chart-1..6 + чистка мёртвых бренд-цветов

Семантическая серия графиков (зелёный-герой + 2 приглушённых + серая шкала
gray3-5) по правилу ≤3 хроматических цвета на слайд. Удалены мёртвые magenta/
aqua/dk2/pastel-*/mid-gray/dark-gray; vivid помечены как резерв (акценты/обводки).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: donut-chart — раскраска `--chart-1..5`, снять opacity-лесенку

**Files:**
- Test: `tests/test_chart_palette.py` (дополнить)
- Modify: `htmlslides/templates/slides/donut-chart.html:7-10` (комм.), `:35` (ladder), `:57` (op), `:67-71` (дуга), `:78` (op), `:80-81` (свотч)

- [ ] **Step 1: Дописать падающий тест donut**

Добавить в `tests/test_chart_palette.py`:

```python
def _assemble_one(template_id, content, theme="dark"):
    plan = DeckPlan(title="qa", slides=[SlidePlan(
        index=1, type="content", template_id=template_id, content=content)])
    return assemble(plan, theme=theme)


def test_donut_colors_by_index_no_opacity():
    content = {"title": "T", "segments": [
        {"label": f"S{i}", "value": str(v)}
        for i, v in enumerate([86, 72, 54, 40, 22])]}
    html = _assemble_one("donut-chart", content)
    # 5 сегментов → chart-1..5 и на дугах, и на свотчах легенды (по 2 вхождения)
    for n in range(1, 6):
        assert html.count(f"var(--chart-{n})") == 2, f"chart-{n} expected twice"
    # opacity-лесенка снята
    assert "stroke-opacity" not in html
    assert "fill-opacity" not in html
    # accent больше не красит сегменты donut
    assert 'stroke="var(--accent)"' not in html
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_chart_palette.py::test_donut_colors_by_index_no_opacity -v`
Expected: FAIL — сегменты ещё `var(--accent)` + `stroke-opacity`.

- [ ] **Step 3: Убрать `ladder` и `op`, покрасить дугу по индексу**

В `donut-chart.html` удалить строку 35:

```jinja
{% set ladder = [1.0, 0.78, 0.56, 0.38, 0.24] %}
```

Внутри группы дуг удалить строку 57 (`{% set op = ladder[...] %}`) и заменить блок дуги (строки 67-71):

```jinja
      <circle class="arc-draw" cx="{{ cx }}" cy="{{ cy }}" r="{{ R }}" fill="none"
              stroke="var(--accent)" stroke-opacity="{{ op }}" stroke-width="70"
              stroke-dasharray="{{ vis }} {{ C - vis }}"
              stroke-dashoffset="{{ -ns.offset }}"
              style="--draw-from:{{ -ns.offset - vis }};--draw-to:{{ -ns.offset }}"/>
```

на:

```jinja
      <circle class="arc-draw" cx="{{ cx }}" cy="{{ cy }}" r="{{ R }}" fill="none"
              stroke="var(--chart-{{ loop.index0 + 1 }})" stroke-width="70"
              stroke-dasharray="{{ vis }} {{ C - vis }}"
              stroke-dashoffset="{{ -ns.offset }}"
              style="--draw-from:{{ -ns.offset - vis }};--draw-to:{{ -ns.offset }}"/>
```

- [ ] **Step 4: Покрасить свотч легенды по индексу**

Удалить строку 78 (`{% set op = ladder[...] %}` в цикле легенды) и заменить свотч (строки 80-81):

```jinja
    <rect x="{{ leg_x }}" y="{{ ry - 12 }}" width="24" height="24" rx="4"
          fill="var(--accent)" fill-opacity="{{ op }}"/>
```

на:

```jinja
    <rect x="{{ leg_x }}" y="{{ ry - 12 }}" width="24" height="24" rx="4"
          fill="var(--chart-{{ loop.index0 + 1 }})"/>
```

- [ ] **Step 5: Переписать комментарий-шапку (donut-chart.html:7-10)**

Заменить строки 7-10:

```jinja
    Один бренд-акцент, без радуги: все сегменты залиты var(--accent), но
    различаются ступенями прозрачности (stroke-opacity ladder) — крупнейшая доля
    получает полную непрозрачность. Ladder: 1.0 / 0.78 / 0.56 / 0.38 / 0.24
    (по порядку сегментов; первым ставь самый крупный). Трек/дырка var(--bg-card).
```

на:

```jinja
    Раскраска по индексу сегмента (правило «≤3 хроматических цвета на слайд»):
    крупнейшая доля первой → var(--chart-1) (зелёный-герой), далее --chart-2..5
    (2 приглушённых доп-цвета + 2 серых). Полная непрозрачность у всех — различие
    даёт сам цвет/оттенок, без opacity-лесенки. Трек/дырка var(--bg-card).
```

- [ ] **Step 6: Запустить тесты — убедиться, что проходят**

Run: `.venv/bin/python -m pytest tests/test_chart_palette.py -v`
Expected: PASS — тест donut зелёный, Task 1 тесты не сломаны.

- [ ] **Step 7: Коммит**

```bash
git add tests/test_chart_palette.py htmlslides/templates/slides/donut-chart.html
git commit -m "$(cat <<'EOF'
feat(charts): donut красит сегменты по --chart-1..5 без opacity-лесенки

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: stacked-bar — раскраска `--chart-1..4`, свотчи легенды, фикс контраста %-метки

**Files:**
- Test: `tests/test_chart_palette.py` (дополнить)
- Modify: `htmlslides/templates/slides/stacked-bar.html:7-9` и `:26` (комм.), `:31` (ladder), `:53-55` (свотч), `:76` (op), `:83-84` (сегмент), `:90-93` (%-метка)

- [ ] **Step 1: Дописать падающий тест stacked-bar**

Добавить в `tests/test_chart_palette.py`:

```python
def test_stacked_colors_by_index_and_label_contrast():
    content = {
        "title": "T",
        "legend": [{"label": f"L{i}"} for i in range(4)],
        "bars": [{"label": "B1", "v1": "50", "v2": "30", "v3": "15", "v4": "5"},
                 {"label": "B2", "v1": "40", "v2": "35", "v3": "20", "v4": "5"}]}
    html = _assemble_one("stacked-bar", content)
    # свотчи легенды (4) + сегменты в 2 барах (4×2=8) → chart-1..4
    for n in range(1, 5):
        assert f"var(--chart-{n})" in html
    assert "fill-opacity" not in html
    # логотип _chrome.html красится var(--accent) в КАЖДОМ слайде → остаётся ровно 1;
    # важно, что сегменты/свотчи бара им больше не красятся (было 13: 12 + логотип)
    assert html.count('fill="var(--accent)"') == 1
    # %-метка фиксированно-графитовая (контраст в обеих темах); старый var(--bg) ушёл
    # (трек остаётся var(--bg-card) — это другая строка, её не задеваем)
    assert 'fill="var(--cl-graphite)"' in html
    assert 'fill="var(--bg)"' not in html
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_chart_palette.py::test_stacked_colors_by_index_and_label_contrast -v`
Expected: FAIL — сегменты/свотчи ещё `var(--accent)`+`fill-opacity`, метка `var(--bg)`.

- [ ] **Step 3: Убрать `ladder`**

В `stacked-bar.html` удалить строку 31:

```jinja
{% set ladder = [1.0, 0.78, 0.56, 0.38] %}
```

- [ ] **Step 4: Покрасить свотч легенды по индексу**

Удалить строку 53 (`{% set op = ladder[...] %}` в цикле легенды) и заменить свотч (строки 54-55):

```jinja
    <rect x="{{ lx }}" y="14" width="26" height="26" rx="0"
          fill="var(--accent)" fill-opacity="{{ op }}"/>
```

на:

```jinja
    <rect x="{{ lx }}" y="14" width="26" height="26" rx="0"
          fill="var(--chart-{{ loop.index0 + 1 }})"/>
```

- [ ] **Step 5: Покрасить сегмент по индексу**

Удалить строку 76 (`{% set op = ladder[...] %}` в цикле сегментов) и заменить `<rect class="sb-seg">` (строки 83-84):

```jinja
    <rect class="sb-seg" x="{{ bar_x + seg.offset }}" y="{{ y }}" width="{{ draww }}" height="{{ bar_h }}"
          fill="var(--accent)" fill-opacity="{{ op }}"/>
```

на:

```jinja
    <rect class="sb-seg" x="{{ bar_x + seg.offset }}" y="{{ y }}" width="{{ draww }}" height="{{ bar_h }}"
          fill="var(--chart-{{ loop.index0 + 1 }})"/>
```

- [ ] **Step 6: Фикс контраста %-метки**

Заменить `<text>` %-метки (строки 91-93):

```jinja
    <text x="{{ bar_x + seg.offset + draww / 2 }}" y="{{ cy }}" text-anchor="middle"
          dominant-baseline="middle" font-size="{{ 26 if sw >= 90 else 20 }}" font-weight="500"
          fill="var(--bg)">{{ pct }}%</text>
```

на:

```jinja
    <text x="{{ bar_x + seg.offset + draww / 2 }}" y="{{ cy }}" text-anchor="middle"
          dominant-baseline="middle" font-size="{{ 26 if sw >= 90 else 20 }}" font-weight="500"
          fill="var(--cl-graphite)">{{ pct }}%</text>
```

Причина: старый `var(--bg)` в тёмной теме = #222 (тёмный) — на новых непрозрачных сегментах читался плохо, а в светлой теме = #FFF (белый) — тонул на зелёном/пастельных. Фиксированный `var(--cl-graphite)` (#222) — крупная жирная %-метка (font-weight 500, ≥20px) читается на всех сегментах обеих тем (проверяется на рендере в Task 7).

- [ ] **Step 7: Переписать комментарии-шапку (строки 7-9) и геометрии (строка 26)**

Заменить строки 7-9:

```jinja
    Один бренд-акцент, без радуги: сегменты залиты var(--accent), различаются
    ступенями прозрачности (opacity ladder, как в donut): 1.0 / 0.78 / 0.56 / 0.38
    (по порядку сегментов v1..v4). Легенда сверху сопоставляет свотч → label.
```

на:

```jinja
    Раскраска по индексу сегмента v1..v4 → var(--chart-1..4) (правило «≤3
    хроматических цвета на слайд»): зелёный-герой + 2 приглушённых доп-цвета +
    серый. Полная непрозрачность у всех — различие даёт цвет, без opacity-лесенки.
    Легенда сверху сопоставляет свотч (те же --chart-*) → label.
```

Заменить в строке 26:

```jinja
      Сегмент i — var(--accent) с fill-opacity ladder[i]. Под баром — трек var(--bg-card).
```

на:

```jinja
      Сегмент i — var(--chart-{i+1}) (полная непрозрачность). Под баром — трек var(--bg-card).
```

- [ ] **Step 8: Запустить тесты — убедиться, что проходят**

Run: `.venv/bin/python -m pytest tests/test_chart_palette.py -v`
Expected: PASS — тест stacked зелёный, предыдущие не сломаны.

- [ ] **Step 9: Коммит**

```bash
git add tests/test_chart_palette.py htmlslides/templates/slides/stacked-bar.html
git commit -m "$(cat <<'EOF'
feat(charts): stacked-bar по --chart-1..4 + читаемая %-метка (graphite)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: bar-chart — раскраска `--chart-1..6` по индексу бара

**Files:**
- Test: `tests/test_chart_palette.py` (дополнить)
- Modify: `htmlslides/templates/slides/bar-chart.html:16` (комм. в docstring), `:58-59` (заливка бара)

**ВАЖНО:** в рабочем дереве `bar-chart.html` уже есть незакоммиченная правка (перенос длинных подписей на 2 строки, строки 39-55) — она **ортогональна** цвету. Её не трогаем и не откатываем: этот таск добавит цвет поверх и закоммитит оба изменения вместе.

> **Caveat по worktree:** незакоммиченная правка живёт только в *текущем* рабочем дереве. Если план исполняется в свежем git-worktree (subagent-driven), она туда НЕ попадёт — правка переноса подписей потеряется (цвет всё равно ляжет чисто: строки 58-59 есть и в HEAD). Чтобы не потерять её — либо исполнять этот план в текущем рабочем дереве (inline), либо закоммитить/перенести правку `bar-chart.html` до старта.

- [ ] **Step 1: Дописать падающий тест bar-chart**

Добавить в `tests/test_chart_palette.py`:

```python
def test_bar_colors_by_index():
    content = {"title": "T", "bars": [
        {"label": f"B{i}", "value": str(v)}
        for i, v in enumerate([86, 72, 54, 40, 28, 16])]}
    html = _assemble_one("bar-chart", content)
    # 6 баров → chart-1..6 (по разу на бар)
    for n in range(1, 7):
        assert f"var(--chart-{n})" in html
    # заливка бара больше не var(--accent) (трек остаётся var(--bg-card));
    # логотип _chrome.html держит ровно 1 var(--accent) в каждом слайде (было 7: 6 + логотип)
    assert html.count('fill="var(--accent)"') == 1
```

- [ ] **Step 2: Запустить — убедиться, что падает**

Run: `.venv/bin/python -m pytest tests/test_chart_palette.py::test_bar_colors_by_index -v`
Expected: FAIL — бар ещё `fill="var(--accent)"`.

- [ ] **Step 3: Покрасить бар по индексу**

В `bar-chart.html` заменить `<rect class="bar-fill">` (строки 58-59):

```jinja
    <rect class="bar-fill" x="{{ bar_x }}" y="{{ y }}" width="{{ bw if bw > 1 else 1 }}" height="{{ bar_h }}"
          fill="var(--accent)"/>
```

на:

```jinja
    <rect class="bar-fill" x="{{ bar_x }}" y="{{ y }}" width="{{ bw if bw > 1 else 1 }}" height="{{ bar_h }}"
          fill="var(--chart-{{ loop.index0 + 1 }})"/>
```

- [ ] **Step 4: Обновить строку docstring про цвет бара (bar-chart.html:16)**

Заменить в строке 16:

```jinja
      Бар — var(--accent) (зелёный на обеих темах); тонкая дорожка-трек
```

на:

```jinja
      Бар — var(--chart-{index+1}) по индексу (зелёный-герой + 2 доп + 3 серых,
      правило «≤3 хроматических цвета»); тонкая дорожка-трек
```

- [ ] **Step 5: Запустить тесты — убедиться, что проходят**

Run: `.venv/bin/python -m pytest tests/test_chart_palette.py -v`
Expected: PASS — все тесты палитры зелёные.

- [ ] **Step 6: Коммит (включая ортогональную правку переноса подписей)**

```bash
git add tests/test_chart_palette.py htmlslides/templates/slides/bar-chart.html
git commit -m "$(cat <<'EOF'
feat(charts): bar-chart красит бары по --chart-1..6 по индексу

Включает ранее незакоммиченный перенос длинных подписей на 2 строки
(ортогонально цвету).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Переписать комменты CSS-блоков графиков в deck.css

Комменты `.bar-svg`/`.donut-svg`/`.sb-svg` в `deck.css` всё ещё описывают старую одноцветную раскраску («один акцент, без радуги»). Приводим их в соответствие. Только комментарии — поведение не меняется.

**Files:**
- Modify: `htmlslides/engine/deck.css:290-294` (bar), `:297-302` (donut), `:314-320` (stacked)

- [ ] **Step 1: Переписать коммент bar-chart (deck.css:290-294)**

Заменить строки 290-294:

```css
/* ---- bar-chart (не из Figma; бренд-производная SVG-гистограмма) ----
   контейнер в контент-зоне x60..1860=1800px; вся геометрия — в SVG-атрибутах
   (viewBox 0 0 1800 620): геттер 520, бары от x=560, trackWidth 1100, значения
   справа. Бар var(--accent) на обеих темах, трек var(--bg-card), подписи
   var(--fg-body) — перекрашиваются темой, без per-theme переопределений. */
```

на:

```css
/* ---- bar-chart (не из Figma; бренд-производная SVG-гистограмма) ----
   контейнер в контент-зоне x60..1860=1800px; вся геометрия — в SVG-атрибутах
   (viewBox 0 0 1800 620): геттер 520, бары от x=560, trackWidth 1100, значения
   справа. Бар var(--chart-{index+1}) по индексу (зелёный-герой + 2 доп + 3 серых,
   правило ≤3 хроматических цвета), трек var(--bg-card), подписи var(--fg-body). */
```

- [ ] **Step 2: Переписать коммент donut-chart (deck.css:297-302)**

Заменить строки 297-302:

```css
/* ---- donut-chart (не из Figma; бренд-производное SVG-кольцо долей) ----
   контейнер в контент-зоне x60..1860=1800px; вся геометрия — в SVG-атрибутах
   (viewBox 0 0 1800 620): кольцо cx=310 cy=310 r=225 толщина 70 (дырка → донат),
   сегменты var(--accent) с разной stroke-opacity (один акцент, без радуги),
   трек var(--bg-card); легенда-текст var(--fg-body) — перекрашивается темой,
   без per-theme переопределений. */
```

на:

```css
/* ---- donut-chart (не из Figma; бренд-производное SVG-кольцо долей) ----
   контейнер в контент-зоне x60..1860=1800px; вся геометрия — в SVG-атрибутах
   (viewBox 0 0 1800 620): кольцо cx=310 cy=310 r=225 толщина 70 (дырка → донат),
   сегменты var(--chart-1..5) по индексу (зелёный + 2 доп + 2 серых, ≤3 хром. цвета),
   трек var(--bg-card); легенда-текст var(--fg-body) — перекрашивается темой,
   без per-theme переопределений. */
```

- [ ] **Step 3: Переписать коммент stacked-bar (deck.css:314-320)**

Заменить строки 314-320:

```css
/* ---- stacked-bar (не из Figma; бренд-производные SVG составные бары) ----
   контейнер в контент-зоне x60..1860=1800px; вся геометрия — в SVG-атрибутах
   (viewBox 0 0 1800 620): легенда сверху (свотчи opacity-ladder), бары ниже —
   геттер подписей 360, бар от x=400, трек на остаток ширины. Сегменты var(--accent) с
   разной fill-opacity (один акцент, без радуги; каждый бар = 100% состава),
   трек var(--bg-card); подписи var(--fg-body), %-метки в сегменте var(--bg) —
   перекрашиваются темой, без per-theme переопределений. */
```

на:

```css
/* ---- stacked-bar (не из Figma; бренд-производные SVG составные бары) ----
   контейнер в контент-зоне x60..1860=1800px; вся геометрия — в SVG-атрибутах
   (viewBox 0 0 1800 620): легенда сверху (свотчи --chart-1..4), бары ниже —
   геттер подписей 360, бар от x=400, трек на остаток ширины. Сегменты
   var(--chart-1..4) по индексу (зелёный + 2 доп + серый; каждый бар = 100% состава),
   трек var(--bg-card); подписи var(--fg-body), %-метки var(--cl-graphite) —
   перекрашиваются темой, без per-theme переопределений. */
```

- [ ] **Step 4: Прогнать тесты (комменты не должны ничего сломать)**

Run: `.venv/bin/python -m pytest tests/test_chart_palette.py -v`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add htmlslides/engine/deck.css
git commit -m "$(cat <<'EOF'
docs(charts): комменты CSS-блоков графиков на новую раскраску --chart-*

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Справочник `docs/brand/chart-colors.md`

**Files:**
- Create: `docs/brand/chart-colors.md`

- [ ] **Step 1: Создать каталог и файл**

```bash
mkdir -p docs/brand
```

Создать `docs/brand/chart-colors.md`:

```markdown
# Цвета графиков — справочник

**Авторитетный источник для движка** — комменты в `htmlslides/engine/deck.css`
(токены `--chart-*`) и шаблоны `htmlslides/templates/slides/*.html`. Этот файл —
человекочитаемая выжимка.

## Правило (общесистемное)

На одном слайде — **не более трёх хроматических (цветных) цветов**. Для дальнейшей
дифференциации — только **оттенки/тинты этих цветов** и **нейтральная серая шкала**.
Зелёный `#26D07C` — всегда цвет-герой, держит доминанту по массе. Насыщенные
(«живые», vivid) доп-цвета — только для обводок и точечных акцентов, **не** для
заливки графиков.

## Серия `--chart-1..6`

`--chart-1` (зелёный-герой) одинаков в обеих темах; `--chart-2..6` резолвятся темой.

| Токен | Роль | dark | light |
|---|---|---|---|
| `--chart-1` | зелёный-герой | `#26D07C` | `#26D07C` |
| `--chart-2` | ultramarine приглушённый | `#5D779F` | `#9CADCE` |
| `--chart-3` | purple приглушённый | `#8372A1` | `#BDB0D2` |
| `--chart-4` | серый gray3 | `#989898` | `#A6A6A6` |
| `--chart-5` | серый gray4 | `#737373` | `#8C8C8C` |
| `--chart-6` | серый gray5 | `#525252` | `#737373` |

## Что цветное, что зелёное, что в резерве

- **Композиционные графики** (раскрашиваются по индексу сегмента `--chart-*`):
  - `donut-chart` — до 5 сегментов → `--chart-1..5` (зелёный + 2 доп + 2 серых);
  - `stacked-bar` — до 4 сегментов v1..v4 → `--chart-1..4` (зелёный + 2 доп + серый),
    свотчи легенды — те же токены; %-метки — `--cl-graphite`;
  - `bar-chart` — до 6 баров → `--chart-1..6` (зелёный + 2 доп + 3 серых).
- **Метрики/тренды остаются зелёными** (цвет там не нёс бы смысла):
  `kpi`, `stats-row`, `kpi-rings`, `line-chart` → `var(--accent)`.
- **Резерв vivid** (только акценты/обводки, НЕ заливка графиков):
  `--cl-ultramarine`, `--cl-purple`, `--cl-carrot`, `--cl-coral`, `--cl-yellow`.

## Данные → цвет

Цвет назначается по **индексу сегмента**. Конвенция авторинга: данные приходят от
крупных к мелким → индекс 0 = крупнейший = зелёный-герой. Сортировки в шаблонах нет.
```

- [ ] **Step 2: Проверить, что файл на месте**

Run: `test -f docs/brand/chart-colors.md && echo OK`
Expected: `OK`

- [ ] **Step 3: Коммит**

```bash
git add docs/brand/chart-colors.md
git commit -m "$(cat <<'EOF'
docs(brand): справочник цветов графиков (--chart-* + правило ≤3 цвета)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Визуальная верификация обеих тем + подбор светлых серых

Автотесты подтверждают токены и разметку; но «рецессивность» светлых серых, доминанту
зелёного и читаемость %-метки на тёмных сегментах надо проверить **глазами** на реальном
рендере через движок (`assemble` + `screenshot_slides`).

**Files:**
- Create (временный): `_qa_charts.py`
- Возможная правка: `htmlslides/engine/deck.css` (значения `--chart-4..6` для light)

- [ ] **Step 1: Создать временный харнесс рендера**

Создать `_qa_charts.py`:

```python
"""ВРЕМЕННЫЙ харнесс: рендер donut(5)/stacked(4-сег)/bar(6) в обеих темах
через реальный движок. Снимает PNG для глаз-проверки палитры. Удаляется после."""
import pathlib

from htmlslides.assembler import assemble
from htmlslides.models import DeckPlan, SlidePlan
from htmlslides.pipeline.screenshot import screenshot_slides

OUT = pathlib.Path("_shots"); OUT.mkdir(exist_ok=True)

SLIDES = [
    SlidePlan(index=1, type="content", template_id="donut-chart", content={
        "title": "Donut · 5 сегментов",
        "segments": [{"label": l, "value": str(v)} for l, v in [
            ("Вычисления", 86), ("Хранение", 72), ("Сеть", 54),
            ("Базы данных", 40), ("Прочее", 22)]]}),
    SlidePlan(index=2, type="content", template_id="stacked-bar", content={
        "title": "Stacked · 4 сегмента",
        "legend": [{"label": l} for l in ["Вычисления", "Хранение", "Сеть", "Бэкап"]],
        "bars": [
            {"label": "Прод", "v1": "50", "v2": "30", "v3": "15", "v4": "5"},
            {"label": "Стейдж", "v1": "40", "v2": "35", "v3": "20", "v4": "5"},
            {"label": "Дев", "v1": "30", "v2": "40", "v3": "25", "v4": "5"}]}),
    SlidePlan(index=3, type="content", template_id="bar-chart", content={
        "title": "Bar · 6 баров",
        "bars": [{"label": l, "value": str(v)} for l, v in [
            ("Вычисления", 86), ("Хранение", 72), ("Сеть", 54),
            ("Базы данных", 40), ("CDN", 28), ("Прочее", 16)]]}),
]

for theme in ("dark", "light"):
    plan = DeckPlan(title="qa-charts", slides=SLIDES)
    html = assemble(plan, theme=theme)
    hp = OUT / f"_charts_{theme}.html"
    hp.write_text(html, encoding="utf-8")
    shots = screenshot_slides(hp, [1, 2, 3], OUT / theme)
    for i, p in shots.items():
        print(theme, i, p)
print("DONE")
```

- [ ] **Step 2: Прогнать харнесс**

Run: `.venv/bin/python _qa_charts.py`
Expected: печатает 6 путей (`dark 1..3`, `light 1..3`) и `DONE`; PNG лежат в `_shots/dark/` и `_shots/light/`.

- [ ] **Step 3: Осмотреть PNG (глаз-проверка)**

Открыть `_shots/dark/qa-slide-01..03.png` и `_shots/light/qa-slide-01..03.png`. Проверить:
1. **Зелёный доминирует** по массе; ultramarine/purple приглушены и уступают; серые — хвост.
2. **Светлые серые `--chart-4..6`** в light-теме **рецессивны** — не тяжелее пастельных `--chart-2/3`. Если серый «перетягивает» пастель — осветлить значения в light-блоке (Step 4).
3. **%-метки stacked-bar** читаются на всех сегментах обеих тем (в т.ч. на приглушённых доп-цветах). Если на каком-то сегменте `--cl-graphite` слаб — см. Step 5 (контингенция).

- [ ] **Step 4: (Если нужно) подобрать светлые серые**

Если серые не рецессивны, отредактировать в `deck.css` блок light `--chart-4..6` (осветлить, напр. `#B3B3B3`/`#999999`/`#808080`), затем повторить Step 2-3. Стартовые значения `#A6A6A6`/`#8C8C8C`/`#737373` — гипотеза из спеки, правим только по факту рендера.

- [ ] **Step 5: (Контингенция) %-метка на приглушённых сегментах**

Если `var(--cl-graphite)` местами нечитаем: сделать метку зависимой от индекса — зелёный сегмент (index 0) держит `var(--cl-graphite)`, остальные — `var(--fg-body)`. В `stacked-bar.html` заменить `fill="var(--cl-graphite)"` на:

```jinja
          fill="{{ 'var(--cl-graphite)' if loop.index0 == 0 else 'var(--fg-body)' }}"
```

и в тесте Task 3 ослабить ассерт до `assert "var(--cl-graphite)" in html`. Повторить Step 2-3. **Применять только если рендер этого требует.**

- [ ] **Step 6: Финальный прогон всех тестов**

Run: `.venv/bin/python -m pytest tests/test_chart_palette.py tests/test_render_png.py -v`
Expected: PASS.

- [ ] **Step 7: Коммит правок light-серых (если были в Step 4/5)**

```bash
git add htmlslides/engine/deck.css htmlslides/templates/slides/stacked-bar.html tests/test_chart_palette.py
git commit -m "$(cat <<'EOF'
fix(charts): подбор светлых серых --chart-4..6 по рендеру

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

Если правок не было — шаг пропустить (пустой коммит не создавать).

---

## Task 8: Уборка временных файлов

**Files:**
- Delete: `_qa_charts.py`, `_qa_palette.py`, `_shots/` (все временные PNG/HTML)

- [ ] **Step 1: Убедиться, что временные файлы не отслеживаются git**

Run: `git status --short _qa_charts.py _qa_palette.py _shots/`
Expected: все — `??` (untracked). Если что-то `A`/`M` — НЕ удалять, разобраться.

- [ ] **Step 2: Удалить временные файлы**

```bash
rm -f _qa_charts.py _qa_palette.py
rm -rf _shots/
```

- [ ] **Step 3: Проверить чистоту рабочего дерева**

Run: `git status --short`
Expected: нет незакоммиченных изменений в `htmlslides/`, `tests/`, `docs/` (кроме, возможно, `.DS_Store` — их не трогаем). Временных `_qa_*`/`_shots/` больше нет.

---

## Итоговая проверка (после всех тасков)

- [ ] `.venv/bin/python -m pytest tests/ -q` — весь сюит зелёный (нет регрессий).
- [ ] `git log --oneline -8` — коммиты по таскам на месте, temp-файлы не попали в историю.
- [ ] Глаз-проверка PNG (Task 7) пройдена в обеих темах для всех трёх графиков.
