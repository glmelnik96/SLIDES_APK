# Переработка UX стеклянной сборки — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Экран сборки как отдельный полноэкранный этап с честной телеметрией, фоновая досборка в редакторе, лёгкие миниатюры (один слайд на iframe, DOM-перестановка, rAF), точечное «Улучшить этот слайд» вместо общедековой кнопки, стеклянный путь — дефолт веба.

**Architecture:** Серверный glass-контракт (`/glass/start|step|score|answer`) не меняется. Клиент получает полноэкранный оверлей `#glassOverlay` поверх `/editor?glass=1`, компакт-индикатор `#glassMini` для фоновой досборки, а вся новая чистая логика (степпер этапов, телеметрия, авто-переход) живёт в errtext.js под `node --test`. Сервер получает две ручки: `GET /deck?slide=N` (один слайд) и `POST /drafts/{sid}/slides/{i}/improve` (пер-слайдовый QA+автофикс).

**Tech Stack:** FastAPI (webapp/app.py), ванильный JS (webapp/static), pytest, `node --test`.

**Спека:** `docs/superpowers/specs/2026-08-20-glass-ux-rework-design.md`.

---

## Карта файлов

| Файл | Что происходит |
|---|---|
| `webapp/static/errtext.js` | + 8 чистых функций glass-UX; − `REBUILD_LABEL`, `rebuildEstimate` |
| `tests/js/glass.test.js` | + тесты новых функций |
| `tests/js/errtext.test.js` | − тесты `REBUILD_LABEL`/`rebuildEstimate` |
| `webapp/deck_edit.py` | + `extract_slide(html, index)` |
| `webapp/app.py` | `get_deck`: параметр `slide`; + `improve_draft_slide` |
| `webapp/glass.py` | + `NotReady`, `improve_slide()` |
| `tests/test_deck_edit.py`, `tests/test_draft.py`, `tests/test_glass.py` | pytest на новое |
| `webapp/static/editor.html` | + `#glassOverlay`, `#glassMini`, `#improveWrap`; − `#rebuild` |
| `webapp/static/styles.css` | стили оверлея/индикатора/кнопки (канон v5) |
| `webapp/static/editor.js` | оверлей, тик-телеметрия, лёгкие тумбы, DOM-move, rAF, improve, − rebuild |
| `webapp/static/index.html`, `webapp/static/app.js` | одна карточка запуска → glass; точный перенос — прежним чёрным ящиком |

Ключевые якоря editor.js (4311 строк): `loadDeck` :28, `handleFrameLoad`→`buildThumbs()` :247, `buildThumbs` :449, `thumbGrip` :558, `reloadDraft` :1178, `renderBuilderForm` :1319, `initDraftBuilder` :3202, rebuild-обработчик :3216-3243, glass-блок :3572-4229, init :4231-4311.

---

### Task 1: Чистая логика glass-UX в errtext.js (TDD)

**Files:**
- Modify: `webapp/static/errtext.js` (вставка перед блоком экспортов, строка ~336)
- Test: `tests/js/glass.test.js`
- Modify: `tests/js/errtext.test.js` (убрать REBUILD_LABEL/rebuildEstimate)

- [ ] **Step 1: Написать падающие тесты**

Дописать в конец `tests/js/glass.test.js` (до закрывающих скобок файла нет — тесты идут плоско, `require("../../webapp/static/errtext.js")` уже есть в шапке; новые функции добавить в деструктуризацию импорта):

```js
const P = (slides) => ({ slides });
const S = (over) => Object.assign(
  { brief: "Тема\nтекст", filled: false, status: null }, over);

test("glassCurrentTarget: первый brief&&!filled&&без статуса, зеркало _next_index", () => {
  const plan = P([S({ filled: true }), S({ status: "needs_input" }),
                  S({ brief: "Как устроен процесс\nдетали" }), S({})]);
  const t = glassCurrentTarget(plan);
  assert.strictEqual(t.index, 3);
  assert.strictEqual(t.label, "Как устроен процесс");
});

test("glassCurrentTarget: работы нет → null", () => {
  assert.strictEqual(glassCurrentTarget(P([S({ filled: true })])), null);
  assert.strictEqual(glassCurrentTarget(null), null);
});

test("glassSlideLabel: заголовок > бриф > номер; длинное режется", () => {
  assert.strictEqual(glassSlideLabel({ fields: { title: "Итоги" } }, 5), "Итоги");
  assert.strictEqual(glassSlideLabel({ content: { title: "Планы" } }, 5), "Планы");
  assert.strictEqual(glassSlideLabel({ brief: "Тема раздела\nтело" }, 5), "Тема раздела");
  assert.strictEqual(glassSlideLabel({}, 5), "Слайд 5");
  assert.ok(glassSlideLabel({ brief: "х".repeat(80) }, 1).length <= 48);
});

test("glassScoutTarget: первый unscored", () => {
  assert.strictEqual(glassScoutTarget(P([S({ filled: true }), S({ status: "unscored" })])), 2);
  assert.strictEqual(glassScoutTarget(P([S({})])), null);
});

test("glassFillLine/glassScoutLine — телеметрия с тикающим таймером", () => {
  assert.strictEqual(glassFillLine({ index: 4, label: "Процесс" }, 23),
    "Заполняю слайд 4 — «Процесс»… 23 с");
  assert.strictEqual(glassFillLine({ index: 4, label: "Процесс" }, null),
    "Заполняю слайд 4 — «Процесс»…");
  assert.strictEqual(glassFillLine(null, 5), "");
  assert.strictEqual(glassScoutLine(6), "параллельно подбираю макет для слайда 6");
  assert.strictEqual(glassScoutLine(null), "");
});

test("glassStages: степпер этапов Документ→Раскладка→Заполнение→Редактор", () => {
  // раскладка идёт (есть unscored)
  let st = glassStages({ total: 8, unscored: 3, filled: 2, loopDone: false });
  assert.deepStrictEqual(st.map((s) => s.state), ["done", "active", "active", "todo"]);
  assert.strictEqual(st[2].label, "Заполнение 2/8");
  // раскладка кончилась, заполнение идёт
  st = glassStages({ total: 8, unscored: 0, filled: 5, loopDone: false });
  assert.deepStrictEqual(st.map((s) => s.state), ["done", "done", "active", "todo"]);
  // всё заполнено
  st = glassStages({ total: 8, unscored: 0, filled: 8, loopDone: true });
  assert.deepStrictEqual(st.map((s) => s.state), ["done", "done", "done", "active"]);
  // самое начало: план ещё пуст
  st = glassStages({ total: 0, unscored: 0, filled: 0, loopDone: false });
  assert.deepStrictEqual(st.map((s) => s.state), ["done", "active", "todo", "todo"]);
});

test("glassAutoExitReady: авто-переход когда автозаполнение исчерпано", () => {
  // остались только слайды-вопросы → выходим
  assert.ok(glassAutoExitReady(P([S({ filled: true }), S({ status: "needs_input" })])));
  // всё заполнено (осечка = filled заглушкой) → выходим
  assert.ok(glassAutoExitReady(P([S({ filled: true }), S({ filled: true, status: "failed" })])));
  // есть незаполненный без вопроса (в очереди/unscored) → рано
  assert.ok(!glassAutoExitReady(P([S({ filled: true }), S({})])));
  assert.ok(!glassAutoExitReady(P([S({ status: "unscored" })])));
  // пустой план → рано
  assert.ok(!glassAutoExitReady(P([])));
  assert.ok(!glassAutoExitReady(null));
});

test("glassMiniText: компакт-индикатор досборки", () => {
  assert.strictEqual(
    glassMiniText({ working: true, filled: 5, total: 8, line: "заполняю «Итоги»… 12 с" }),
    "Досборка: 5 из 8 · заполняю «Итоги»… 12 с");
  assert.strictEqual(glassMiniText({ working: true, filled: 5, total: 8, line: "" }),
    "Досборка: 5 из 8");
  assert.strictEqual(glassMiniText({ working: false, open: 2 }),
    "2 вопроса ждут ответа");
  assert.strictEqual(glassMiniText({ working: false, open: 0 }), "");
});
```

