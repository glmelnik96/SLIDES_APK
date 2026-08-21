# «Заполнить шаблон» — открытие ручного входа: план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Открыть точку входа «Заполнить шаблон» на главной и закрыть 4 UX-пробела конструктора: пустой старт, тяжёлый пикер (22 live-iframe), кривой бейдж «15 типов», выключенная карточка.

**Architecture:** Скелет из трёх слайдов — opt-in флаг `skeleton:true` в существующем `POST /api/drafts` (glass не задет). PNG-миниатюры пикера — новый модуль `webapp/tpl_thumbs.py` (Playwright-скриншот превью-HTML, дисковый кэш, инвалидация по mtime `library.json`) + эндпоинты `/thumb`; при недоступном Chromium — 404 и клиентский фолбэк на текущий live-iframe. Группировка каталога — чистая JS-функция в отдельном модуле (паттерн `errtext.js`), тестируемая `node --test`.

**Tech Stack:** FastAPI, Pydantic (webapp/draft.py), Playwright sync API, vanilla JS (UMD-модули в webapp/static), pytest + node --test.

**Спека:** `docs/superpowers/specs/2026-08-21-manual-entry-design.md`

---

### Task 0: Коммит спеки и плана

- [ ] **Step 0.1: Проверить чистоту дерева**

Run: `git status --short`
Expected: только новые `docs/superpowers/specs/2026-08-21-manual-entry-design.md` и `docs/superpowers/plans/2026-08-21-manual-entry.md` (`??`). Если есть другие изменения — остановиться и разобраться, что это.

- [ ] **Step 0.2: Commit**

```bash
git add docs/superpowers/specs/2026-08-21-manual-entry-design.md docs/superpowers/plans/2026-08-21-manual-entry.md
git commit -m "docs: спека и план открытия ручного входа «Заполнить шаблон»"
```

---

### Task 1: Скелет при создании черновика (сервер)

**Files:**
- Modify: `webapp/app.py:288-311` (`create_draft`)
- Test: `tests/test_draft.py` (после `test_create_draft_rejects_bad_mode`, ~строка 185)

- [ ] **Step 1.1: Написать падающие тесты**

В `tests/test_draft.py` после `test_create_draft_rejects_bad_mode` добавить (импорты `draft`, `_client`, `H` в файле уже есть):

```python
def test_create_draft_skeleton_flag(monkeypatch, tmp_path):
    """skeleton:true → план сразу с тремя пустыми слайдами cover→blank→contacts.

    Флаг шлёт ТОЛЬКО кнопка «Заполнить шаблон» на главной — лечит паралич
    чистого листа. Контент пуст: превью-коэрсия показывает плейсхолдеры."""
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/api/drafts", json={"mode": "manual", "skeleton": True},
                   headers=H())
        assert r.status_code == 200
        sid = r.json()["session_id"]
        plan = draft.load_plan(sid)
        assert [s.template_id for s in plan.slides] == ["cover", "blank", "contacts"]
        assert all(not s.filled and not s.content for s in plan.slides)
        # deck.html отрендерен сразу с тремя секциями (DeckPlan-as-truth)
        deck = c.get(f"/api/jobs/{sid}/deck", headers=H())
        assert deck.text.count("<section") == 3


def test_create_draft_without_skeleton_stays_empty(monkeypatch, tmp_path):
    """Без флага (glass, старые вызовы) — пустой план, как раньше."""
    with _client(monkeypatch, tmp_path) as c:
        sid = c.post("/api/drafts", json={"mode": "manual"},
                     headers=H()).json()["session_id"]
        assert draft.load_plan(sid).slides == []
```

- [ ] **Step 1.2: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_draft.py -k skeleton -x -q`
Expected: FAIL — `assert [] == ['cover', 'blank', 'contacts']`

- [ ] **Step 1.3: Реализация**

В `webapp/app.py` `create_draft`, сразу после строки `plan = draft.DraftPlan(title=str(data.get("title") or ""))`:

```python
    # Скелет «обложка → пустой → контакты» — opt-in ТОЛЬКО для кнопки
    # «Заполнить шаблон» на главной: старт со структурой вместо чистого листа.
    # Glass и существующие вызовы флаг не шлют — их путь не меняется (риск
    # «скелет просочился в glass» закрыт явным opt-in).
    if mode == "manual" and bool(data.get("skeleton")):
        plan.slides = [draft.DraftSlide(template_id=tid)
                       for tid in ("cover", "blank", "contacts")]
