# Quick Wins (Slice A) — план реализации

> **Для агента-исполнителя:** ОБЯЗАТЕЛЬНЫЙ СУБ-НАВЫК: используй
> superpowers:subagent-driven-development (рекомендуется) или
> superpowers:executing-plans, чтобы выполнять план задача-за-задачей. Шаги
> помечены чекбоксами (`- [ ]`).

**Goal:** сделать конструктор/чат-режим/первый экран понятнее четырьмя дешёвыми
фронтовыми правками, не трогая пайплайн генерации, слот-контракт и эндпоинты.

**Architecture:** только слой отображения. Данные (автосейв, коды ошибок валидации,
лимиты, поля типизированных слайдов) уже есть — их показываем словами. Новый крошечный
модуль `errtext.js` (чистая функция + строки статуса) юнит-тестируется через `node --test`.
Для подсветки (Task 4) добавляем атрибут-хук `data-slot` в Jinja-мастера и одно CSS-правило.

**Tech Stack:** FastAPI + ванильный JS (без сборки), Jinja2, pytest, встроенный
`node --test` (Node уже стоит, v26). Новых npm/pip-зависимостей нет.

**Единый визуальный стиль (сквозное требование).** Всё новое переиспользует токены и
компоненты `webapp/static/styles.css` / `htmlslides/engine/deck.css`: цвета `--accent`
(#26D07C), `--danger` (#ff6b6b), `--muted-2`; **скругления = 0** (в теме `--r*` = 0px);
шрифты `--font-display`/`--font-text`; классы `.field-hint`, `.field-error`,
`.field-item`, `.field-list`, кнопки `.btn-ghost.btn-sm`, `.item-del`. Никаких новых
цветов/радиусов.

---

## File Structure

| Файл | Действие | Ответственность |
|------|----------|-----------------|
| `webapp/static/errtext.js` | Создать | Чистые `errText(code, detail)` + `SAVE_STATUS`; двойной экспорт (browser global + CommonJS) |
| `tests/js/errtext.test.js` | Создать | Юнит-тест `errText` через `node --test` |
| `tests/test_slot_markers.py` | Создать | Проверка, что каждый мастер помечает верхнеуровневые слоты `data-slot` и рендерится без ошибок |
| `webapp/static/editor.html` | Изменить | Подключить `errtext.js`; добавить `#saveStatus` в шапку формы |
| `webapp/static/editor.js` | Изменить | Статус автосейва; текст ошибки под полем; счётчик символов; построчный редактор чат-полей; подсветка слота |
| `webapp/static/index.html` | Изменить | Сжать интро в строку + `<details>`; класс `entry-card--primary` на карточке 01 |
| `webapp/static/styles.css` | Изменить | Стили `.save-status`, `.field-hint--error/--over`, `.entry-card--primary`, `.intro-more`, `.field-lines` |
| `htmlslides/templates/slides/*.html` | Изменить (23) | Добавить `data-slot` на элементы верхнеуровневых слотов |
| `htmlslides/engine/deck.css` | Изменить | Правило `.slot-highlight` |

Порядок задач (от дешёвой к тяжёлой): **1 → 2 → 3 → 4**. Задачи слабо связаны; Task 1
даёт `errText`, остальные независимы.

**Запуск приложения для ручной проверки:** `uvicorn webapp.app:app --reload`
(объект `app` в `webapp/app.py:65`).

---

## Task 1: Статус «Сохранено ✓» + текст ошибки под полем + счётчик символов

**Files:**
- Create: `webapp/static/errtext.js`
- Create: `tests/js/errtext.test.js`
- Modify: `webapp/static/editor.html:45-52` (шапка формы), `:97` (подключение скрипта)
- Modify: `webapp/static/editor.js:524-530` (счётчик), `:631-660` (статус), `:662-668` (текст ошибки)
- Modify: `webapp/static/styles.css` (новые классы)

### 1a. Чистая логика `errText` (TDD)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/js/errtext.test.js`:

```js
const test = require("node:test");
const assert = require("node:assert");
const { errText, SAVE_STATUS } = require("../../webapp/static/errtext.js");

test("missing_required → просьба заполнить", () => {
  assert.strictEqual(errText("missing_required", ""), "Заполните обязательное поле");
});

test("too_long парсит 'N > max'", () => {
  assert.strictEqual(errText("too_long", "45 > 40"), "Слишком длинно: 45 из 40 символов");
});

test("too_many_items парсит 'N > max'", () => {
  assert.strictEqual(errText("too_many_items", "7 > 6"), "Слишком много пунктов: 7 из 6");
});

test("unknown_slot → пусто (пользователю не показываем)", () => {
  assert.strictEqual(errText("unknown_slot", "нечто"), "");
});

test("too_long без разбираемого detail → общий текст", () => {
  assert.strictEqual(errText("too_long", "—"), "Слишком длинно");
});

test("SAVE_STATUS содержит три состояния", () => {
  assert.deepStrictEqual(Object.keys(SAVE_STATUS).sort(), ["error", "saved", "saving"]);
});
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && node --test tests/js/errtext.test.js`
Expected: FAIL — `Cannot find module '../../webapp/static/errtext.js'`.

- [ ] **Step 3: Реализовать `errtext.js`**

Создать `webapp/static/errtext.js`:

```js
// Русские строки автосейва и текстов ошибок валидации для конструктора.
// Отдельный модуль, чтобы чистую логику errText() юнит-тестировать через
// `node --test`. В браузере подключается ПЕРЕД editor.js и кладёт функции в
// window; в Node — экспортируется через module.exports. Меняется только текст,
// никакой DOM-логики здесь нет.
(function (root) {
  // Три состояния индикатора автосейва (показываем в шапке формы).
  var SAVE_STATUS = {
    saving: "Сохранение…",
    saved: "Сохранено ✓",
    error: "Не сохранено",
  };

  // Разбирает detail вида "N > max" в [N, max]; иначе [null, null].
  function parseCounts(detail) {
    var m = /(-?\d+)\s*>\s*(-?\d+)/.exec(detail == null ? "" : String(detail));
    return m ? [Number(m[1]), Number(m[2])] : [null, null];
  }

  // Русский текст ошибки по коду слот-контракта. Пустая строка — «не показывать»
  // (unknown_slot и прочее внутреннее), чтобы вызывающий ничего не рисовал.
  function errText(code, detail) {
    var c = parseCounts(detail), n = c[0], max = c[1];
    if (code === "missing_required") return "Заполните обязательное поле";
    if (code === "too_long") {
      return n != null ? "Слишком длинно: " + n + " из " + max + " символов"
                       : "Слишком длинно";
    }
    if (code === "too_many_items") {
      return n != null ? "Слишком много пунктов: " + n + " из " + max
                       : "Слишком много пунктов";
    }
    return "";
  }

  root.SAVE_STATUS = SAVE_STATUS;
  root.errText = errText;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { SAVE_STATUS: SAVE_STATUS, errText: errText };
  }
})(typeof window !== "undefined" ? window : globalThis);
```

- [ ] **Step 4: Запустить тест — убедиться, что проходит**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && node --test tests/js/errtext.test.js`
Expected: PASS — 6 tests.

- [ ] **Step 5: Коммит**

```bash
git add webapp/static/errtext.js tests/js/errtext.test.js
git commit -m "$(cat <<'EOF'
feat: add errtext helper for save-status and validation messages

Pure errText(code, detail) + SAVE_STATUS strings, unit-tested via node --test.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### 1b. Подключить статус, тексты ошибок и счётчик в редактор

- [ ] **Step 6: Подключить `errtext.js` перед `editor.js`**

В `webapp/static/editor.html` строку 97 заменить:

```html
<script src="/static/editor.js"></script>
```

на:

```html
<script src="/static/errtext.js"></script>
<script src="/static/editor.js"></script>
```

- [ ] **Step 7: Добавить место под индикатор автосейва в шапке формы**

В `webapp/static/editor.html` блок (строки 45-52):

```html
    <div class="builder-head">
      <h3>Заполнение слайда</h3>
      <div class="builder-actions">
```

заменить на:

```html
    <div class="builder-head">
      <h3>Заполнение слайда</h3>
      <span class="save-status" id="saveStatus" aria-live="polite"></span>
      <div class="builder-actions">
```

- [ ] **Step 8: Живой счётчик символов в `renderSlot`**

В `webapp/static/editor.js` блок (строки 524-530):

```js
    if (spec.max_chars) el.maxLength = spec.max_chars;
    el.value = value == null ? "" : String(value);
    el.dataset.slot = name;
    el.dataset.kind = "text";
    el.oninput = scheduleSave;
    wrap.appendChild(el);
    if (spec.max_chars) wrap.appendChild(hint(`до ${spec.max_chars} символов`));
```

заменить на:

```js
    if (spec.max_chars) el.maxLength = spec.max_chars;
    el.value = value == null ? "" : String(value);
    el.dataset.slot = name;
    el.dataset.kind = "text";
    el.addEventListener("input", scheduleSave);
    wrap.appendChild(el);
    if (spec.max_chars) wrap.appendChild(charCounter(el, spec.max_chars));
```

- [ ] **Step 9: Добавить хелпер `charCounter` рядом с `hint`**

В `webapp/static/editor.js` сразу после функции `hint` (после строки 585) вставить:

```js
// Живой счётчик «M/N» для текстового поля: тот же узел, что hint(), но обновляется
// на ввод и краснеет (.field-hint--over) при переполнении M > max. maxLength обычно
// не даёт превысить руками — красный нужен для значений, пришедших из плана.
function charCounter(el, max) {
  const h = hint("");
  const upd = () => {
    const n = (el.value || "").length;
    h.textContent = `${n}/${max}`;
    h.classList.toggle("field-hint--over", n > max);
  };
  el.addEventListener("input", upd);
  upd();
  return h;
}
```

- [ ] **Step 10: Индикатор автосейва в `saveCurrentSlide`**

В `webapp/static/editor.js` функцию `saveCurrentSlide` (строки 631-660) заменить целиком на:

```js
async function saveCurrentSlide() {
  // Capture the slide index up front: `current` can change (navigation) during
  // the awaited PUT, and the URL/marking must stay bound to the edited slide.
  const idx = current;
  putTimer = null;
  const slide = draftPlan.slides[idx];
  if (!slide || slide.freeform) return;
  const content = collectContent();
  slide.content = content; // optimistic local update
  setSaveStatus("saving");
  let r;
  try {
    r = await fetch(U(`/api/drafts/${sessionId}/slides/${idx + 1}`), {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
  } catch (e) {
    r = null;
  }
  if (r && r.ok) {
    const { errors } = await r.json();
    if (current === idx) markFieldErrors(errors || []); // only if still shown
    setSaveStatus("saved");
    loadDeck(); // refresh preview
  } else {
    // Save failed (network/server): resync from the server so we never write stale
    // state back on the next edit, and say so in words (persistent red status)
    // instead of an alert() on every failure.
    await reloadDraft(idx);
    setSaveStatus("error");
  }
}

// Индикатор автосейва в шапке формы: «Сохранение…» → «Сохранено ✓» → «Не сохранено».
// Успех мягко гаснет через 1.6с; процесс/ошибка висят до следующего сейва. Узел
// #saveStatus живёт в .builder-head, который не перестраивается renderBuilderForm,
// поэтому статус переживает пересборку формы.
let saveStatusTimer = null;
function setSaveStatus(state) {
  const el = byId("saveStatus");
  if (!el) return;
  clearTimeout(saveStatusTimer);
  el.textContent = SAVE_STATUS[state] || "";
  el.className = "save-status save-status--" + state;
  if (state === "saved") {
    saveStatusTimer = setTimeout(() => {
      el.textContent = "";
      el.className = "save-status";
    }, 1600);
  }
}
```

- [ ] **Step 11: Текст ошибки под полем в `markFieldErrors`**

В `webapp/static/editor.js` функцию `markFieldErrors` (строки 662-668) заменить целиком на:

```js
function markFieldErrors(errors) {
  // Первую ошибку на каждый слот верхнего уровня печатаем под соответствующим
  // полем (узел .field-hint--error). Рамку красит существующий .field-error.
  const bySlot = new Map();
  errors.forEach((e) => {
    const top = e.slot.split(/[.[]/)[0];
    if (!bySlot.has(top)) bySlot.set(top, e);
  });
  byId("builderForm").querySelectorAll(".field").forEach((f) => {
    const slot = f.querySelector("[data-slot]")?.dataset.slot;
    const err = slot ? bySlot.get(slot) : null;
    f.classList.toggle("field-error", !!err);
    const text = err ? errText(err.code, err.detail) : "";
    let msg = f.querySelector(".field-hint--error");
    if (text) {
      if (!msg) {
        msg = document.createElement("div");
        msg.className = "field-hint field-hint--error";
        f.appendChild(msg);
      }
      msg.textContent = text;
    } else if (msg) {
      msg.remove();
    }
  });
}
```

- [ ] **Step 12: Стили статуса, ошибки и переполнения счётчика**

В `webapp/static/styles.css` после строки 302 (`.field-error input, .field-error textarea { border-color: var(--danger); }`) добавить:

```css
/* Индикатор автосейва в шапке формы (слева от кнопок; острые углы, токены) */
.save-status { margin-left: 10px; margin-right: auto; font-size: 11px;
  color: var(--muted-2); transition: opacity .3s; }
.save-status--saving { color: var(--muted-2); }
.save-status--saved { color: var(--accent); }
.save-status--error { color: var(--danger); }
/* Текст ошибки под полем и красный счётчик при переполнении */
.field-hint--error { color: var(--danger); }
.field-hint--over { color: var(--danger); }
```

- [ ] **Step 13: Ручная проверка**

Запустить `uvicorn webapp.app:app --reload`, открыть конструктор (точка входа
«Заполнить шаблон»), добавить слайд. Проверить критерии:
- при вводе появляется «Сохранение…», затем «Сохранено ✓» и через ~1.6с гаснет;
- под полем `title` заголовка-обложки (cover, лимит 20) счётчик «M/20» краснеет,
  если вставить длинный текст (значение из плана > лимита);
- очистка обязательного поля → под полем «Заполните обязательное поле» + красная рамка;
- визуально тексты — как обычные подсказки (`.field-hint`), не «выпадают».

- [ ] **Step 14: Коммит**

```bash
git add webapp/static/editor.html webapp/static/editor.js webapp/static/styles.css
git commit -m "$(cat <<'EOF'
feat: show save status, per-field error text and live char counter

Autosave now reports Сохранение…/Сохранено ✓/Не сохранено in the form head,
validation errors print readable RU text under the field, and text fields show a
live M/N counter that reddens on overflow. Replaces the per-save alert().

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Главная кнопка «Загрузить документ» + сжатие интро

**Files:**
- Modify: `webapp/static/index.html:27-30` (интро), `:40` (класс primary)
- Modify: `webapp/static/styles.css` (`.entry-card--primary`, `.intro-more`)

**Не трогаем `app.js`.** Карточки сохраняют структуру (`entry-num/title/desc/cta`,
`data-entry`, id) — меняем только класс и стили. Внутри `.workspace__controls`
карточки уже стоят в один столбец (`styles.css:407`), поэтому грид не меняем — только
акцент на 01 и сжатие интро.

- [ ] **Step 1: Сжать интро в строку + `<details>`**

В `webapp/static/index.html` блок (строки 27-30):

```html
    <h1 class="intro-title">Презентации в бренде Cloud.ru</h1>
    <p class="intro-lead">Здесь можно собрать HTML-презентацию в фирменном стиле Cloud.ru тремя способами: <b>загрузить готовый документ</b> (модель сама разобьёт его на слайды, подберёт вёрстку и построит графики), <b>заполнить шаблон вручную</b> из готовых макетов или <b>собрать деку в чате</b> &mdash; ассистент спланирует структуру и создаст слайды по одному. Любую готовую деку можно править прямо в браузере, дорабатывать через чат и скачивать в HTML или PNG.</p>
    <p class="intro-lead">Для загрузки подойдёт <b>.md / .txt / .docx / .pptx</b>. Проще всего подготовить исходник через нейросеть (ChatGPT, Claude, GigaChat): попросите собрать markdown с заголовками <code>##</code> для каждого раздела и короткими тезисами с цифрами внутри &mdash; чем чётче разделы и факты, тем точнее структура и тем больше наглядных графиков вместо сплошного текста.</p>
```

заменить на:

```html
    <h1 class="intro-title">Презентации в бренде Cloud.ru</h1>
    <p class="intro-lead">Соберите HTML-презентацию в фирменном стиле Cloud.ru: загрузите документ, заполните шаблон вручную или соберите деку в чате.</p>
    <details class="intro-more">
      <summary>Подробнее</summary>
      <p class="intro-lead">Здесь можно собрать HTML-презентацию в фирменном стиле Cloud.ru тремя способами: <b>загрузить готовый документ</b> (модель сама разобьёт его на слайды, подберёт вёрстку и построит графики), <b>заполнить шаблон вручную</b> из готовых макетов или <b>собрать деку в чате</b> &mdash; ассистент спланирует структуру и создаст слайды по одному. Любую готовую деку можно править прямо в браузере, дорабатывать через чат и скачивать в HTML или PNG.</p>
      <p class="intro-lead">Для загрузки подойдёт <b>.md / .txt / .docx / .pptx</b>. Проще всего подготовить исходник через нейросеть (ChatGPT, Claude, GigaChat): попросите собрать markdown с заголовками <code>##</code> для каждого раздела и короткими тезисами с цифрами внутри &mdash; чем чётче разделы и факты, тем точнее структура и тем больше наглядных графиков вместо сплошного текста.</p>
    </details>
```

- [ ] **Step 2: Пометить карточку 01 как главную**

В `webapp/static/index.html` строку 40:

```html
      <button type="button" class="entry-card is-active" id="entryUpload" data-entry="upload">
```

заменить на:

```html
      <button type="button" class="entry-card entry-card--primary is-active" id="entryUpload" data-entry="upload">
```

- [ ] **Step 3: Стили главной карточки и раскрывашки интро**

В `webapp/static/styles.css` после строки 130 (`@media (max-width: 720px) { .entry-cards { grid-template-columns: 1fr; } }`) добавить:

```css
/* Главная точка входа: акцентный фон/рамка (приём как у .is-active) + крупнее
   заголовок. Острые углы, цвета из токенов — в общем стиле карточек. */
.entry-card--primary { padding: 22px 18px 18px; border-color: var(--accent);
  box-shadow: inset 0 0 0 1px var(--accent); background: var(--accent-dim); }
.entry-card--primary:hover { background: var(--accent-dim); }
.entry-card--primary .entry-title { font-size: 20px; }
/* Раскрывашка «Подробнее» под интро */
.intro-more { margin-top: 6px; }
.intro-more > summary { cursor: pointer; color: var(--accent); font-size: 13px;
  list-style: none; display: inline-block; }
.intro-more > summary::-webkit-details-marker { display: none; }
.intro-more > summary::after { content: " ↓"; }
.intro-more[open] > summary::after { content: " ↑"; }
.intro-more .intro-lead { margin-top: 12px; }
```

- [ ] **Step 4: Ручная проверка**

Запустить `uvicorn webapp.app:app --reload`, открыть главную. Проверить критерии:
- карточка «01 Загрузить документ» визуально доминирует; 02/03 явно тише;
- интро — одна строка + «Подробнее» (по клику раскрывает прежний текст, стрелка ↓/↑);
- клики по всем трём карточкам работают как раньше (upload раскрывает `#uploadFlow`,
  manual/chat создают черновик), галочка «Точный перенос» на месте;