В `tests/js/errtext.test.js`: удалить `REBUILD_LABEL` и `rebuildEstimate` из require-деструктуризации (строка 3) и тесты на строках 202-205 и 232-241.

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `node --test tests/js/`
Expected: FAIL — `glassCurrentTarget is not defined` (и соседние).

- [ ] **Step 3: Реализация в errtext.js**

Вставить перед `root.SAVE_STATUS = SAVE_STATUS;` (после `glassStatusText`, ~строка 335):

```js
  // ── экран сборки (переработка UX 2026-08-20) ──────────────────────────────
  // Подпись слайда для телеметрии и скелетов ленты: заголовок заполненного,
  // иначе первая строка брифа (тема раздела из плана). Никакого фейка: нет
  // темы — честное «Слайд N».
  function glassSlideLabel(s, n) {
    var t = (s && s.fields && s.fields.title) ||
            (s && s.content && s.content.title) || "";
    if (!t && s && s.brief) t = String(s.brief).split("\n")[0];
    t = String(t || "").trim();
    if (t.length > 48) t = t.slice(0, 47).replace(/\s+$/, "") + "…";
    return t || ("Слайд " + n);
  }

  // Кого степпер возьмёт следующим — зеркало серверного _next_index: первый
  // слайд с темой, не заполненный и без статуса (unscored/needs_input/failed
  // пропускаются, как на сервере). По нему телеметрия знает, что заполняется
  // ПРЯМО СЕЙЧАС: пока шаг в полёте, слайд в плане ещё не filled.
  function glassCurrentTarget(plan) {
    var slides = (plan && plan.slides) || [];
    for (var i = 0; i < slides.length; i++) {
      var s = slides[i];
      if (s && s.brief && !s.filled && !s.status)
        return { index: i + 1, label: glassSlideLabel(s, i + 1) };
    }
    return null;
  }

  function glassScoutTarget(plan) {
    var slides = (plan && plan.slides) || [];
    for (var i = 0; i < slides.length; i++)
      if (slides[i] && slides[i].status === "unscored") return i + 1;
    return null;
  }

  // Таймер тикает с первой секунды — это и есть замена плашки «дольше обычного».
  function glassFillLine(target, seconds) {
    if (!target) return "";
    var t = "Заполняю слайд " + target.index + " — «" + target.label + "»…";
    if (seconds != null) t += " " + Math.max(0, Math.round(seconds)) + " с";
    return t;
  }

  function glassScoutLine(index) {
    return index ? "параллельно подбираю макет для слайда " + index : "";
  }

  // Степпер этапов: Документ → Раскладка → Заполнение N/M → Редактор.
  // Документ done всегда: экран существует только после удачного /glass/start.
  function glassStages(s) {
    var layoutDone = s.total > 0 && !s.unscored;
    var fillDone = s.total > 0 && s.filled >= s.total;
    return [
      { label: "Документ", state: "done" },
      { label: "Раскладка", state: layoutDone ? "done" : "active" },
      { label: s.total ? "Заполнение " + s.filled + "/" + s.total : "Заполнение",
        state: fillDone ? "done"
          : (s.total && (layoutDone || s.filled)) ? "active" : "todo" },
      { label: "Редактор", state: fillDone ? "active" : "todo" },
    ];
  }

  // Авто-переход в редактор (решение B): автозаполнение исчерпано — все слайды
  // заполнены либо остались только слайды-вопросы (needs_input дозаполняются из
  // редактора через карточки). Осечка (failed) заполнена заглушкой — не держит.
  function glassAutoExitReady(plan) {
    var slides = (plan && plan.slides) || [];
    var hasWork = false, total = 0;
    for (var i = 0; i < slides.length; i++) {
      var s = slides[i];
      if (!s || !s.brief) continue;
      total++;
      if (!s.filled && s.status !== "needs_input") hasWork = true;
    }
    return total > 0 && !hasWork;
  }

  // Текст компакт-индикатора досборки над сценой. Пустая строка = индикатору
  // нечего сказать (всё заполнено и вопросов нет) — он исчезает.
  function glassMiniText(s) {
    if (s.working) {
      var t = "Досборка: " + s.filled + " из " + s.total;
      if (s.line) t += " · " + s.line;
      return t;
    }
    var open = s.open || 0;
    if (open) return open + " " +
      plural(open, "вопрос ждёт", "вопроса ждут", "вопросов ждут") + " ответа";
    return "";
  }
```

Экспортировать все 8 функций в оба блока (`root.…` и `module.exports`): `glassSlideLabel, glassCurrentTarget, glassScoutTarget, glassFillLine, glassScoutLine, glassStages, glassAutoExitReady, glassMiniText`. Удалить `REBUILD_LABEL` (строка ~20) и `rebuildEstimate` (определение + оба экспорта) — кнопка уходит из UI в Task 7 этого же PR-цикла.

- [ ] **Step 4: Тесты зелёные**

Run: `node --test tests/js/`
Expected: PASS все (включая старые glassStepDecision/glassStatusText).

- [ ] **Step 5: Commit**

```bash
git add webapp/static/errtext.js tests/js/glass.test.js tests/js/errtext.test.js
git commit -m "feat(glass): чистая логика экрана сборки — степпер, телеметрия, авто-переход"
```

---

### Task 2: Один слайд из деки — `extract_slide` + `GET /deck?slide=N` (TDD)

**Files:**
- Modify: `webapp/deck_edit.py` (после `slides_with_hardcoded_colors`, ~строка 85)
- Modify: `webapp/app.py:1080-1089` (`get_deck`)
- Test: `tests/test_deck_edit.py`, `tests/test_draft.py`

- [ ] **Step 1: Падающий юнит-тест в `tests/test_deck_edit.py`**

```python
def test_extract_slide_keeps_head_tail_and_one_section():
    html = ('<html data-theme="dark"><head><style>.s{}</style></head><body>'
            '<section class="slide">ONE</section>\n'
            '<section class="slide">TWO</section>\n'
            '<section class="slide">THREE</section>'
            '<script>var deck=1;</script></body></html>')
    out = deck_edit.extract_slide(html, 2)
    assert out.count("<section") == 1
    assert "TWO" in out and "ONE" not in out and "THREE" not in out
    assert "<style>" in out and "var deck=1" in out       # шапка и хвост живы
    assert deck_edit.extract_slide(html, 0) is None
    assert deck_edit.extract_slide(html, 4) is None
```

(Импорт `deck_edit` в файле уже есть — тесты рядом им пользуются.)

- [ ] **Step 2: Тест падает** — `pytest tests/test_deck_edit.py -x -q` → `AttributeError: extract_slide`.

- [ ] **Step 3: Реализация в deck_edit.py**

```python
def extract_slide(html: str, index: int) -> str | None:
    """Полный HTML-документ, в котором оставлен ТОЛЬКО слайд index (1-based).

    Шапка (стили, токены темы) и хвост (встроенный deck.js) сохраняются —
    документ остаётся самодостаточным. Лёгкие миниатюры редактора грузят один
    слайд вместо всей деки: 8 миниатюр = 8 документов, а не 8×N рендеров.
    Вне диапазона — None."""
    starts = [m.start() for m in _SLIDE_RE.finditer(html)]
    if not 1 <= index <= len(starts):
        return None
    spans = []
    for i, start in enumerate(starts):
        close = html.find("</section>", start)
        end = close + len("</section>") if close != -1 else (
            starts[i + 1] if i + 1 < len(starts) else len(html))
        spans.append((start, end))
    return (html[:spans[0][0]] + html[spans[index - 1][0]:spans[index - 1][1]]
            + html[spans[-1][1]:])
```

- [ ] **Step 4: Юнит зелёный** — `pytest tests/test_deck_edit.py -x -q` → PASS.

- [ ] **Step 5: Падающий эндпоинт-тест в `tests/test_draft.py`**

Рядом с тестом на строке ~264 (`.count("<section") == 2` — там уже есть черновик из 2 слайдов; повторить его сетап — создание черновика и добавление двух слайдов тем же способом, что в соседнем тесте):

```python
def test_deck_single_slide_param(...):   # сигнатура/фикстуры как у соседнего теста
        # ...тот же сетап: черновик sid с двумя слайдами...
        one = c.get(f"/api/jobs/{sid}/deck?slide=2", headers=H())
        assert one.status_code == 200
        assert one.text.count("<section") == 1
        assert c.get(f"/api/jobs/{sid}/deck?slide=99",
                     headers=H()).status_code == 404
```