```

- [ ] **Step 1.4: Прогнать тесты**

Run: `python -m pytest tests/test_draft.py tests/test_glass.py -q`
Expected: PASS (в т.ч. все glass-тесты — план без флага не изменился)

- [ ] **Step 1.5: Commit**

```bash
git add webapp/app.py tests/test_draft.py
git commit -m "feat(draft): opt-in скелет cover→blank→contacts при создании manual-черновика"
```

---

### Task 2: PNG-миниатюры пикера — модуль + эндпоинты

**Files:**
- Create: `webapp/tpl_thumbs.py`
- Modify: `webapp/app.py:356-410` (рефактор `template_preview`/`diagram_kind_preview` + 2 новых эндпоинта)
- Test: `tests/test_thumbs.py` (новый)

- [ ] **Step 2.1: Создать `webapp/tpl_thumbs.py`**

```python
"""PNG-миниатюры превью макетов/типов схем для пикера конструктора.

22 живых full-deck iframe — главная тяжесть пикера: простыня ~3000px тормозит
слабые машины. Клиент просит статичный PNG; генерим лениво при первом запросе
той же Playwright-скриншотилкой, что vision-QA, и кэшируем на диске.
Chromium недоступен → ThumbUnavailable → эндпоинт отдаёт 404, клиент падает
обратно на live-iframe (мягкая деградация, поведение как до этой фичи).
"""
from __future__ import annotations

import tempfile
from importlib import resources
from pathlib import Path

from webapp import paths


class ThumbUnavailable(RuntimeError):
    """Playwright/Chromium недоступен — вызывающему вернуть 404."""


def cache_dir() -> Path:
    # Рядом с сессиями: <workdir>/../tpl-thumbs. На VM SLIDESBOT_WORKDIR=
    # /opt/app2/data/sessions → кэш в /opt/app2/data/tpl-thumbs (внутри
    # ReadWritePaths юнита); локально — tempdir/slidesapp/tpl-thumbs.
    d = paths.workdir_root().parent / "tpl-thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _library_mtime() -> float:
    with resources.as_file(resources.files("htmlslides") / "templates"
                           / "library.json") as p:
        return p.stat().st_mtime


def get_thumb(key: str, theme: str, render_html) -> Path:
    """PNG по ключу кэша; render_html() зовётся только на промахе.

    Инвалидация — по mtime library.json: правка каталога делает все
    закэшированные PNG старее файла, и они перегенерятся сами."""
    png = cache_dir() / f"{key}-{theme}.png"
    if png.exists() and png.stat().st_mtime >= _library_mtime():
        return png
    _render_png(render_html(), png)
    return png


def _render_png(html: str, out: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:                     # extra [qa] не установлен
        raise ThumbUnavailable("playwright не установлен") from exc
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "preview.html"
        src.write_text(html, encoding="utf-8")
        tmp = out.with_name(out.name + ".tmp")
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                # 480×270: дека сама вписывает слайд 1920×1080 в вьюпорт
                # (transform-scale, как в deck.js) — готовая миниатюра без
                # ресайза. reduced_motion — финальные кадры (паттерн
                # htmlslides/pipeline/screenshot.py).
                page = browser.new_page(
                    viewport={"width": 480, "height": 270},
                    reduced_motion="reduce")
                page.goto(src.resolve().as_uri())
                page.add_style_tag(content=".deck-progress{display:none}")
                page.wait_for_timeout(200)
                page.screenshot(path=str(tmp))
                browser.close()
        except Exception as exc:              # chromium недоступен / краш
            tmp.unlink(missing_ok=True)
            raise ThumbUnavailable(str(exc)) from exc
        tmp.replace(out)   # атомарно: полузаписанный PNG не попадёт в кэш
```

- [ ] **Step 2.2: Написать падающие тесты `tests/test_thumbs.py`**

Сначала открыть `tests/test_draft.py:1-80` и посмотреть, как устроены `_client(monkeypatch, tmp_path)` и `H()`. Импортировать их из test_draft (в проекте tests импортируются между собой — проверить по `tests/test_glass.py:926`, там тот же паттерн; если импорт не работает — скопировать хелперы дословно).

```python
"""Thumb-эндпоинты пикера: ленивая генерация, кэш, инвалидация, 404-фолбэк."""
from webapp import tpl_thumbs
from tests.test_draft import H, _client


def _fake_render(calls):
    def fake(html, out):
        calls.append(html)
        out.write_bytes(b"\x89PNG-fake")
    return fake


def test_thumb_generates_then_serves_from_cache(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tpl_thumbs, "_render_png", _fake_render(calls))
    with _client(monkeypatch, tmp_path) as c:
        r1 = c.get("/api/templates/cover/thumb?theme=dark", headers=H())
        assert r1.status_code == 200
        assert r1.headers["content-type"] == "image/png"
        r2 = c.get("/api/templates/cover/thumb?theme=dark", headers=H())
        assert r2.status_code == 200
        assert len(calls) == 1          # второй запрос — из кэша


def test_thumb_theme_is_separate_cache_entry(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tpl_thumbs, "_render_png", _fake_render(calls))
    with _client(monkeypatch, tmp_path) as c:
        c.get("/api/templates/cover/thumb?theme=dark", headers=H())
        c.get("/api/templates/cover/thumb?theme=light", headers=H())
        assert len(calls) == 2
        # светлая тема реально уходит в рендер светлой декой
        assert 'data-theme="light"' in calls[1]


def test_thumb_regenerated_after_catalog_change(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tpl_thumbs, "_render_png", _fake_render(calls))
    monkeypatch.setattr(tpl_thumbs, "_library_mtime", lambda: 0.0)
    with _client(monkeypatch, tmp_path) as c:
        c.get("/api/templates/cover/thumb", headers=H())
        # «каталог обновили»: mtime library.json стал больше mtime PNG
        monkeypatch.setattr(tpl_thumbs, "_library_mtime", lambda: 1e12)
        c.get("/api/templates/cover/thumb", headers=H())
        assert len(calls) == 2


def test_thumb_unknown_template_404(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/api/templates/nope/thumb",
                     headers=H()).status_code == 404


def test_thumb_chromium_unavailable_404(monkeypatch, tmp_path):
    """Chromium нет → 404 → клиент падает на live-iframe (мягкая деградация)."""
    def boom(html, out):
        raise tpl_thumbs.ThumbUnavailable("no chromium")
    monkeypatch.setattr(tpl_thumbs, "_render_png", boom)
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/api/templates/cover/thumb",
                     headers=H()).status_code == 404


