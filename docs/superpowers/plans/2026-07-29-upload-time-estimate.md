# Оценка времени сборки при загрузке — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** У каждой карточки сборки в очереди/прогрессе показывать «N разделов · примерно X мин»; для >20 разделов — предупреждающий тон.

**Architecture:** Сервер считает разделы в `/api/jobs` реальным парсером (`htmlslides.parsers.base.parse_file`), кладёт в in-memory meta раннера и отдаёт в `POST /api/jobs` и `GET /api/jobs/active` (переживает перезагрузку страницы; БД не нужна — активные джобы живут только в памяти раннера). Клиент считает минуты чистой функцией `estimateLine()` в UMD-модуле `errtext.js` (тестируется `node --test`) и рисует сегмент в meta-строке карточки.

**Tech Stack:** FastAPI + pytest (tests/test_app.py, TestClient), vanilla JS + node:test.

---

### Task 1: JS — чистая функция estimateLine

**Files:**
- Modify: `webapp/static/errtext.js` (добавить функцию + экспорт)
- Test: `tests/js/errtext.test.js`

- [ ] **Step 1: Write failing tests** — добавить в конец `tests/js/errtext.test.js` (импорт вверху дополнить `estimateLine`):

```js
test("estimateLine: null/0 → null", () => {
  assert.strictEqual(estimateLine(null, "a.md"), null);
  assert.strictEqual(estimateLine(0, "a.md"), null);
});

test("estimateLine: мелкий док — нейтрально, минимум 2 мин", () => {
  assert.deepStrictEqual(estimateLine(1, "a.md"),
    { text: "1 раздел · примерно 2 мин", warn: false });
  assert.deepStrictEqual(estimateLine(10, "d.docx"),
    { text: "10 разделов · примерно 5 мин", warn: false });
});

test("estimateLine: .pptx считает слайдами", () => {
  assert.deepStrictEqual(estimateLine(12, "deck.PPTX"),
    { text: "12 слайдов · примерно 6 мин", warn: false });
});

test("estimateLine: >20 — предупреждающий тон с припиской", () => {
  assert.deepStrictEqual(estimateLine(40, "big.md"),
    { text: "крупный документ: 40 разделов · примерно 20 мин", warn: true });
});

test("estimateLine: >100 — кап и honest-минуты по первым 100", () => {
  assert.deepStrictEqual(estimateLine(120, "huge.md"),
    { text: "крупный документ: 120 разделов · примерно 50 мин, соберём первые 100",
      warn: true });
});
```

- [ ] **Step 2: Run** `node --test tests/js/errtext.test.js` — Expected: FAIL (`estimateLine is not a function`).

- [ ] **Step 3: Implement** — в `webapp/static/errtext.js` перед блоком `root.SAVE_STATUS = ...`:

```js
  // Оценка времени сборки по числу разделов исходника (~30 с на слайд, замер
  // 2026-07: 40 слайдов ≈ 20 мин). Возвращает {text, warn} или null.
  // >20 разделов — предупреждающий тон; >100 — движок соберёт первые 100.
  function estimateLine(count, filename) {
    if (count == null || count <= 0) return null;
    var pptx = /\.pptx$/i.test(filename || "");
    var unit = pptx ? plural(count, "слайд", "слайда", "слайдов")
                    : plural(count, "раздел", "раздела", "разделов");
    var minutes = Math.max(2, Math.round(Math.min(count, 100) * 0.5));
    var text = count + " " + unit + " · примерно " + minutes + " мин";
    if (count > 100) text += ", соберём первые 100";
    var warn = count > 20;
    if (warn) text = "крупный документ: " + text;
    return { text: text, warn: warn };
  }
```

плюс `root.estimateLine = estimateLine;` и `estimateLine: estimateLine` в `module.exports`.

- [ ] **Step 4: Run** `node --test tests/js/errtext.test.js` — Expected: PASS (13 tests).