- [ ] **Step 6: Реализация в app.py `get_deck`**

```python
@app.get("/api/jobs/{session_id}/deck", response_class=HTMLResponse)
async def get_deck(session_id: str, request: Request, download: int = 0,
                   slide: int = 0, user=Depends(get_current_user)):
    await _owned_or_404(request, session_id, user)
    path = deck_edit.ensure_deck(session_id, runner.result_path(session_id))
    if path is None:
        raise HTTPException(404, "дека не найдена")
    if download:
        return FileResponse(path, filename="deck.html", media_type="text/html")
    html = path.read_text("utf-8")
    if slide:
        # Лёгкая миниатюра редактора: документ с одним слайдом вместо всей деки.
        html = deck_edit.extract_slide(html, slide)
        if html is None:
            raise HTTPException(404, f"слайда {slide} в деке нет")
    return HTMLResponse(html)
```

- [ ] **Step 7: Тесты зелёные** — `pytest tests/test_deck_edit.py tests/test_draft.py -x -q` → PASS.

- [ ] **Step 8: Commit**

```bash
git add webapp/deck_edit.py webapp/app.py tests/test_deck_edit.py tests/test_draft.py
git commit -m "feat(deck): параметр slide=N — документ с одним слайдом для лёгких миниатюр"
```

---

### Task 3: Лёгкие миниатюры — slide=N, точечный рефреш, DOM-перестановка, rAF

**Files:**
- Modify: `webapp/static/editor.js` (`buildThumbs` :449, `handleFrameLoad` :247, `reloadDraft` :1178, drag-блок :2781-2924)

- [ ] **Step 1: buildThumbs — один слайд на iframe + живые индексы**

В `buildThumbs()` заменить строку 471:

```js
    ifr.src = U(`/api/jobs/${sessionId}/deck?t=${deckT}&editor=1#${i + 1}`);
```
на
```js
    // 2а: один слайд на iframe — миниатюра грузит лёгкий документ, а не всю деку.
    ifr.src = U(`/api/jobs/${sessionId}/deck?t=${deckT}&editor=1&slide=${i + 1}`);
```

И убрать устаревающие замыкания по `i` (после DOM-перестановки индексы «плывут»):
- строка 509: `t.onclick = () => goTo(i);` → `t.onclick = () => goTo(Number(t.dataset.index));`
- строка 517: `deleteSlideAt(i)` → `deleteSlideAt(Number(t.dataset.index))`

- [ ] **Step 2: Пересборка ленты — только при смене состава**

Заменить вызов `buildThumbs();` в `handleFrameLoad` (строка 247) на `syncThumbs();` и добавить после `buildThumbs` (после `hintVariant`, ~строка 552):

```js
// Полная пересборка ленты — только при реальной смене состава. Обычный сейв
// поля или glass-шаг перерисовывает точечно: текущий слайд и свежезаполненный.
let thumbsDirty = true;                    // первый рендер — полная сборка
function syncThumbs() {
  const box = document.getElementById("thumbs");
  const have = box ? box.querySelectorAll(".thumb").length : 0;
  if (thumbsDirty || have !== slides.length) {
    thumbsDirty = false;
    buildThumbs();
    return;
  }
  refreshThumb(current);
  if (pendingGoTo !== current) refreshThumb(pendingGoTo);
  syncThumbBadges();
}

function refreshThumb(i) {
  const box = document.getElementById("thumbs");
  const t = box && box.querySelectorAll(".thumb")[i];
  if (!t) return;
  const ifr = t.querySelector("iframe");
  if (ifr) ifr.src = U(`/api/jobs/${sessionId}/deck?t=${deckT}&editor=1&slide=${i + 1}`);
  const s = slides[i];
  const titleText = s
    ? (s.querySelector("h1,h2,h3,[data-slot=title]")?.textContent || "").trim() : "";
  let ttl = t.querySelector(".thumb-title");
  if (titleText && !ttl) {
    ttl = document.createElement("span");
    ttl.className = "thumb-title";
    t.querySelector(".thumb-cap")?.appendChild(ttl);
  }
  if (ttl) ttl.textContent = titleText;
}

// Метки «?» / «!» / «⟳» без пересборки: статусы в glass-режиме меняются на
// каждом шаге, а раньше их доносила только полная пересборка ленты.
function syncThumbBadges() {
  const box = document.getElementById("thumbs");
  if (!box || !isDraft) return;
  const filling = glassRunning && glassLooping
    ? (glassCurrentTarget(draftPlan) || {}).index : null;
  [...box.querySelectorAll(".thumb")].forEach((t, i) => {
    t.querySelector(".thumb-quest")?.remove();
    const ds = draftPlan.slides[i];
    if (!ds) return;
    let cls = "", title = "", mark = "";
    if (ds.status === "needs_input" && !ds.filled) {
      cls = "thumb-quest"; mark = "?";
      title = "ИИ ждёт вашего ответа по этому слайду";
    } else if (ds.status === "failed") {
      cls = "thumb-quest thumb-quest--failed"; mark = "!";
      title = "Слайд не заполнился — можно выбрать макет ещё раз";
    } else if (filling === i + 1) {
      cls = "thumb-quest thumb-quest--filling"; mark = "⟳";
      title = "Слайд сейчас заполняется — подождите";
    }
    if (!cls) return;
    const m = document.createElement("span");
    m.className = cls; m.title = title; m.textContent = mark;
    t.appendChild(m);
  });
}
```

В `buildThumbs` вынести блок меток (строки 488-508) в вызов `syncThumbBadges()` после цикла — чтобы логика меток жила в одном месте (внутри цикла блок `if (ds …)` удалить, после `box.appendChild(t);`-цикла добавить `syncThumbBadges();`).

В `reloadDraft` (строка 1178) добавить первой строкой тела `thumbsDirty = true;` — структурные правки честно пересобирают ленту.

- [ ] **Step 3: moveSlide — перестановка DOM вместо полной пересборки**

Заменить `moveSlide` (строки 2912-2924) на:

```js
async function moveSlide(idx, to1) {
  if (to1 < 1 || to1 > draftPlan.slides.length) return;
  await flushPendingSave(); // preserve the moving slide's edit before reordering
  const r = await fetch(U(`/api/drafts/${sessionId}/slides/${idx + 1}/move`), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ to: to1 }),
  }).catch(() => null);
  if (!r || !r.ok) { setSaveStatus("error"); return; }
  pushUndo();
  // 2а: сервер уже переставил слайд в plan.json — повторяем то же локально и
  // в DOM ленты. Полной пересборки (reloadDraft) нет: она перегружала 8 полных
  // дек на каждый move и лента «мигала».
  const [moved] = draftPlan.slides.splice(idx, 1);
  draftPlan.slides.splice(to1 - 1, 0, moved);
  dgmUnsaved = null;
  builtFormFor = -1;
  pendingGoTo = to1 - 1;
  loadDeck();                    // сцена перерисуется; deckT обновился для src
  moveThumbDom(idx, to1 - 1);
}