- Back с экрана редактора возвращает карточки без «залипшего Создаю…» (pageshow);
- проверить обе темы (dark/light) и узкую ширину (< 720px).

- [ ] **Step 5: Коммит**

```bash
git add webapp/static/index.html webapp/static/styles.css
git commit -m "$(cat <<'EOF'
feat: emphasize upload entry and collapse intro into a disclosure

Card 01 (Загрузить документ) now reads as the primary action; the two-paragraph
intro collapses to one line plus a Подробнее <details>. Entry JS untouched.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Построчные инпуты вместо «|»-синтаксиса в чат-режиме

**Files:**
- Modify: `webapp/static/editor.js:861-937` (хелпер `lineListEditor` + ветки `renderFieldCard`)
- Modify: `webapp/static/styles.css` (`.field-lines`)

Модель данных типизированных полей проще, чем у конструктора (`bullets`/`left`/`right`
— массивы строк, `stats` — массив `{value,label}`), поэтому делаем компактный репитер,
визуально повторяющий список конструктора (`.field-list`/`.field-item`/`+ пункт`/`✕`),
а не буквальный reuse `renderItem`. Payload на `/fields` не меняется.

- [ ] **Step 1: Добавить хелпер `lineListEditor`**

В `webapp/static/editor.js` сразу после функции `fieldInput` (после строки 867) вставить:

```js
// Построчный редактор списка для типизированных полей чата. `cols` описывает
// ячейки одной строки: одна ячейка без key → значение-строка (bullets/колонки),
// две ячейки с key value/label → объект (stats). Визуально повторяет список
// конструктора (.field-list/.field-item/+ пункт/✕). onChange(values) зовём после
// любой правки/добавления/удаления — там вызывается saveFields.
function lineListEditor(items, cols, onChange) {
  const list = document.createElement("div");
  list.className = "field-list";
  const single = cols.length === 1;

  const collect = () => {
    const out = [];
    list.querySelectorAll(".field-item").forEach((row) => {
      const inputs = row.querySelectorAll("input");
      if (single) {
        const v = inputs[0].value.trim();
        if (v) out.push(v);
      } else {
        const obj = {};
        cols.forEach((c, i) => { obj[c.key] = inputs[i].value.trim(); });
        if (Object.values(obj).some(Boolean)) out.push(obj);
      }
    });
    onChange(out);
  };

  const makeRow = (item) => {
    const row = document.createElement("div");
    row.className = "field-item";
    cols.forEach((c, i) => {
      const inp = document.createElement("input");
      inp.placeholder = c.placeholder;
      inp.value = single ? (item || "") : ((item && item[c.key]) || "");
      inp.addEventListener("input", collect);
      row.appendChild(inp);
    });
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn btn-ghost btn-sm item-del";
    del.textContent = "✕";
    del.onclick = () => { row.remove(); collect(); };
    row.appendChild(del);
    list.appendChild(row);
    return row;
  };

  (items.length ? items : [single ? "" : {}]).forEach(makeRow);

  const wrap = document.createElement("div");
  wrap.className = "field-lines";
  wrap.appendChild(list);
  const add = document.createElement("button");
  add.type = "button";
  add.className = "btn btn-ghost btn-sm";
  add.textContent = "+ пункт";
  add.onclick = () => { makeRow(single ? "" : {}).querySelector("input")?.focus(); };
  wrap.appendChild(add);
  return wrap;
}
```

- [ ] **Step 2: Переключить `bullets` на построчный ввод**

В `webapp/static/editor.js` блок (строки 906-912):

```js
  } else if (s.slide_type === "bullets") {
    f.bullets = f.bullets || [];
    addRow("Тезисы", fieldInput(f.bullets.join(" | "), (e) => {
      f.bullets = e.target.value.split("|").map((x) => x.trim()).filter(Boolean);
      commit();
    }));
    appendHint(card, "Пункты через | (вертикальная черта)");
```

заменить на:

```js
  } else if (s.slide_type === "bullets") {
    f.bullets = f.bullets || [];
    addRow("Тезисы", lineListEditor(f.bullets, [{ placeholder: "пункт" }],
      (vals) => { f.bullets = vals; commit(); }));
```

- [ ] **Step 3: Переключить `stats` на построчный ввод**

В `webapp/static/editor.js` блок (строки 913-923):

```js
  } else if (s.slide_type === "stats") {
    f.stats = f.stats || [];
    addRow("Цифры", fieldInput(
      f.stats.map((x) => `${x.value}=${x.label}`).join(" | "), (e) => {
        f.stats = e.target.value.split("|").map((p) => {
          const [value, label] = p.split("=");
          return { value: (value || "").trim(), label: (label || "").trim() };
        }).filter((x) => x.value || x.label);
        commit();
      }));
    appendHint(card, "value=label, пары через |  (напр. 99%=аптайм | 3=региона)");
```

заменить на:

```js
  } else if (s.slide_type === "stats") {
    f.stats = f.stats || [];
    addRow("Цифры", lineListEditor(f.stats,
      [{ key: "value", placeholder: "значение" }, { key: "label", placeholder: "подпись" }],
      (vals) => { f.stats = vals; commit(); }));
```

- [ ] **Step 4: Переключить `two_col` на построчный ввод**

В `webapp/static/editor.js` блок (строки 924-935):

```js
  } else if (s.slide_type === "two_col") {
    f.left = f.left || []; f.right = f.right || [];
    addRow("Левая колонка", fieldInput(f.left.join(" | "), (e) => {
      f.left = e.target.value.split("|").map((x) => x.trim()).filter(Boolean);
      commit();
    }));
    addRow("Правая колонка", fieldInput(f.right.join(" | "), (e) => {
      f.right = e.target.value.split("|").map((x) => x.trim()).filter(Boolean);
      commit();
    }));
    appendHint(card, "Пункты через | в каждой колонке");
```

заменить на:

```js
  } else if (s.slide_type === "two_col") {
    f.left = f.left || []; f.right = f.right || [];
    addRow("Левая колонка", lineListEditor(f.left, [{ placeholder: "пункт" }],
      (vals) => { f.left = vals; commit(); }));
    addRow("Правая колонка", lineListEditor(f.right, [{ placeholder: "пункт" }],
      (vals) => { f.right = vals; commit(); }));
```

- [ ] **Step 5: Стиль обёртки построчного редактора**

В `webapp/static/styles.css` после строки 306 (`.item-del { flex: 0 0 auto; padding: 6px 8px; }`) добавить:

```css
.field-lines { display: flex; flex-direction: column; gap: 8px; }
```

- [ ] **Step 6: Проверить, что `appendHint` больше не используется**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && grep -n "appendHint" webapp/static/editor.js`
Expected: только определение функции `appendHint` (строка ~872), без вызовов. Если
определение осталось без вызовов — удалить саму функцию `appendHint` (строки ~869-877),
чтобы не копить мёртвый код.

- [ ] **Step 7: Ручная проверка**

Запустить `uvicorn webapp.app:app --reload`, войти через «Собрать в чате», получить
план и типизированные слайды. Проверить критерии:
- у `bullets`/`stats`/`two_col` строки редактируются по одной, без « | »;
- «+ пункт»/«✕» работают, добавление/удаление сразу видно в превью;
- у `stats` две ячейки «значение»/«подпись» дают тот же результат, что раньше;
- вид строк/кнопок совпадает с конструктором (`.field-item`, `.btn-ghost.btn-sm`).

- [ ] **Step 8: Коммит**

```bash
git add webapp/static/editor.js webapp/static/styles.css
git commit -m "$(cat <<'EOF'
feat: per-row inputs for chat-mode list fields instead of | syntax

bullets/stats/two_col edit line by line (+ пункт / ✕), mirroring the constructor's
list editor. Same /fields payload shape; drops the fragile | separator hints.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Подсветка редактируемого элемента на слайде

**Files:**
- Create: `tests/test_slot_markers.py`
- Modify: `htmlslides/templates/slides/*.html` (23 мастера — добавить `data-slot`)
- Modify: `htmlslides/engine/deck.css` (правило `.slot-highlight`)
- Modify: `webapp/static/editor.js` (обработчики `focusin`/`focusout`)

**Правило разметки `data-slot`:**
- Текстовый слот `content.X` → `data-slot="X"` на элементе, который непосредственно
  оборачивает `{{ content.X }}`.
- Список `content.X` (перебирается `{% for ... in content.X %}`) → `data-slot="X"` на
  элементе-контейнере цикла.
- Общая шапка: `content-head-title` → `data-slot="title"`, `content-head-sub` →
  `data-slot="subtitle"`.
- Подсветка — только для верхнеуровневых слотов; на отдельные пункты/бары маркеры не
  вешаем.

### 4a. Тест-проверка разметки всех мастеров (TDD)

- [ ] **Step 1: Написать падающий тест**

Создать `tests/test_slot_markers.py`:

```python
"""Каждый мастер должен помечать верхнеуровневые слоты атрибутом data-slot
(хук подсветки редактируемого поля) и рендериться без ошибок слот-контракта."""
import json
from importlib import resources

from htmlslides.assembler import assemble
from htmlslides.models import DeckPlan, SlidePlan


def _library():
    node = resources.files("htmlslides") / "templates" / "library.json"
    return json.loads(node.read_text("utf-8"))


def _sample_content(slots):
    """Минимальный валидный контент: каждый текст — 'x', каждый список — один пункт
    со всеми под-слотами '1'. Хватает, чтобы отрисовались все опциональные слоты."""
    content = {}
    for name, spec in slots.items():
        if spec["kind"] == "text":
            content[name] = "x"
        elif spec["kind"] == "list":
            content[name] = [{sub: "1" for sub in spec.get("item_slots", {})}]
    return content


def test_every_master_marks_top_level_slots():
    for tpl in _library()["templates"]:
        slots = tpl["slots"]
        content = _sample_content(slots)
        plan = DeckPlan(title="t", slides=[SlidePlan(
            index=1, type=tpl["type"], template_id=tpl["id"], content=content)])
        html = assemble(plan, theme="dark")  # также проверяет, что рендер не падает
        for slot in slots:
            assert f'data-slot="{slot}"' in html, (
                f'{tpl["id"]}: нет data-slot="{slot}"')
```

- [ ] **Step 2: Запустить тест — убедиться, что падает**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_slot_markers.py -v`
Expected: FAIL — первый же шаблон со слотами (`cover`) не содержит `data-slot="title"`.

- [ ] **Step 3: Разметить шапку и текстовые слоты (мастера с `.content-head`)**

Для каждого файла ниже добавить `data-slot` по правилу. Заголовок/подзаголовок общей
шапки одинаковы во всех контентных мастерах:

```html
<h3 class="content-head-title t-head-42" data-slot="title">{{ content.title }}</h3>
```
```html
<p class="content-head-sub t-sub-36" data-slot="subtitle">{{ content.subtitle }}</p>
```

Мастера с шапкой `.content-head` (title + subtitle): `stats-row.html`, `bar-chart.html`,
`donut-chart.html`, `line-chart.html`, `stacked-bar.html`, `kpi-rings.html`,
`kpi.html`, `before-after.html`, `service-table.html`, `timeline.html`,
`two-col-cards.html`, `three-col.html`, `grid-2x2.html`, `cards-6.html`, `blank.html`.

- [ ] **Step 4: Разметить мастера без общей шапки (собственная вёрстка заголовка)**

`cover.html` (строки 5-6):
```html
    <h1 class="cover-title t-hero-156 m-enter m-enter--hero" data-slot="title">{{ content.title }}</h1>
    {% if content.subtitle %}<p class="cover-subtitle t-sub-36 m-enter" data-slot="subtitle">{{ content.subtitle }}</p>{% endif %}
```

`contacts.html` (строки 15, 19):
```html
    <h1 class="contacts-title t-hero-156" data-slot="title">{{ content.title }}</h1>
```
```html
    <p class="contacts-subtitle t-sub-36" data-slot="subtitle">{{ content.subtitle }}</p>
```

`cover-image.html` — `image` вешаем на SVG-фигуру (строка 5), а не на `<image>`
внутри (у SVG-`<image>` outline не рисуется); `title` (строка 11) и `lead` (строка 14):
```html
  <svg class="cover-image-art" viewBox="0 0 1020 1080" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" data-slot="image">
```
```html
  <h1 class="cover-image-title m-enter m-enter--hero" data-slot="title">{{ content.title }}</h1>
```
```html
  <p class="cover-image-lead t-sub-36 m-enter" data-slot="lead">{{ content.lead or 'Масштабируйте возможности бизнеса в облаке' }}</p>
```

`statement.html` (строки 10, 15) — этот файл рендерит ОБА мастера (`statement` и
`statement-green`), поэтому правим только его:
```html
    <h1 class="statement-title t-hero-156 m-enter m-enter--hero" data-slot="title">{{ content.title }}</h1>
```
```html
    <p class="statement-plate-text t-sub-36" data-slot="highlight">{{ content.highlight or 'Главный вывод секции' }}</p>
```

`statement-green.html`: **не трогаем** — это тонкий `{% include "statement.html" %}`
без своей разметки; маркеры из `statement.html` покрывают и его.

`section-dots.html` (строки 7, 9):
```html
  <h1 class="sd-label t-hero-156 m-enter m-enter--hero" data-slot="label">{{ content.label }}</h1>
```
```html
  {% if content.number %}{% set num = content.number | string | trim %}<div class="sd-number t-hero-156 m-enter" data-slot="number">{% if num.isdigit() %}{{ "%02d" | format(num | int) }}{% else %}{{ num }}{% endif %}</div>{% endif %}
```

`section-frame.html` (строки 12, 14):
```html
  <h1 class="sf-title m-enter m-enter--hero" data-slot="label">{{ content.label }}</h1>
```
```html
  {% if content.number %}{% set num = content.number | string | trim %}<p class="sf-number t-sub-36 m-enter" data-slot="number">{% if num.isdigit() %}{{ "%02d" | format(num | int) }}{% else %}{{ num }}{% endif %}</p>{% endif %}
```

`back-cover.html`: слотов нет — не трогаем.

`before-after.html` (кроме шапки) — на каждый текстовый слот (строки 25, 26, 34, 37, 38):
```html
    <p class="ba-label ba-label--before t-head-36" data-slot="before_label">{{ content.before_label }}</p>
    <p class="ba-text t-body-30" data-slot="before_text">{{ content.before_text }}</p>
```
```html
      {% if content.delta %}<p class="ba-delta" data-slot="delta">{{ content.delta }}</p>{% endif %}
```
```html
    <p class="ba-label ba-label--after t-head-36" data-slot="after_label">{{ content.after_label }}</p>
    <p class="ba-text t-body-30" data-slot="after_text">{{ content.after_text }}</p>
```

`service-table.html` (кроме шапки — строки 12, 17, 18; `image` вешаем на обёртку
`.svc-image`, а не на `<img>`):
```html
  <div class="svc-image" aria-hidden="true" data-slot="image">
```
```html
    <h2 class="svc-name t-head-54" data-slot="service_name">{{ content.service_name }}</h2>
```
```html
    {% if content.service_desc %}<p class="svc-desc t-body-30" data-slot="service_desc">{{ content.service_desc }}</p>{% endif %}
```

`timeline.html` — у него общая шапка (title/subtitle покрыты Step 3, `items` — Step 5),
но есть отдельный текстовый слот `image`; вешаем `data-slot="image"` на контейнер-арт
(строка 17), который рендерится всегда:
```html
  <div class="tl-art m-enter-right" aria-hidden="true" data-slot="image">
```

- [ ] **Step 5: Разметить контейнеры списков**

Правило: `data-slot="X"` вешаем на элемент-контейнер, внутри которого идёт
`{% for ... in content.X %}`.

**(A) HTML-контейнеры `<div class="… m-stagger">`** — добавить `data-slot` в открывающий
тег (у всех — та же строка, что показана):
```html
  <div class="kpi-grid m-stagger" data-slot="kpis">            <!-- kpi.html:10 -->
  <div class="sr-row m-stagger" data-slot="stats">            <!-- stats-row.html:16 -->
  <div class="svc-rows m-stagger" data-slot="rows">           <!-- service-table.html:19 -->
  <div class="tl-list m-stagger" data-slot="items">           <!-- timeline.html:10 -->
  <div class="tcc-grid m-stagger" data-slot="cards">          <!-- two-col-cards.html:10 -->
  <div class="tcl-grid m-stagger" data-slot="columns">        <!-- three-col.html:10 -->
  <div class="g22-grid m-stagger" data-slot="cells">          <!-- grid-2x2.html:10 -->
  <div class="c6-grid m-stagger" data-n="{{ content.cards|length }}" data-slot="cards"> <!-- cards-6.html:10 -->
```

**(B) SVG-графики** — бары/сегменты рисуются как SVG-элементы внутри внешнего
`<svg class="…-svg">`; `data-slot` вешаем на этот **внешний `<svg>`** (CSS `outline`
рисуется на корневом `<svg>` как на replaced-элементе; на внутренних `<g>`/`<circle>`
— нет). Добавить `data-slot` в открывающий тег `<svg …>`:
```html
  <svg class="bar-svg m-enter" ... data-slot="bars">          <!-- bar-chart.html:32 -->
  <svg class="donut-svg m-enter" ... data-slot="segments">    <!-- donut-chart.html:47 (покрывает и дуги, и легенду — обе по content.segments) -->
  <svg class="line-svg m-enter" ... data-slot="points">       <!-- line-chart.html:56 -->
  <svg class="kr-svg m-enter" ... data-slot="rings">          <!-- kpi-rings.html:40 -->
```

**Особый случай — `stacked-bar.html`:** в одном `<svg class="sb-svg">` идут ДВА
списковых слота (`legend` — строка 51, `bars` — строка 60). На `<svg>` вешаем главный
слот `bars`; легенду оборачиваем в невидимую SVG-группу `<g data-slot="legend">` (группа
не меняет геометрию). Строка 47:
```html
  <svg class="sb-svg m-enter" ... data-slot="bars">
```
Обернуть цикл легенды (строки 51-58) — было:
```html
    {# легенда сверху: свотч opacity-ladder → label сегмента #}
    {% for lg in legend %}
```
стало (добавить строку `<g data-slot="legend">` перед циклом и `</g>` после `{% endfor %}`):
```html
    {# легенда сверху: свотч opacity-ladder → label сегмента #}
    <g data-slot="legend">
    {% for lg in legend %}
```
и закрыть группу сразу после соответствующего `{% endfor %}` (строка 58):
```html
    {% endfor %}
    </g>
```

- [ ] **Step 6: Запустить тест — убедиться, что проходит**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest tests/test_slot_markers.py -v`
Expected: PASS. Если падает — сообщение укажет `<template>: нет data-slot="<slot>"`;
доразметить указанный слот.

- [ ] **Step 7: Прогнать весь набор тестов — регрессий нет**

Run: `cd /Users/mdmolotkova/Desktop/SLIDES_APK && python -m pytest -q`
Expected: всё зелёное (существующие рендер-тесты не сломаны добавлением атрибутов).

- [ ] **Step 8: Коммит**

```bash
git add tests/test_slot_markers.py htmlslides/templates/slides/*.html
git commit -m "$(cat <<'EOF'
feat: mark top-level slots with data-slot in all masters

Adds a data-slot hook to every top-level slot element across the 23 masters,
covered by a new test that also verifies rendering doesn't break.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

### 4b. Правило подсветки и обработчики фокуса

- [ ] **Step 9: Добавить CSS-правило подсветки**

В конец `htmlslides/engine/deck.css` добавить:

```css
/* Хук панельного редактора: обводит редактируемый блок при фокусе поля слева.
   Острые углы; --accent определён в теме (см. выше). */
.slot-highlight { outline: 2px solid var(--accent); outline-offset: 3px; }
```

- [ ] **Step 10: Обработчики фокуса в редакторе**

В `webapp/static/editor.js` после блока `byId("addSlide")?.addEventListener(...)`
(после строки 700) добавить:

```js
// Подсветка редактируемого блока на слайде: #deck — iframe того же origin, поэтому
// дотягиваемся до его DOM напрямую (без postMessage). По фокусу поля конструктора
// находим в деке элемент [data-slot=<слот>] и обводим его. На exact/freeform-слайдах
// data-slot нет — подсветка просто не срабатывает.
function highlightSlot(slot, on) {
  const doc = frame.contentDocument;
  if (!doc) return;
  doc.querySelectorAll(".slot-highlight").forEach((n) =>
    n.classList.remove("slot-highlight"));
  if (on && slot) {
    const el = doc.querySelector(`[data-slot="${slot}"]`);
    if (el) el.classList.add("slot-highlight");
  }
}

byId("builderForm")?.addEventListener("focusin", (e) => {
  const holder = e.target.closest("[data-slot]");
  if (holder) highlightSlot(holder.dataset.slot, true);
});
byId("builderForm")?.addEventListener("focusout", (e) => {
  const holder = e.target.closest("[data-slot]");
  if (holder) highlightSlot(holder.dataset.slot, false);
});
```

- [ ] **Step 11: Ручная проверка + сверка «ничего не поехало»**

Запустить `uvicorn webapp.app:app --reload`, открыть конструктор. Проверить критерии:
- фокус на поле (заголовок, пункт списка, под-поле группы) → соответствующий блок на
  слайде получает зелёную обводку; уход фокуса → обводка исчезает;
- на разных типах слайдов (обложка, stats-row, bar-chart, two-col-cards, before-after,
  section-dots) обводка попадает в правильный блок;
- визуально мастера не изменились от добавления `data-slot` (атрибут не влияет на
  геометрию); при сомнении — сравнить скриншот слайда до/после ветки.
- «Точный перенос» и обычная сборка открываются без ошибок в консоли.

- [ ] **Step 12: Коммит**

```bash
git add htmlslides/engine/deck.css webapp/static/editor.js
git commit -m "$(cat <<'EOF'
feat: highlight the edited slot on the slide preview

Focusing a field in the constructor outlines the matching [data-slot] block in the
same-origin deck iframe; blur clears it. Adds the .slot-highlight rule to deck.css.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Финальная проверка (после всех задач)

- [ ] Прогнать все тесты: `python -m pytest -q` и `node --test tests/js/errtext.test.js` — всё зелёное.
- [ ] Пройти три точки входа вручную, обе темы (dark/light), узкую ширину.
- [ ] Сверить критерии приёмки каждого пункта спеки
      (`docs/superpowers/specs/2026-07-15-quick-wins-design.md`).
- [ ] Завершить ветку через superpowers:finishing-a-development-branch.