- [ ] **Step 5: Commit** `git add webapp/static/errtext.js tests/js/errtext.test.js && git commit -m "feat(ui): estimateLine — оценка минут сборки по числу разделов"`

### Task 2: Сервер — подсчёт разделов и прокидывание в API

**Files:**
- Modify: `webapp/runner.py:116-136` (active_jobs), `webapp/runner.py:255-281` (start)
- Modify: `webapp/app.py:210-259` (create_job)
- Modify: `docs/superpowers/specs/2026-07-29-upload-time-estimate-design.md` (снять колонку БД)
- Test: `tests/test_app.py`, `tests/test_runner.py`

- [ ] **Step 1: Write failing tests.** В `tests/test_app.py` (по образцу существующих job-тестов с их стабом раннера — проверить, что стаб `start` принимает `**kw`):

```python
def test_create_job_reports_section_count(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    md = "# Дека\n\n## Раз\n\nтекст\n\n## Два\n\nтекст\n\n## Три\n\nтекст\n"
    r = client.post("/api/jobs", data={"mode": "htmlnew"},
                    files={"file": ("doc.md", md.encode(), "text/markdown")},
                    headers=H())
    assert r.status_code == 200
    assert r.json()["section_count"] == 3


def test_create_job_parser_crash_still_starts(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    import webapp.app as appmod
    def boom(path):  # noqa: ARG001
        raise RuntimeError("parser died")
    monkeypatch.setattr("htmlslides.parsers.base.parse_file", boom)
    r = client.post("/api/jobs", data={"mode": "htmlnew"},
                    files={"file": ("doc.md", b"# t\n\n## a\n\nx\n", "text/markdown")},
                    headers=H())
    assert r.status_code == 200
    assert r.json()["section_count"] is None
```

В `tests/test_runner.py` — active_jobs несёт section_count (по образцу существующего active_jobs-теста):

```python
def test_active_jobs_carries_section_count(...):
    # runner.start(inp, user_id=1, section_count=7)
    # assert runner.active_jobs()[0]["section_count"] == 7
```

- [ ] **Step 2: Run** `python -m pytest tests/test_app.py -k section_count -x -q` — Expected: FAIL (KeyError/None).

- [ ] **Step 3: Implement runner.** В `start()`: сигнатура `def start(self, inp: Any, *, user_id: int | None = None, section_count: int | None = None)`; в `self._meta[session_id] = {...}` добавить `"section_count": section_count`. В `active_jobs()` в out.append добавить `"section_count": meta.get("section_count"),`.

- [ ] **Step 4: Implement app.py.** В `create_job` после `inp = inp.model_copy(...)` (строка ~247):

```python
    # Оценка размера для фронта: считаем разделы тем же парсером, что и сборка
    # (двойной парсинг — миллисекунды на фоне LLM-этапов). Ошибка парсинга НЕ
    # блокирует запуск — реальную причину покажет воркер со своей диагностикой.
    try:
        from htmlslides.parsers import base as _parsers_base
        doc = await run_in_threadpool(_parsers_base.parse_file, dest)
        section_count: int | None = len(doc.sections)
    except Exception:  # noqa: BLE001
        section_count = None
```

`runner.start(inp, user_id=user.id, section_count=section_count)`; ответ — `{"session_id": ..., "kind": kind, "section_count": section_count}`. (`run_in_threadpool` уже импортирован в app.py.)

Примечание: monkeypatch в тесте патчит `htmlslides.parsers.base.parse_file`, поэтому в app.py импортировать модуль и звать атрибут (`_parsers_base.parse_file`), не `from ... import parse_file`.

- [ ] **Step 5: Run** `python -m pytest tests/test_app.py tests/test_runner.py -q` — Expected: PASS (все, включая старые).

- [ ] **Step 6: Update spec** — в разделе «Сервер» заменить пункт про колонку БД: активные джобы живут только в памяти раннера (страница переживает reload через /api/jobs/active; рестарт процесса убивает джобу целиком), поэтому счётчик хранится в runner meta, БД не трогаем.