function moveThumbDom(from, to0) {
  const box = document.getElementById("thumbs");
  const thumbs = box ? [...box.querySelectorAll(".thumb")] : [];
  const moving = thumbs[from];
  if (!moving) { thumbsDirty = true; return; }
  const rest = thumbs.filter((_, i) => i !== from);
  const ref = rest[to0] || box.querySelector(".thumbs-hint");
  box.insertBefore(moving, ref);
  // Подписи — у всех; iframe-адреса — только у сдвинувшегося диапазона
  // (контент узлов переехал вместе с ними, догрузка ленивая и фоновая).
  const lo = Math.min(from, to0), hi = Math.max(from, to0);
  [...box.querySelectorAll(".thumb")].forEach((t, i) => {
    t.dataset.index = i;
    const num = t.querySelector(".thumb-num");
    if (num) num.textContent = i + 1;
    if (i >= lo && i <= hi) {
      const ifr = t.querySelector("iframe");
      if (ifr) ifr.src = U(`/api/jobs/${sessionId}/deck?t=${deckT}&editor=1&slide=${i + 1}`);
    }
  });
}
```

- [ ] **Step 4: rAF-троттлинг pointermove/dragover**

Заменить `onThumbDragOver` (строки 2806-2813) и `onGripMove` (строки 2854-2868):

```js
let dragRaf = 0;
function onThumbDragOver(e) {
  if (dragFromIndex === null) return;
  e.preventDefault(); // разрешаем drop
  e.dataTransfer.dropEffect = "move";
  if (dragRaf) return;                 // геометрия — не чаще раза на кадр
  const self = this, cx = e.clientX, cy = e.clientY;
  dragRaf = requestAnimationFrame(() => {
    dragRaf = 0;
    const after = dropAfter(self, { clientX: cx, clientY: cy });
    self.classList.toggle("drop-after", after);
    self.classList.toggle("drop-before", !after);
  });
}
```

```js
let gripRaf = 0;
function onGripMove(e) {
  if (!gripDrag) return;
  e.preventDefault();
  if (gripRaf) return;                 // elementFromPoint — не чаще раза на кадр
  const cx = e.clientX, cy = e.clientY;
  gripRaf = requestAnimationFrame(() => {
    gripRaf = 0;
    if (!gripDrag) return;
    clearDropMarks();
    gripDrag.over = null;
    // Захват указателя увёл события на рукоятку — цель ищем по координате.
    const el = document.elementFromPoint(cx, cy);
    const over = el && el.closest && el.closest(".thumb");
    if (!over || over === gripDrag.thumb) return;
    const after = dropAfter(over, { clientX: cx, clientY: cy });
    over.classList.toggle("drop-after", after);
    over.classList.toggle("drop-before", !after);
    gripDrag.over = Number(over.dataset.index);
    gripDrag.after = after;
  });
}
```

(`glassCurrentTarget`/`glassRunning`/`glassLooping` в `syncThumbBadges` определяются ниже по файлу — это допустимо: функция зовётся только после init.)

- [ ] **Step 5: Проверка руками через preview**

`python scripts/devserver.py` + MCP preview: черновик из 3+ слайдов → в Network видно `deck?…&slide=N` у миниатюр; перетащить миниатюру → порядок меняется без перезагрузки всех iframe; F5 → порядок жив.

- [ ] **Step 6: Commit**

```bash
git add webapp/static/editor.js
git commit -m "perf(editor): лёгкие миниатюры — один слайд на iframe, DOM-move без пересборки, rAF"
```

---

### Task 4: Разметка и стили — оверлей, индикатор, improve-кнопка, минус rebuild

**Files:**
- Modify: `webapp/static/editor.html`
- Modify: `webapp/static/styles.css` (в конец файла)

- [ ] **Step 1: editor.html**

1. Удалить кнопку `#rebuild` (строка 16, вместе с title).
2. В `.stage-col` перед `<div id="frameWrap">` вставить:

```html
    <!-- Компакт-индикатор фоновой досборки (glass): мини-прогресс + телеметрия
         + счётчик вопросов. Исчезает, когда всё заполнено и вопросов нет. -->
    <div class="glass-mini hidden" id="glassMini">
      <div class="glass-mini__bar"><i id="glassMiniFill"></i></div>
      <span class="glass-mini__text" id="glassMiniText" aria-live="polite"></span>
      <button class="btn btn-ghost btn-sm hidden" id="glassMiniQ" type="button"></button>
      <button class="btn btn-ghost btn-sm hidden" id="glassMiniRest" type="button"></button>
    </div>
```

3. В `#builder` после `<form class="builder-form" id="builderForm"></form>` вставить:

```html
    <!-- Точечное улучшение: LLM-проверка вёрстки и вида ОДНОГО слайда.
         Активна только после полного завершения сборки (решение спеки). -->
    <div class="builder-improve hidden" id="improveWrap">
      <button class="btn btn-ghost btn-sm" id="improveSlide" type="button">Улучшить этот слайд (~1–2 мин)</button>
      <span class="improve-note" id="improveNote"></span>
    </div>
```

4. Перед `<div class="undo-toast …>` вставить полноэкранный оверлей:

```html
<!-- Экран сборки (?glass=1) — полноэкранный этап поверх редактора (решение 1а).
     Степпер этапов, честный прогресс, телеметрия с таймером, крупный последний
     слайд, карточки вопросов, лента честных скелетов. -->
<div class="glass-overlay hidden" id="glassOverlay">
  <div class="glass-overlay__top">
    <span class="glass-overlay__file" id="gloFile"></span>
    <div class="glass-overlay__stepper" id="gloStepper"></div>
    <span class="sp"></span>
    <button class="btn" id="gloExit" type="button"
            title="Сборка продолжится в фоне — слайды доедут сами">В редактор сейчас &rarr;</button>
  </div>
  <div class="glass-bar glass-overlay__bar"><i id="gloBarFill"></i></div>
  <div class="glass-overlay__tele">
    <span id="gloTele">Раскладываю документ…</span>
    <span class="glass-overlay__scout" id="gloScout"></span>
  </div>
  <div class="glass-overlay__err hidden" id="gloErr" role="alert"></div>
  <div class="glass-overlay__center">
    <div class="glass-overlay__stagebox">
      <iframe id="gloSlide" title="Последний собранный слайд" tabindex="-1" class="hidden"></iframe>
      <div class="glass-overlay__cap" id="gloCap">Слайды появятся здесь по мере заполнения</div>
    </div>
    <div class="glass-overlay__cards" id="gloCards"></div>
  </div>
  <div class="glass-overlay__film" id="gloFilm"></div>
</div>
```

- [ ] **Step 2: styles.css — в конец файла, канон v5 («тихие поверхности», лестница серых, существующие токены)**

```css
/* ── Экран сборки (glass): полноэкранный этап поверх редактора ─────────── */
.glass-overlay {
  position: fixed; inset: 0; z-index: 60;
  display: flex; flex-direction: column; gap: 12px;
  padding: 20px 28px; background: var(--bg, #17181c);
}
.glass-overlay__top { display: flex; align-items: center; gap: 16px; }
.glass-overlay__file { font-weight: 600; opacity: .9; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; max-width: 32ch; }
.glass-overlay__stepper { display: flex; gap: 14px; font-size: 13px; }
.glo-stage--done { color: var(--ok, #4caf7d); }
.glo-stage--active { color: var(--fg, #e8e8ea); }
.glo-stage--todo { opacity: .4; }
.glass-overlay__bar { height: 10px; }
.glass-overlay__tele { display: flex; justify-content: space-between;
  font-size: 13px; opacity: .85; min-height: 1.2em; }
.glass-overlay__scout { opacity: .7; }
.glass-overlay__err { font-size: 13px; display: flex; gap: 10px;
  align-items: center; }
.glass-overlay__center { display: flex; gap: 14px; flex: 1; min-height: 0; }
.glass-overlay__stagebox { flex: 2.4; position: relative; min-width: 0;
  display: flex; flex-direction: column; gap: 6px; }
.glass-overlay__stagebox iframe { width: 100%; flex: 1; border: 0;
  border-radius: 8px; background: var(--bg-card, #202127); }
.glass-overlay__cap { font-size: 12px; opacity: .6; text-align: center; }
.glass-overlay__cards { flex: 1; min-width: 260px; max-width: 380px;
  overflow-y: auto; display: flex; flex-direction: column; gap: 10px; }
.glass-overlay__film { display: flex; gap: 8px; overflow-x: auto;
  padding-bottom: 4px; }
.glo-cell { flex: 1 0 110px; max-width: 150px; aspect-ratio: 16/9;
  border-radius: 6px; background: var(--bg-card, #202127);
  display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 4px; font-size: 11px; overflow: hidden;
  position: relative; }
.glo-cell--queued { opacity: .55; border: 1px dashed var(--border, #33343a); }
.glo-cell--filling { border: 1px dashed var(--warn, #b9973a); }
.glo-cell--quest { border: 1px dashed var(--accent, #7da4d4); }
.glo-cell--quest .glo-cell__mark { color: var(--accent, #7da4d4); }
.glo-cell--failed { border: 1px dashed var(--err, #c96a5c); }
.glo-cell--ready iframe { width: 100%; height: 100%; border: 0;
  pointer-events: none; }
.glo-cell__label { opacity: .7; padding: 0 6px; text-align: center;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  max-width: 100%; }

/* ── Компакт-индикатор досборки над сценой ─────────────────────────────── */
.glass-mini { display: flex; align-items: center; gap: 10px;
  padding: 6px 10px; border-radius: 6px; background: var(--bg-card, #202127);
  font-size: 12px; margin-bottom: 8px; }
.glass-mini__bar { width: 120px; height: 6px; border-radius: 3px;
  overflow: hidden; background: var(--bg-inset, #2a2b31); flex: none; }
.glass-mini__bar i { display: block; height: 100%; width: 0;
  background: var(--ok, #4caf7d); transition: width .3s; }
.glass-mini__text { opacity: .85; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }

/* ── Точечное улучшение слайда ─────────────────────────────────────────── */
.builder-improve { margin-top: auto; padding-top: 10px; display: flex;
  flex-direction: column; gap: 4px; }
.improve-note { font-size: 11px; opacity: .6; }
```