def test_diagram_thumb(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tpl_thumbs, "_render_png", _fake_render(calls))
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/api/diagrams/flowchart/thumb", headers=H())
        assert r.status_code == 200 and len(calls) == 1
        assert c.get("/api/diagrams/nope/thumb",
                     headers=H()).status_code == 404
```

Примечание: `data-theme="light"` — проверить реальный маркер темы в собранном HTML (`htmlslides/assembler.py`, `assemble(plan, theme="light")`); если атрибут другой — подставить фактический.

- [ ] **Step 2.3: Убедиться, что тесты падают**

Run: `python -m pytest tests/test_thumbs.py -x -q`
Expected: FAIL — 404 у `/thumb` (роут не существует)

- [ ] **Step 2.4: Рефактор превью + эндпоинты в `webapp/app.py`**

Вынести тело `template_preview` в хелпер с параметром темы (existing endpoint ведёт себя как раньше):

```python
def _template_preview_html(template_id: str, theme: str = "dark") -> str:
    from htmlslides.assembler import assemble
    from htmlslides.library import TemplateLibrary
    from htmlslides.models import DeckPlan, SlidePlan
    from webapp import templates_api
    try:
        spec = TemplateLibrary.load().get(template_id)
    except Exception:  # noqa: BLE001
        raise HTTPException(404, "unknown template")
    plan = DeckPlan(title="", slides=[SlidePlan(
        index=1, type=spec.type, template_id=template_id,
        content=templates_api.sample_content(template_id))])
    return assemble(plan, theme=theme)


@app.get("/api/templates/{template_id}/preview", response_class=HTMLResponse)
def template_preview(template_id: str, static: bool = False,
                     user=Depends(get_current_user)):
    """(докстринг существующий — не менять)"""
    html = _template_preview_html(template_id)
    if static:
        html = html.replace("</head>", _PREVIEW_QUIET_STYLE + "</head>", 1)
    return HTMLResponse(html)
```

Тот же рефактор для `diagram_kind_preview` (app.py:388-…, прочитать тело перед правкой) → `_diagram_preview_html(kind: str, theme: str = "dark") -> str` (404 при неизвестном/недоступном kind остаётся внутри хелпера).

Новые эндпоинты (рядом с превью; `FileResponse` — проверить импорт из `fastapi.responses` в шапке app.py, добавить при отсутствии):

```python
def _thumb_response(key: str, theme: str, html: str):
    from webapp import tpl_thumbs
    try:
        png = tpl_thumbs.get_thumb(key, theme, lambda: html)
    except tpl_thumbs.ThumbUnavailable:
        # 404 → клиент падает обратно на live-iframe (мягкая деградация)
        raise HTTPException(404, "миниатюра недоступна")
    return FileResponse(png, media_type="image/png")