- [ ] **Step 7: Commit** `git add webapp/runner.py webapp/app.py tests/test_app.py tests/test_runner.py docs/superpowers/specs/2026-07-29-upload-time-estimate-design.md && git commit -m "feat(api): section_count в /api/jobs и /api/jobs/active"`

### Task 3: Клиент — сегмент оценки в карточке

**Files:**
- Modify: `webapp/static/index.html:146` (подключить errtext.js перед app.js)
- Modify: `webapp/static/app.js:172-180` (normActive), `:226-247` (cardMeta)
- Modify: `webapp/static/styles.css` (класс .est-warn)

- [ ] **Step 1: index.html** — перед `<script src="/static/app.js"></script>` добавить `<script src="/static/errtext.js"></script>` (шелл сам добавит кэш-бастер `?v=mtime`).

- [ ] **Step 2: app.js normActive** — добавить `section_count: a.section_count ?? null,` в возвращаемый объект.

- [ ] **Step 3: app.js cardMeta** — хелпер рядом с cardMeta:

```js
// Сегмент «N разделов · примерно X мин» для активной карточки. estimateLine —
// из errtext.js (window); guard на случай, если модуль не загрузился.
function estSegment(it) {
  if (typeof estimateLine !== "function") return "";
  const e = estimateLine(it.section_count, it.source_filename);
  if (!e) return "";
  return e.warn ? `<span class="est-warn">${esc(e.text)}</span>` : esc(e.text);
}
```

В `cardMeta`: для running — `const seg = [toolTitle(it.mode), esc(detail), pct + "%"]; const est = estSegment(it); if (est) seg.push(est); return seg.join(SEP);` для queued — аналогично на базе `[toolTitle(it.mode), "скоро начнётся"]`.

- [ ] **Step 4: styles.css** — рядом со стилями .pcard-meta (тон сверить с палитрой файла):

```css
.pcard-meta .est-warn { color: var(--warn, #b45309); font-weight: 600; }
```

- [ ] **Step 5: Run** `node --test tests/js/errtext.test.js` и `python -m pytest tests/test_app.py -q` — Expected: PASS.

- [ ] **Step 6: Commit** `git add webapp/static/index.html webapp/static/app.js webapp/static/styles.css && git commit -m "feat(ui): оценка времени сборки в карточке очереди/прогресса"`

### Task 4: Полный прогон + e2e в превью

- [ ] **Step 1:** `python -m pytest -q` — Expected: все зелёные (было 393).
- [ ] **Step 2:** dev-превью (scripts/devserver.py): загрузить md на 3 раздела и убедиться, что в карточке «3 раздела · примерно 2 мин»; загрузить крупный md (>20 разделов) — оранжевый «крупный документ: …». Проверить консоль на ошибки.
- [ ] **Step 3:** Commit при необходимости правок.

### Task 5: Деплой + прод-смоук + дневник

- [ ] **Step 1:** `git push`, на VM `cd /opt/app2 && sudo git fetch origin && sudo git merge --ff-only origin/main`; перед restart проверить очередь пустая (`select count(*) from jobs where status in ('queued','running')` через `.venv/bin/python`), `sudo systemctl restart app2.service`.
- [ ] **Step 2:** Смоук: страница отдаёт `errtext.js` в шелле; POST тестовой md-джобы → ответ несёт `section_count`; `/api/jobs/active` несёт поле; отменить тестовую джобу; журнал без ошибок.
- [ ] **Step 3:** Обновить COORDINATION-док (строка статуса + дневник).

## Self-Review

- Spec coverage: подсчёт (Task 2), API-поля (Task 2), формула/тексты/пороги/кап (Task 1), рендер+тон (Task 3), тесты (Tasks 1–2), «не делаем» — соблюдено; отклонение от спеки (без колонки БД) фиксируется в Step 6 Task 2.
- Placeholders: нет.
- Types: `section_count: int | None` сервер ↔ `a.section_count ?? null` клиент ↔ `estimateLine(count, filename)` — согласовано.