Перед коммитом сверить имена токенов с началом styles.css (`--bg`, `--bg-card`, `--border`, `--ok`, `--warn`, `--err`, `--accent`) — использовать реально существующие в проекте, фолбэки в `var()` уже стоят на случай отличий.

- [ ] **Step 3: Commit**

```bash
git add webapp/static/editor.html webapp/static/styles.css
git commit -m "feat(glass): разметка экрана сборки, компакт-индикатора и точечного улучшения"
```

---

### Task 5: editor.js — экран сборки (оверлей)

**Files:**
- Modify: `webapp/static/editor.js` (glass-блок :3572-3844, glassFail :3907, renderGlassQuestions :3917, exitGlassMode :4214)

- [ ] **Step 1: Состояние и тик**

После объявлений glass-состояния (после `const glassBaseTitle…`, строка 3589) добавить:

```js
let glassOverlayOn = false;   // полноэкранный этап сборки поверх редактора
let glassStepT0 = 0;          // старт текущего шага — таймер телеметрии
let glassTickTimer = null;    // секундный тик телеметрии и ленты
const gloFilmFilled = {};     // 1-based index → ячейка уже живой мини-рендер
let gloShown = 0;             // какой слайд крупно показан в центре

function glassTickStart() {
  if (!glassTickTimer) glassTickTimer = setInterval(renderGlassTele, 1000);
}
function glassTickStop() {
  clearInterval(glassTickTimer);
  glassTickTimer = null;
}
```

- [ ] **Step 2: Вход/выход оверлея + правки startGlassMode**

В `startGlassMode()` (строка 3597): удалить строку `byId("rebuild")?.classList.add("hidden");` и строку `byId("glassPanel")?.classList.remove("hidden");` (панель больше не главный экран — её показывает тумблер индикатора); добавить перед `renderGlassPanel(null);`:

```js
  byId("gloExit")?.addEventListener("click", leaveGlassOverlay);
  byId("glassMiniQ")?.addEventListener("click", toggleGlassQuestionsPanel);
  byId("glassMiniRest")?.addEventListener("click", () => {
    toggleGlassQuestionsPanel(true);   // хвост живёт в панели сборки
  });
  enterGlassOverlay();
```

Новые функции (после `startGlassMode`):

```js
// Полноэкранный этап сборки (решение 1а): с первой секунды после загрузки
// документа и до исчерпания автозаполнения (авто-переход, решение B).
function enterGlassOverlay() {
  glassOverlayOn = true;
  byId("glassOverlay")?.classList.remove("hidden");
  const f = byId("gloFile");
  if (f) f.textContent = draftPlan.title || "";
  glassTickStart();
  renderGlassOverlay();
}

// «В редактор сейчас» и авто-переход: оверлей уходит, сборка продолжается в
// фоне под компакт-индикатором. glassRunning остаётся true — inline-правка на
// сцене по-прежнему выключена, а формы/перестановка/смена макета доступны.
function leaveGlassOverlay() {
  if (!glassOverlayOn) return;
  glassOverlayOn = false;
  byId("glassOverlay")?.classList.add("hidden");
  setupPanelTabs();               // обычная правая панель — поля слайда
  builtFormFor = -1;
  renderBuilderForm();
  renderGlassPanel(null);         // перевесит карточки в панель, покажет mini
  loadDeck();
}

// «? N вопросов» в индикаторе: правая панель переключается между полями и
// карточками вопросов сборки. force=true — только показать.
function toggleGlassQuestionsPanel(force) {
  const panel = byId("glassPanel");
  if (!panel) return;
  const show = force === true || panel.classList.contains("hidden");
  panel.classList.toggle("hidden", !show);
  byId("builder")?.classList.toggle("hidden", show);
  byId("rpanelTabs")?.classList.toggle("hidden", show);
}
```

- [ ] **Step 3: Таймер шага в glassSteps + авто-переход**

В `glassSteps()` (строка 3707) внутри `glassEnqueue(async () => { … })` заменить `glassSlowStart();`/`finally { glassSlowStop(); }` на `glassStepT0 = Date.now();` / `finally { glassStepT0 = 0; }`. Удалить целиком блок `GLASS_SLOW_MS`/`glassSlowStart`/`glassSlowStop` (строки 3648-3671) и все их вызовы — телеметрия с таймером заменяет плашку «дольше обычного». Из editor.html можно удалить `#glassSlow` (строка 70).

После строки `renderGlassPanel(out);` в цикле добавить:

```js
    // Решение B: автозаполнение исчерпано (все filled либо остались только
    // слайды-вопросы) → авто-переход в редактор без промежуточного экрана.
    if (glassOverlayOn && glassAutoExitReady(draftPlan)) leaveGlassOverlay();
```

- [ ] **Step 4: Рендер оверлея**

Добавить после `renderGlassPanel` (строка 3844):