@app.get("/api/templates/{template_id}/thumb")
def template_thumb(template_id: str, theme: str = "dark",
                   user=Depends(get_current_user)):
    """Статичная PNG-миниатюра макета для пикера: кэш на диске, ленивая
    генерация Playwright'ом. 404 и при неизвестном макете, и при недоступном
    Chromium — клиент в обоих случаях показывает live-iframe превью."""
    if theme not in ("dark", "light"):
        theme = "dark"
    html = _template_preview_html(template_id, theme).replace(
        "</head>", _PREVIEW_QUIET_STYLE + "</head>", 1)
    return _thumb_response(f"tpl-{template_id}", theme, html)


@app.get("/api/diagrams/{kind}/thumb")
def diagram_thumb(kind: str, theme: str = "dark",
                  user=Depends(get_current_user)):
    """PNG-миниатюра типа схемы (второй шаг мастера «Схема») — тем же способом."""
    if theme not in ("dark", "light"):
        theme = "dark"
    html = _diagram_preview_html(kind, theme).replace(
        "</head>", _PREVIEW_QUIET_STYLE + "</head>", 1)
    return _thumb_response(f"dgm-{kind}", theme, html)
```

- [ ] **Step 2.5: Прогнать тесты**

Run: `python -m pytest tests/test_thumbs.py tests/test_draft.py -q`
Expected: PASS (в т.ч. существующие превью-тесты test_draft.py:410-414)

- [ ] **Step 2.6: Разовый живой рендер (локальный Chromium)**

Run: `python -c "from webapp.app import _template_preview_html, _PREVIEW_QUIET_STYLE; from webapp import tpl_thumbs; h=_template_preview_html('cover').replace('</head>', _PREVIEW_QUIET_STYLE+'</head>',1); p=tpl_thumbs.get_thumb('tpl-cover','dark',lambda: h); print(p, p.stat().st_size)"`
Expected: путь к PNG, размер > 5000 байт. Открыть файл Read-инструментом (это изображение) и глазами проверить: слайд-обложка занимает весь кадр 480×270, не крошечный угол. Если слайд НЕ вписался (дека не масштабирует под вьюпорт) — переключить рендер на viewport 1920×1080 в `_render_png` и добавить в CSS клиента масштабирование `<img>` (объект-фит уже это делает — см. Task 4), размер кэша при этом вырастет, что приемлемо.

- [ ] **Step 2.7: Commit**

```bash
git add webapp/tpl_thumbs.py webapp/app.py tests/test_thumbs.py
git commit -m "feat(picker): PNG-миниатюры макетов и типов схем — ленивый дисковый кэш, 404-фолбэк"
```

---

### Task 3: Группировка каталога — чистая JS-функция

**Files:**
- Create: `webapp/static/picker_groups.js`
- Test: `tests/js/picker_groups.test.js`

- [ ] **Step 3.1: Написать падающий тест `tests/js/picker_groups.test.js`**

```js
const test = require("node:test");
const assert = require("node:assert/strict");
const { groupTemplates } = require("../../webapp/static/picker_groups.js");

// Полный видимый каталог (22 макета, порядок как в library.json — см.
// webapp/templates_api.py; hidden cards-6 в пикер не попадает).
const IDS = ["cover", "cover-image", "statement", "statement-green", "contacts",
  "kpi", "stats-row", "bar-chart", "donut-chart", "line-chart", "stacked-bar",
  "kpi-rings", "before-after", "service-table", "quote", "timeline",
  "two-col-cards", "three-col", "grid-2x2", "frames-grid", "blank", "diagram"];

test("22 макета раскладываются в 4 группы без потерь и дублей", () => {
  const groups = groupTemplates(IDS.map((id) => ({ id })));
  assert.deepEqual(groups.map((g) => g.label), ["Обложки и финал",
    "Цифры и графики", "Сравнение и структура", "Текст и карточки"]);
  const flat = groups.flatMap((g) => g.items.map((t) => t.id));
  assert.equal(flat.length, IDS.length);
  assert.deepEqual([...flat].sort(), [...IDS].sort());
  assert.deepEqual(groups[0].items.map((t) => t.id),
    ["cover", "cover-image", "contacts"]);
});

test("неизвестный макет падает в «Другие макеты», пустых групп нет", () => {
  const groups = groupTemplates([{ id: "cover" }, { id: "brand-new" }]);
  assert.deepEqual(groups.map((g) => g.label),
    ["Обложки и финал", "Другие макеты"]);
  assert.deepEqual(groups[1].items.map((t) => t.id), ["brand-new"]);
});