```js
function renderGlassOverlay() {
  if (!glassOverlayOn) return;
  const targets = (draftPlan.slides || []).filter((s) => s && s.brief);
  const filled = targets.filter((s) => s.filled).length;
  const total = targets.length;
  const unscored = (draftPlan.slides || [])
    .filter((s) => s && s.status === "unscored").length;
  const st = byId("gloStepper");
  if (st) {
    st.innerHTML = "";
    glassStages({ total, unscored, filled, loopDone: glassLoopDone })
      .forEach((g) => {
        const el = document.createElement("span");
        el.className = "glo-stage glo-stage--" + g.state;
        el.textContent =
          (g.state === "done" ? "✓ " : g.state === "active" ? "⟳ " : "") + g.label;
        st.appendChild(el);
      });
  }
  const bar = byId("gloBarFill");
  if (bar) bar.style.width = total ? Math.round((filled / total) * 100) + "%" : "0%";
  renderGlassTele();
  renderGloFilm();
}

// Телеметрия: тикает каждую секунду — видно, что работа ИДЁТ, с первой секунды.
function renderGlassTele() {
  const secs = glassStepT0 ? (Date.now() - glassStepT0) / 1000 : null;
  const target = glassCurrentTarget(draftPlan);
  const scout = glassScoutTarget(draftPlan);
  let line = "";
  if (glassLooping && target) line = glassFillLine(target, secs);
  else if (glassLooping || glassScouting) line = "Подбираю макеты слайдов…";
  else if (glassLoopDone) line = "Все слайды заполнены.";
  else if (openGlassQuestions().length) line = "Сборка ждёт вашего ответа на вопросы справа.";
  if (glassOverlayOn) {
    const t = byId("gloTele");
    if (t) t.textContent = line || "Раскладываю документ…";
    const sc = byId("gloScout");
    if (sc) sc.textContent = [glassScoutLine(glassScouting ? scout : null),
                              draftPlan.notice || ""].filter(Boolean).join(" · ");
    // Ячейка заполняемого слайда: таймер тикает прямо в ленте.
    const cell = target &&
      byId("gloFilm")?.querySelector(`[data-index="${target.index}"] .glo-cell__mark`);
    if (cell && glassLooping && secs != null)
      cell.textContent = `⟳ ${Math.round(secs)} с`;
  }
  renderGlassMini(line);
}

// Крупный центр — последний собранный слайд (обновляет glassSteps через jump).
function gloShowSlide(n) {
  const ifr = byId("gloSlide");
  if (!ifr || !n) return;
  gloShown = n;
  ifr.classList.remove("hidden");
  ifr.src = U(`/api/jobs/${sessionId}/deck?t=${Date.now()}&editor=1&slide=${n}`);
  const cap = byId("gloCap");
  if (cap) {
    const s = (draftPlan.slides || [])[n - 1];
    cap.textContent = `${n}. ${glassSlideLabel(s || {}, n)} — только что заполнен`;
  }
}

// Лента слайдов: ✓ живой мини-рендер, ⟳ + таймер, ? вопрос, честный скелет с
// темой раздела из плана. Никакого фейкового контента в заглушках.
function renderGloFilm() {
  const film = byId("gloFilm");
  if (!film) return;
  const sl = draftPlan.slides || [];
  if (film.childElementCount !== sl.length) {
    film.innerHTML = "";
    Object.keys(gloFilmFilled).forEach((k) => delete gloFilmFilled[k]);
    for (let i = 0; i < sl.length; i++) {
      const c = document.createElement("div");
      c.dataset.index = i + 1;
      film.appendChild(c);
    }
  }
  const filling = glassLooping ? (glassCurrentTarget(draftPlan) || {}).index : null;
  [...film.children].forEach((c, i) => {
    const s = sl[i], n = i + 1;
    const state = !s ? "queued"
      : s.filled && s.status !== "failed" ? "ready"
      : s.status === "failed" ? "failed"
      : s.status === "needs_input" ? "quest"
      : filling === n ? "filling" : "queued";
    if (state === "ready") {
      if (gloFilmFilled[n]) return;          // уже живой рендер — не дёргаем
      gloFilmFilled[n] = true;
      c.className = "glo-cell glo-cell--ready";
      c.innerHTML = "";
      const f = document.createElement("iframe");
      f.loading = "lazy"; f.tabIndex = -1; f.setAttribute("aria-hidden", "true");
      f.title = `Слайд ${n}`;
      f.src = U(`/api/jobs/${sessionId}/deck?t=${deckT}&editor=1&slide=${n}`);
      c.appendChild(f);
      return;
    }
    delete gloFilmFilled[n];
    c.className = "glo-cell glo-cell--" + state;
    c.innerHTML = "";
    const mark = document.createElement("span");
    mark.className = "glo-cell__mark";
    mark.textContent = state === "filling" ? "⟳"
      : state === "quest" ? "?" : state === "failed" ? "!" : "";
    const lbl = document.createElement("span");
    lbl.className = "glo-cell__label";
    lbl.textContent = glassSlideLabel(s || {}, n);
    c.append(mark, lbl);
  });
}
```

В `glassSteps` строку `if (d.jump != null) pendingGoTo = d.jump;` дополнить:

```js
    if (d.jump != null) { pendingGoTo = d.jump; gloShowSlide(d.jump + 1); }
```

В конец `renderGlassPanel` (перед `document.title = …`) добавить `renderGlassOverlay();`.

- [ ] **Step 5: Карточки вопросов в оверлее + ошибка с «Повторить»**

В `renderGlassQuestions` (строка 3917) заменить `const box = byId("glassQuestions");` на:

```js
  // Оверлей активен — карточки живут справа от крупного слайда; после выхода
  // в редактор те же DOM-узлы переезжают в правую панель (insertBefore переносит).
  const box = glassOverlayOn ? byId("gloCards") : byId("glassQuestions");
```

В `glassFail()` (строка 3907) добавить в конец:

```js
  // 3 осечки подряд на оверлее: русская ошибка + «Повторить» (спека, секция 3).
  const err = byId("gloErr");
  if (err && glassOverlayOn) {
    err.classList.remove("hidden");
    err.textContent = "Сборка прервалась: три шага подряд не удались — " +
      "проверьте соединение. ";
    const b = document.createElement("button");
    b.className = "btn btn-sm"; b.type = "button"; b.textContent = "Повторить";
    b.onclick = () => { err.classList.add("hidden"); glassLoop(); glassScout(); };
    err.appendChild(b);
  }
```

И в начале `glassSteps` (после `failures = 0;` при успехе шага) убедиться, что `byId("gloErr")?.classList.add("hidden");` — успешный шаг гасит ошибку (добавить строку после `failures = 0;`).

- [ ] **Step 6: exitGlassMode — полный демонтаж без rebuild**

Заменить `exitGlassMode` (строки 4214-4229):

```js
function exitGlassMode() {
  glassRunning = false;
  glassOverlayOn = false;
  glassTickStop();
  document.title = glassBaseTitle;
  byId("glassOverlay")?.classList.add("hidden");
  byId("glassMini")?.classList.add("hidden");
  byId("glassPanel")?.classList.add("hidden");
  const badge = byId("modeBadge");
  if (badge) badge.textContent = "Конструктор";
  setupPanelTabs();      // вернуть обычную правую панель («Поля» по умолчанию)
  builtFormFor = -1;
  renderBuilderForm();
  syncImproveButton();   // сборка кончилась — точечное улучшение доступно
  loadDeck();            // перерисовать превью уже с contenteditable
  // F5 после выхода — обычный конструктор, а не перезапуск степпера
  const url = new URL(location.href);
  url.searchParams.delete("glass");
  history.replaceState(null, "", url);
}
```

(`syncImproveButton` появится в Task 7 — до него оставить вызов закомментированным или добавить задел-пустышку нельзя; порядок выполнения: Task 7 идёт в этом же цикле, поэтому просто выполнить Task 7 до ручной проверки. При выполнении строго по порядку — вставить строку в Task 7.)

- [ ] **Step 7: Прогон JS-тестов** — `node --test tests/js/` → PASS (чистая логика не тронута).

- [ ] **Step 8: Commit**

```bash
git add webapp/static/editor.js webapp/static/editor.html
git commit -m "feat(glass): полноэкранный экран сборки — степпер, телеметрия, лента, авто-переход"
```

---

### Task 6: editor.js — фоновая досборка в редакторе (индикатор, замочек)

**Files:**
- Modify: `webapp/static/editor.js` (`renderGlassPanel` :3804, `renderBuilderForm` :1319)

- [ ] **Step 1: Компакт-индикатор**

Добавить после `renderGlassTele` (Task 5):

```js
// Индикатор над сценой (спека, секция 2): виден, пока сборка в фоне или висят
// вопросы. Исчезает, когда все слайды заполнены И вопросов не осталось.
function renderGlassMini(teleLine) {
  const mini = byId("glassMini");
  if (!mini) return;
  const show = glassRunning && !glassOverlayOn;
  mini.classList.toggle("hidden", !show);
  if (!show) return;
  const targets = (draftPlan.slides || []).filter((s) => s && s.brief);
  const filled = targets.filter((s) => s.filled).length;
  const total = targets.length;
  const open = openGlassQuestions().length + glassFailedSlides().length;
  const fill = byId("glassMiniFill");
  if (fill) fill.style.width = total ? Math.round((filled / total) * 100) + "%" : "0%";
  const txt = byId("glassMiniText");
  if (txt) txt.textContent = glassMiniText({
    working: glassLooping || glassScouting, filled, total,
    line: teleLine || "", open,
  });
  const q = byId("glassMiniQ");
  if (q) {
    q.classList.toggle("hidden", !open);
    q.textContent = `? ${open} ${plural(open, "вопрос", "вопроса", "вопросов")}`;
  }
  const rest = byId("glassMiniRest");
  if (rest) {
    const n = draftPlan.rest || 0;
    rest.classList.toggle("hidden", !n);
    if (n) rest.textContent = `Хвост: ещё ${n} ${plural(n, "раздел", "раздела", "разделов")}`;
  }
}
```

Вопросы кончились и работа встала → индикатору нечего сказать, но `glassRunning` ещё true: в `glassResume()` (строка 4200) уже есть выход `exitGlassMode()` при loopDone без вопросов/осечек — этого достаточно; при открытой панели вопросов `toggleGlassQuestionsPanel(false)` вызывается из `exitGlassMode` косвенно (панель прячется классом). Дополнительно в `glassResume` после `renderGlassPanel(null)` ничего менять не нужно.