test("пустой/отсутствующий каталог → пусто", () => {
  assert.deepEqual(groupTemplates([]), []);
  assert.deepEqual(groupTemplates(null), []);
});
```

- [ ] **Step 3.2: Убедиться, что тест падает**

Run: `node --test tests/js/picker_groups.test.js`
Expected: FAIL — Cannot find module '../../webapp/static/picker_groups.js'

- [ ] **Step 3.3: Реализация `webapp/static/picker_groups.js`**

Перед написанием посмотреть конец `webapp/static/errtext.js` — повторить его UMD-хвост дословно (module.exports для Node, window.* для браузера).

```js
// Группировка каталога макетов для пикера конструктора: простыня из 22 плиток
// без групп не сканируется глазами. Отдельный модуль без DOM — чистая функция
// тестируется через `node --test`; в браузере подключается ПЕРЕД editor.js.
(function (root) {
  // Порядок групп = порядок в пикере. Макеты, не приписанные ни к одной группе
  // (будущие), падают в «Другие макеты» — каталог расширяем без правок здесь.
  var GROUPS = [
    ["Обложки и финал", ["cover", "cover-image", "contacts"]],
    ["Цифры и графики", ["kpi", "stats-row", "bar-chart", "donut-chart",
      "line-chart", "stacked-bar", "kpi-rings"]],
    ["Сравнение и структура", ["before-after", "timeline", "diagram"]],
    ["Текст и карточки", ["statement", "statement-green", "quote",
      "service-table", "two-col-cards", "three-col", "grid-2x2",
      "frames-grid", "blank"]],
  ];

  // catalog: [{id, …}] (уже без hidden) → [{label, items}] без пустых групп.
  function groupTemplates(catalog) {
    var by = {};
    (catalog || []).forEach(function (t) { by[t.id] = t; });
    var used = {};
    var out = [];
    GROUPS.forEach(function (g) {
      var items = g[1].map(function (id) { used[id] = true; return by[id]; })
        .filter(Boolean);
      if (items.length) out.push({ label: g[0], items: items });
    });
    var rest = (catalog || []).filter(function (t) { return !used[t.id]; });
    if (rest.length) out.push({ label: "Другие макеты", items: rest });
    return out;
  }

  var api = { groupTemplates: groupTemplates };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PickerGroups = api;
})(typeof window !== "undefined" ? window : globalThis);
```

- [ ] **Step 3.4: Прогнать JS-тесты**

Run: `node --test tests/js/picker_groups.test.js` затем полный набор `node --test tests/js/*.test.js` (на Windows перечислить файлы, если глоб не раскрывается)
Expected: PASS, всего = старые 156 + 3 новых

- [ ] **Step 3.5: Commit**

```bash
git add webapp/static/picker_groups.js tests/js/picker_groups.test.js
git commit -m "feat(picker): группировка каталога макетов — чистая функция + node --test"
```

---

### Task 4: Пикер — PNG-плитки с фолбэком, группы, нормальный бейдж

**Files:**
- Modify: `webapp/static/editor.js:3127-3205` (`openPicker`), `:3234-3285` (`openDiagramPicker`)
- Modify: `webapp/static/editor.html:198-201` (подключить модуль)
- Modify: `webapp/static/styles.css:886-951` (плитки, группы, бейдж)

- [ ] **Step 4.1: Подключить модуль в `editor.html`**

Перед `<script src="/static/editor.js"></script>` (строка ~201):

```html
<script src="/static/picker_groups.js"></script>
```

- [ ] **Step 4.2: Хелпер превью-плитки в `editor.js`**

Рядом с `curBadge()` (~строка 3117):

```js
// Плитка превью: статичный PNG (лёгкий) с фолбэком на live-iframe (прежнее
// поведение) — сервер отдаёт 404, когда Chromium недоступен, и на любом
// сбое картинки пикер продолжает работать как раньше.
function thumbPreview(prev, thumbUrl, iframeUrl) {
  const img = document.createElement("img");
  img.className = "picker-thumb";
  img.loading = "lazy";
  img.alt = "";
  img.onerror = () => {
    const ifr = document.createElement("iframe");
    ifr.loading = "lazy";
    ifr.tabIndex = -1;
    ifr.src = iframeUrl;
    img.replaceWith(ifr);
  };
  img.src = thumbUrl;
  prev.appendChild(img);
}
```

- [ ] **Step 4.3: Переписать тело `openPicker` — группы + PNG + бейдж в meta**

Заменить блок `pickable.forEach((t, i) => { … });` (editor.js:3147-3204) на:

```js
  // Тема миниатюр = тема черновика: пикер показывает то, что реально встанет.
  const th = draftPlan && draftPlan.theme === "light" ? "light" : "dark";
  let num = 0;
  PickerGroups.groupTemplates(pickable).forEach((group) => {
    const head = document.createElement("div");
    head.className = "picker-group";
    head.textContent = group.label;
    grid.appendChild(head);
    group.items.forEach((t) => {
      num += 1;
      const isCur = !!curId && t.id === curId;
      // Мастер, а не макет: за карточкой — второй шаг со списком типов схем.
      const wizard = t.id === "diagram" && kinds.length > 1;
      const card = document.createElement("button");
      card.type = "button";
      card.className = "picker-item" + (isCur ? " picker-item--current" : "");
      const prev = document.createElement("div");
      prev.className = "picker-prev";
      thumbPreview(prev, U(`/api/templates/${t.id}/thumb?theme=${th}`),
                   U(`/api/templates/${t.id}/preview?static=1`));
      if (isCur) prev.appendChild(curBadge());
      const n = document.createElement("span");
      n.className = "picker-num";
      n.textContent = String(num).padStart(2, "0");
      prev.appendChild(n);
      const meta = document.createElement("div");
      meta.className = "picker-meta";
      // К§2: имя крупно; сырой id — приглушённой строкой. Бейдж «N типов» —
      // обычный чип в строке имени (раньше — плашка поверх превью, вклеенная
      // криво; фикс вёрстки из спеки).
      meta.innerHTML = `<span class="picker-id">${t.display_name || t.id}` +
        (wizard ? ` <span class="picker-badge">${kinds.length} ` +
          `${plural(kinds.length, "тип", "типа", "типов")}</span>` : "") +
        `</span>` +
        `<span class="picker-intent">${t.intent || ""}</span>` +
        (t.display_name ? `<span class="picker-code">${t.id}</span>` : "");
      if (wizard) {
        // «Много» абстрактно, а «блок-схема, воронка, цикл…» говорит, что откроется.
        const more = document.createElement("span");
        more.className = "picker-more";
        const names = kinds.slice(0, 3).map((k) => k.display_name || k.kind);
        more.textContent = "Откроется выбор: " + names.join(", ")
          + (kinds.length > names.length ? " и другие" : "");
        meta.appendChild(more);
      }
      card.appendChild(prev);
      card.appendChild(meta);
      card.onclick = () => {
        picker.classList.add("hidden");
        if (t.id === "diagram") openDiagramPicker((kind) => onPick(t.id, kind),
                                                  cur && cur.kind);
        // Уже выбранная карточка просто закрывает пикер (пересадка = delete+add,
        // у диаграммного слайда она сбрасывала бы схему к примеру).
        else if (!isCur) onPick(t.id);
      };
      grid.appendChild(card);
    });
  });
```

Существующие комментарии до/после блока (docstring openPicker, пустое состояние) сохранить. Класс `.picker-wizard` в JS больше не используется.

- [ ] **Step 4.4: PNG в `openDiagramPicker`**

В ветке `if (t.available)` (editor.js:3256-3262) заменить создание iframe на:

```js
      const th = draftPlan && draftPlan.theme === "light" ? "light" : "dark";
      thumbPreview(prev, U(`/api/diagrams/${t.kind}/thumb?theme=${th}`),
                   U(`/api/diagrams/${t.kind}/preview?static=1`));
      if (isCur) prev.appendChild(curBadge());
```

(`const th` вынести перед `cat.forEach`, чтобы не вычислять в цикле.)

- [ ] **Step 4.5: CSS в `styles.css`**

1. После правила `.picker-prev iframe { … }` (:912-913) добавить:

```css
/* PNG-миниатюра (лёгкий путь): рендер 480×270 растягивается на плитку 318×179.
   Фолбэк-iframe при 404 использует правило выше — масштаб живого рендера. */
.picker-thumb { position: absolute; inset: 0; width: 100%; height: 100%;
  object-fit: cover; display: block; }
```

2. Удалить правило `.picker-wizard { … }` (:931-936 вместе с комментарием) и на его месте:

```css
/* «Схема» — мастер, а не макет: чип «15 типов» в строке имени (раньше —
   плашка поверх превью, вклеенная в вёрстку криво). */
.picker-badge { display: inline-block; vertical-align: 2px; margin-left: 6px;
  padding: 2px 8px; border-radius: 999px; font-size: var(--fs-cap);
  font-weight: 600; letter-spacing: .03em; color: var(--muted);
  border: 1px solid var(--line); }
```

3. После правила `.picker-grid` (:886-888) добавить:

```css
/* Заголовки групп каталога — вся строка грида, тихая капитель (канон v5). */
.picker-group { grid-column: 1 / -1; margin: 6px 0 -6px; padding: 0 2px;
  font-size: var(--fs-cap); font-weight: 600; letter-spacing: .08em;
  text-transform: uppercase; color: var(--muted-2); }
.picker-group:first-child { margin-top: 0; }
```

- [ ] **Step 4.6: Проверка в preview**

Перезапустить preview-сервер (backend менялся в Task 2): `preview_stop` + `preview_start` (`python scripts/devserver.py`, порт 8021). Затем: создать manual-черновик, открыть пикер («+ Добавить слайд» / «Сменить макет»):
- `preview_network`: запросы `/api/templates/*/thumb` → 200 image/png (или 404 → в DOM живые iframe);
- `preview_snapshot`: 4 заголовка групп, 22 плитки, бейдж «15 типов» в строке имени «Схема»;
- открыть мастер «Схема»: плитки типов тоже `<img>`;
- `preview_screenshot` пикера — глазами: миниатюры не искажены, группы читаются.

- [ ] **Step 4.7: Commit**

```bash
git add webapp/static/editor.js webapp/static/editor.html webapp/static/styles.css
git commit -m "feat(picker): статичные PNG-плитки с iframe-фолбэком, группы каталога, чип «N типов» в имени"
```

---

### Task 5: Пустое состояние конструктора

**Files:**
- Modify: `webapp/static/editor.html:80-83` (`#builderEmpty`)
- Modify: `webapp/static/editor.js` (рядом с `addSlideViaPicker`, ~строка 2783)
- Modify: `webapp/static/styles.css` (стили `.builder-empty`)

- [ ] **Step 5.1: Разметка**

Заменить editor.html:80-83 на:

```html
    <div class="builder-empty" id="builderEmpty">
      <p class="builder-empty__hint">Слайдов нет. Начните со структуры из трёх
        слайдов — или выберите первый макет сами.</p>
      <div class="builder-empty__actions">
        <button class="btn" id="emptySkeleton" type="button">Начать со структуры</button>
        <button class="btn btn-ghost" id="builderAdd" type="button">Выбрать макет</button>
      </div>
    </div>
```

(`#builderAdd` сохраняет id — существующий обработчик editor.js:2783 продолжает открывать пикер.)

- [ ] **Step 5.2: Обработчик «Начать со структуры» в `editor.js`**

После `byId("builderAdd")?.addEventListener(...)` (:2783):

```js
// Пустой конструктор: «Начать со структуры» ставит три слайда разом (обложка →
// пустой → контакты) теми же POST, что одиночное добавление, — undo и
// перерисовка работают штатно. Тот же скелет сервер даёт при входе с главной
// (skeleton:true); эта кнопка — для «всё удалил, начну заново».
byId("emptySkeleton")?.addEventListener("click", async () => {
  const btn = byId("emptySkeleton");
  btn.disabled = true;
  try {
    for (const tid of ["cover", "blank", "contacts"]) {
      const r = await fetch(U(`/api/drafts/${sessionId}/slides`), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ template_id: tid }),
      }).catch(() => null);
      // Неуспех молча — «нажал и ничего»: показываем статус и не продолжаем.
      if (!r || !r.ok) { setSaveStatus("error"); return; }
    }
    pushUndo();
    await reloadDraft(0);
  } finally { btn.disabled = false; }
});
```

- [ ] **Step 5.3: CSS**

Найти в styles.css текущее правило `.builder-empty` (Grep) и дополнить рядом:

```css
/* Пустой конструктор: не голая строка с мелкой кнопкой, а подсказка + два
   крупных действия — лекарство от паралича чистого листа (спека, §2). */