- [ ] **Step 2: Замочек на заполняемом слайде**

В `renderBuilderForm()` (строка 1319) — сразу после того, как функция определила текущий слайд черновика (первые строки, где берётся `draftPlan.slides[current]`; найти по месту), добавить гард:

```js
  // Слайд, заполняемый сейчас, залочен (спека, секция 2): параллельная правка
  // формы проиграла бы гонку с _fill_one — сервер вклеит свой результат поверх.
  if (glassRunning && glassLooping &&
      (glassCurrentTarget(draftPlan) || {}).index === current + 1) {
    const form = byId("builderForm");
    if (form) form.innerHTML =
      '<p class="builder-locked">⟳ Этот слайд сейчас заполняется — форма ' +
      'откроется, когда ИИ закончит (обычно до минуты).</p>';
    builtFormFor = -1;          // после заполнения форму перерисовать заново
    return;
  }
```

В styles.css (конец): `.builder-locked { font-size: 12px; opacity: .7; padding: 10px 0; }`.

- [ ] **Step 3: Ручная проверка через preview**

devserver + glass-сборка небольшого документа: оверлей с тикающим таймером → «В редактор сейчас» → индикатор с телеметрией, форма текущего заполняемого слайда залочена, соседние редактируются; вопросы открываются по «? N вопросов»; по завершении и ответах индикатор исчезает, `?glass` уходит из URL.

- [ ] **Step 4: Commit**

```bash
git add webapp/static/editor.js webapp/static/styles.css
git commit -m "feat(glass): фоновая досборка в редакторе — компакт-индикатор и замочек слайда"
```

---

### Task 7: Точечное «Улучшить этот слайд» + удаление rebuild из UI (TDD на сервере)

**Files:**
- Modify: `webapp/glass.py` (после `refill_slide`, конец файла)
- Modify: `webapp/app.py` (после `rebuild_draft`, ~строка 712)
- Modify: `webapp/static/editor.js` (rebuild-обработчик :3216-3243, `initDraftBuilder` :3202, improve-кнопка)
- Test: `tests/test_glass.py`

- [ ] **Step 1: Падающие тесты в `tests/test_glass.py`**

По образцу соседних тестов файла (те же фикстуры создания сессии/плана; фейковый клиент как в существующих тестах step/answer):

```python
def test_improve_slide_gate_and_flow(...):
    # 1) сборка не завершена (есть brief без filled) → NotReady
    with pytest.raises(glass.NotReady):
        glass.improve_slide(sid, 1)
    # 2) после заполнения: слайд вне диапазона → IndexError
    ...заполнить план (filled=True у всех, как делают соседние тесты)...
    with pytest.raises(IndexError):
        glass.improve_slide(sid, 99)
    # 3) чистый слайд, замечаний нет → improved=False, план не тронут
    out = glass.improve_slide(sid, 1, vision=False, client=fake_client)
    assert out["improved"] is False
    # 4) замечания есть (подменяем _qa_notes) → автофикс прошёл, план обновлён
    monkeypatch.setattr(glass, "_improve_notes",
                        lambda *a, **k: ["overflow: текст вылез"])
    out = glass.improve_slide(sid, 1, vision=False, client=fake_client)
    assert out["improved"] is True
    assert out["plan"]["slides"][0]["filled"] is True
```

(Фейковый клиент для автофикса — тот же паттерн, что в тестах `_fill_one`: `chat_json` возвращает валидный контент слота.)

- [ ] **Step 2: Тест падает** — `pytest tests/test_glass.py -x -q` → `AttributeError: improve_slide`.

- [ ] **Step 3: Реализация в glass.py**

```python
class NotReady(RuntimeError):
    """Точечное улучшение доступно только после полного завершения сборки."""


def _improve_notes(plan_one: Any, html: str, *, vision: bool,
                   client: Any, theme: str) -> list[str]:
    """Замечания QA по одному слайду (линт + замер + vision). Вынесено для
    подмены в тестах: полный QA требует Playwright."""
    from htmlslides.pipeline.build import _qa_notes
    notes = _qa_notes(plan_one, html, vision=vision, vision_all=True,
                      client=client, theme=theme, artifacts=None,
                      progress=lambda *_: None)
    return notes.get(1, [])


def improve_slide(session_id: str, index: int, *, vision: bool = True,
                  client: Any | None = None) -> dict:
    """«Улучшить этот слайд»: QA одного слайда и один автофикс — точечная
    замена общедековой кнопки rebuild (спека 2026-08-20). Доступно только
    после полного заполнения аутлайна: до того слайд может перезаполнить сам
    конвейер, и автофикс проиграл бы гонку."""
    from htmlslides.library import TemplateLibrary
    from htmlslides.models import DeckPlan, SlidePlan
    from htmlslides.pipeline.filler import autofix_slide
    from webapp import deck_edit

    with _plan_lock(session_id):
        plan = draft.load_plan(session_id)
        if any(s.brief and not s.filled for s in plan.slides):
            raise NotReady("сборка ещё идёт — улучшение доступно после "
                           "её завершения")
        if not 1 <= index <= len(plan.slides):
            raise IndexError(
                f"слайд {index} вне диапазона (1..{len(plan.slides)})")
        slide = plan.slides[index - 1]
        if slide.freeform:
            raise NoContext("свободный слайд правится в чате — "
                            "автофикс работает по макету")
        old_brief = slide.brief

    library = TemplateLibrary.load()
    deck_html = deck_edit.deck_path(session_id).read_text("utf-8")
    single = deck_edit.extract_slide(deck_html, index)
    if single is None:
        raise IndexError(f"слайда {index} нет в отрендеренной деке")
    tid = slide.template_id or "blank"
    content = dict(slide.fields or slide.content or {})
    sp = SlidePlan(index=1, type=library.get(tid).type, template_id=tid,
                   content=content)
    fixes = _improve_notes(DeckPlan(title=plan.title, slides=[sp]), single,
                           vision=vision, client=client or _kimi(),
                           theme=plan.theme or "dark")
    if not fixes:
        return {"improved": False, "notes": 0, "plan": plan.model_dump()}
    sp = autofix_slide(client or _kimi(), library, sp, fixes,
                       deck_title=plan.title)
    # Вызов модели шёл без замка — вклеиваем только свой слайд в свежий план
    # (splice-into-fresh, как _fill_one).
    with _plan_lock(session_id):
        fresh = draft.load_plan(session_id)
        if (not 1 <= index <= len(fresh.slides)
                or fresh.slides[index - 1].brief != old_brief):
            return {"improved": False, "notes": len(fixes),
                    "plan": fresh.model_dump()}
        fresh = draft.update_slide(fresh, index, content=sp.content,
                                   template_id=sp.template_id or tid)
        s = fresh.slides[index - 1]
        typed = slide_types.typed_from_content(sp.template_id or tid, sp.content)
        s.slide_type, s.fields = typed if typed else (None, None)
        s.filled = True
        draft.save_plan(session_id, fresh)
        draft_render.render_draft(session_id, fresh)
    return {"improved": True, "notes": len(fixes), "plan": fresh.model_dump()}
```

(Проверить фактические импорты в шапке glass.py: `deck_edit` может быть уже импортирован; `slide_types`, `draft_render` там есть — используются в `_fill_one`.)

- [ ] **Step 4: Тесты зелёные** — `pytest tests/test_glass.py -x -q` → PASS.

- [ ] **Step 5: Эндпоинт в app.py** (после `rebuild_draft`; сам rebuild-эндпоинт остаётся для API/бота):

```python
@app.post("/api/drafts/{session_id}/slides/{index}/improve")
async def improve_draft_slide(session_id: str, index: int, request: Request,
                              user=Depends(get_current_user)) -> JSONResponse:
    """Точечное «Улучшить этот слайд»: QA + автофикс одного слайда (~1-2 мин).
    409 — пока сборка не завершена (кнопка в UI в это время выключена)."""
    from webapp import glass
    await _draft_or_404(request, session_id, user, mutate=True)
    try:
        out = await run_in_threadpool(glass.improve_slide, session_id, index)
    except glass.NotReady as exc:
        raise HTTPException(409, str(exc))
    except (IndexError, glass.NoContext) as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 — logged; нейтральная русская ошибка
        logger.exception("improve slide failed (session %s, slide %s)",
                         session_id, index)
        raise HTTPException(500, "не удалось улучшить слайд — "
                                 "попробуйте ещё раз") from exc
    return JSONResponse(out)
```