.builder-empty__hint { margin: 0 0 12px; font-size: var(--fs-sm);
  color: var(--muted); line-height: 1.5; }
.builder-empty__actions { display: flex; flex-direction: column; gap: 8px; }
.builder-empty__actions .btn { width: 100%; }
```

Если существующее `.builder-empty` задаёт `display`/выравнивание, конфликтующее с колонкой, — поправить его на `display:block` с прежними отступами.

- [ ] **Step 5.4: Проверка в preview**

В manual-черновике удалить все слайды → панель показывает подсказку и обе кнопки. «Начать со структуры» → 3 слайда (обложка/пустой/контакты), фокус на первом. Ctrl+Z → слайды исчезают (undo жив). «Выбрать макет» → пикер. `preview_screenshot` панели.

- [ ] **Step 5.5: Commit**

```bash
git add webapp/static/editor.html webapp/static/editor.js webapp/static/styles.css
git commit -m "feat(editor): пустое состояние конструктора — «Начать со структуры» и «Выбрать макет»"
```

---

### Task 6: Точка входа на главной

**Files:**
- Modify: `webapp/static/index.html:118-128` (карточка manual)
- Modify: `webapp/static/app.js:851-869` (`startDraft`)

- [ ] **Step 6.1: Включить карточку в `index.html`**

Заменить :118-128 на:

```html
        <div class="entry-alt" data-mode="manual" tabindex="0" role="button">
          <div class="alt-main">
            <div class="alt-name">Заполнить шаблон</div>
            <p class="alt-desc">Соберите презентацию по слайду из готовых макетов, заполняя поля вручную.</p>
          </div>
          <div class="alt-aside">
            <div class="alt-when">когда нужен полный контроль</div>
            <span class="entry-open" id="openManual">Открыть конструктор →</span>
          </div>
        </div>
```

Снято: `is-disabled`, `aria-disabled`, `data-tip` (тултип «в разработке»), `.soon-badge`. Карточка «Собрать в чате» (:107-117) остаётся заглушенной как есть. Перед правкой Grep по styles.css/app.js на `data-tip`/`tabindex` у `.entry-alt`: если фокусируемость включённых карточек уже решена CSS/JS-ом иначе — не дублировать `tabindex`/`role`.

- [ ] **Step 6.2: Флаг скелета в `app.js`**

В `startDraft` (:856-859) заменить `body`:

```js
      body: JSON.stringify(mode === "manual"
        ? { mode, skeleton: true }   // старт со структурой (спека, §1) — флаг
        : { mode }),                 // шлёт ТОЛЬКО эта кнопка, glass не задет
```

- [ ] **Step 6.3: Проверка в preview**

Главная: карточка «Заполнить шаблон» активна (нет «в разработке»), «Собрать в чате» — заглушена. Клик по карточке → `preview_network`: `POST /api/drafts` с `{"mode":"manual","skeleton":true}` → редирект `/editor?session=…&mode=manual` → в редакторе 3 слайда-скелета, форма первого (обложка) открыта. Enter/Space на сфокусированной карточке — тоже работает. Back на главную → «Продолжить» показывает черновик, кнопка не залипла на «Создаю…».

- [ ] **Step 6.4: Commit**

```bash
git add webapp/static/index.html webapp/static/app.js
git commit -m "feat(web): точка входа «Заполнить шаблон» открыта — скелет из трёх слайдов на старте"
```

---

### Task 7: Полная верификация

- [ ] **Step 7.1: Юнит-сюиты**

Run: `python -m pytest tests/ -q` — expected: 727 старых + ~9 новых, 0 failed.
Run: `node --test tests/js/*.test.js` (перечислить файлы при необходимости) — expected: 159 pass.

- [ ] **Step 7.2: e2e в preview (полный путь спеки)**

Главная → «Заполнить шаблон» → редактор со скелетом → пикер с PNG-группами → заполнить пару слотов обложки → «Начать со структуры» после удаления всех слайдов → тема light (миниатюры пикера светлые) → реордер → скачать HTML, проверить правки в файле. Пруф: `preview_screenshot` редактора и пикера.

- [ ] **Step 7.3: Доложить Глебу**

Итог + скриншоты. Деплой — только по его команде; при деплое проверить thumb-рендер В КОНТЕКСТЕ sandbox юнита (ProtectHome; Chromium в `/opt/app2/.playwright`, писать кэш в `/opt/app2/data/tpl-thumbs` — внутри ReadWritePaths): `sudo systemd-run -p ProtectHome=true -p ProtectSystem=strict -p PrivateTmp=true -p ReadWritePaths=/opt/app2/data -E PLAYWRIGHT_BROWSERS_PATH=/opt/app2/.playwright -E SLIDESBOT_WORKDIR=/opt/app2/data/sessions ...` + `curl -H "X-User-Id: t" "http://127.0.0.1:8012/api/templates/cover/thumb"` → PNG.

---

## Риски и фолбэки

- **Дека не масштабирует слайд в 480×270** → ворота в Step 2.6: переключиться на viewport 1920×1080 (кэш тяжелее, но `.picker-thumb` c object-fit растянет корректно).
- **Chromium недоступен (локально/прод)** → 404 → фолбэк на live-iframe, поведение как до фичи. На проде проверять в sandbox-контексте, не в интерактивном SSH.
- **Скелет в glass-пути** → закрыт opt-in флагом + тест `test_create_draft_without_skeleton_stays_empty` + прогон tests/test_glass.py.