Эндпоинт-тест в `tests/test_glass.py` или `tests/test_draft.py` (где живут HTTP-тесты драфтов): незавершённая сборка → 409; после завершения с подменённым `_improve_notes` → 200 и `improved` в ответе.

- [ ] **Step 6: Клиент — кнопка вместо rebuild**

В editor.js:
1. Удалить rebuild-блок целиком (строки 3215-3243: `let rebuilding…` и обработчик) и строку `byId("rebuild")?.classList.remove("hidden");` в `initDraftBuilder` (:3203). Функцию `watchRebuild` (:3247) оставить только если на неё есть другие ссылки — проверить `grep -n "watchRebuild" webapp/static/editor.js`; если ссылка одна (из удалённого обработчика) — удалить и её вместе с обработкой `?rebuilt` она не связана (блок `rebuilt` в initModeBadge остаётся: редирект после API-rebuild жив).
2. Добавить (рядом с удалённым блоком):

```js
/* ---- точечное «Улучшить этот слайд» (замена общедековой rebuild-кнопки) ---- */
let improving = false;
function syncImproveButton() {
  const wrap = byId("improveWrap");
  const btn = byId("improveSlide");
  const note = byId("improveNote");
  if (!wrap || !btn) return;
  const s = (draftPlan.slides || [])[current];
  wrap.classList.toggle("hidden", mode !== "manual" || !s);
  if (!s) return;
  const building = (draftPlan.slides || []).some((x) => x.brief && !x.filled);
  btn.disabled = improving || building || !!s.freeform;
  if (note) note.textContent = improving ? "Проверяю и улучшаю…"
    : building ? "Доступно после завершения сборки"
    : s.freeform ? "Свободный слайд правится в чате" : "";
}

byId("improveSlide")?.addEventListener("click", async () => {
  if (improving) return;
  improving = true;
  syncImproveButton();
  try {
    await flushPendingSave();
    const r = await glassFetch(
      U(`/api/drafts/${sessionId}/slides/${current + 1}/improve`),
      { method: "POST" });
    if (!r.ok) {
      let detail = "";
      try { detail = JSON.parse(await r.text()).detail; } catch (e) { detail = ""; }
      throw new Error(detail || "не удалось улучшить слайд");
    }
    const out = await r.json();
    if (out.plan) draftPlan = out.plan;
    builtFormFor = -1;
    loadDeck();
    addMsg?.("bot", out.improved
      ? "Слайд проверен и улучшен."
      : "Слайд проверен — замечаний нет, менять нечего.");
  } catch (e) {
    alertDialog("Не удалось улучшить слайд: " + (e && e.message ? e.message : e));
  } finally {
    improving = false;
    syncImproveButton();
  }
});
```

3. Вызвать `syncImproveButton()` в конце `renderBuilderForm()` (форма перерисовывается при смене слайда) и в `exitGlassMode` (строка из Task 5 Step 6). Если `addMsg` в manual-режиме недоступен (панель чата скрыта) — заменить на `if (note) note.textContent = …` с тем же текстом.

- [ ] **Step 7: Прогон** — `python -m pytest tests/ -x -q` и `node --test tests/js/` → PASS.

- [ ] **Step 8: Commit**

```bash
git add webapp/glass.py webapp/app.py webapp/static/editor.js tests/test_glass.py
git commit -m "feat(editor): точечное «Улучшить этот слайд» вместо общедековой кнопки"
```

---

### Task 8: Стеклянный путь — дефолт веба

**Files:**
- Modify: `webapp/static/index.html` (mode-cards :93-103)
- Modify: `webapp/static/app.js` (:87-97, :597-598)

- [ ] **Step 1: Одна карточка запуска**

Заменить блок `.mode-cards` (строки 90-102, вместе с комментарием) на:

```html
            <!-- Стеклянный путь — единственный для веба (спека 2026-08-20):
                 сборка видна по шагам, чёрный ящик остаётся API и боту. -->
            <div class="mode-cards">
              <button id="createGlass" class="btn mode-card" aria-describedby="createHint" disabled>
                <span class="mode-card__name">Собрать презентацию</span>
                <span class="mode-card__desc">Слайды заполняются по одному у вас на глазах. Там, где макет неочевиден, ИИ спросит &mdash; и править можно прямо по ходу.</span>
              </button>
            </div>
```

- [ ] **Step 2: app.js**

1. Строка 88: `["#create", "#createGlass"]` → `["#createGlass"]`.
2. Строки 597-598 заменить на:

```js
// Стрелка, а не сама функция: onclick передал бы MouseEvent в opts.
// «Точный перенос» — режим движка (пере-вёрстка pptx 1:1): пошаговой сборке
// недоступен, такой запуск идёт прежним чёрным ящиком (createJob).
$("#createGlass").onclick = () =>
  ($("#exactTransfer")?.checked ? createJob() : createGlass());
```

3. `grep -n '"#create"' webapp/static/app.js` — убедиться, что других ссылок на `#create` не осталось (createJob сам остаётся: его зовёт точный перенос).

- [ ] **Step 3: Ручная проверка** — preview главной: одна карточка, выбор файла активирует её, запуск ведёт на `/editor?…&glass=1` с оверлеем; pptx + галочка «Точный перенос» → прежний прогресс чёрного ящика.

- [ ] **Step 4: Commit**

```bash
git add webapp/static/index.html webapp/static/app.js
git commit -m "feat(web): стеклянная сборка — единственный путь загрузки документа в вебе"
```

---

### Task 9: Полная верификация

- [ ] **Step 1: Юнит-прогон**

Run: `python -m pytest tests/ -x -q` и `node --test tests/js/`
Expected: все зелёные (500+ старых + новые).

- [ ] **Step 2: e2e через MCP preview** (`python scripts/devserver.py`; после backend-правок — `preview_stop`+`preview_start`)

1. Главная → один путь → загрузка документа → полноэкранный экран сборки: степпер, прогресс, таймер тикает с первой секунды, лента скелетов с темами.
2. Вопрос ИИ → карточка справа от крупного слайда → ответ → сборка продолжается.
3. «В редактор сейчас» → индикатор досборки, телеметрия, слайды доезжают; заполняемый слайд залочен; «? N вопросов» открывает карточки.
4. Авто-переход: дождаться исчерпания автозаполнения → оверлей уходит сам.
5. Drag миниатюр: порядок меняется без пересборки ленты (Network: только 2-3 лёгких `slide=N` запроса), F5 → порядок жив.
6. «Улучшить этот слайд»: до конца сборки disabled с подсказкой; после — прогон и результат.
7. 3 осечки (обрыв сети devtools) → русская ошибка на оверлее + «Повторить».

- [ ] **Step 3: Финальный коммит хвостов, если были** — только по фактическим правкам.

---

## Вне охвата (из спеки)

Серверный glass-контракт и планировщик; диаграммный движок; drag на собранных деках (`moveSlideBuilt` не трогаем); мобильная вёрстка оверлея сверх текущего адаптива. `POST /api/drafts/{sid}/rebuild` остаётся для API/бота.

## Саморевью плана

- Покрытие спеки: секция 1 → Task 4-5; секция 2 → Task 3, 6, 7; секция 3 → Task 8 (входы), Task 5 Step 5 (ошибки), Task 1-2, 7, 9 (тесты). Плашка «дольше обычного» удаляется в Task 5 Step 3. Общедековая кнопка уходит в Task 4/7, эндпоинт остаётся.
- Типы согласованы: `glassCurrentTarget` → `{index, label}` используется единообразно (телеметрия, лента, замочек, метки); `glassMiniText` принимает `{working, filled, total, line, open}`; `improve_slide` возвращает `{improved, notes, plan}` — клиент читает `out.improved`/`out.plan`.
- Известные допущения, проверяемые по месту: имена CSS-токенов (фолбэки стоят), точное место вставки гарда в `renderBuilderForm`, наличие `addMsg` в manual-режиме, единственность ссылки на `watchRebuild`.
