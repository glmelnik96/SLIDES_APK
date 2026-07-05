# Spec — Живой аутлайн + кнопка «Собрать деку» в чат-конструкторе

**Дата:** 2026-07-06
**Область:** App2 «Slides» (`/slides`), чат-конструктор черновиков (feature 3).
**Тип:** П2 (отдельный лёгкий аутлайн) + М-a (расширить `DraftSlide`, без новой сущности).

## Проблема
Из скриншотов: чат-агент ненадёжно распознаёт намерение «собрать» среди коротких «да / иди / ок» → флоу буксует. Плюс концептуально: сейчас каждый `add` сразу жарит полный слайд (`fill_slide`), отдельной стадии «согласовать план до сборки» нет.

## Решение (целевой UX)
1. В чате пользователь обсуждает деку. Агент копит **лёгкий аутлайн** — слайды с темой (`brief`), без начинки.
2. Рядом с полем чата — **живая панель аутлайна**, обновляется на каждом ходе.
3. Постоянная кнопка **«Собрать деку»** — основной триггер сборки: прогоняет весь аутлайн через `fill_slide` разом. Текст-триггеры («собирай», «поехали») — вторичный дубль, классификатор больше не несущий.
4. После сборки — обычные правки по готовой деке (существующий флоу). Опционально «Собрать через движок» (`/rebuild`) — тяжёлый QA-полиш (не трогаем).

---

## Контракт данных (source of truth для обоих агентов)

### `DraftSlide` (webapp/draft.py) — +2 поля
```python
class DraftSlide(BaseModel):
    template_id: str | None = None
    freeform: bool = False
    content: dict = Field(default_factory=dict)
    brief: str = ""        # NEW: тема слайда в аутлайне (до сборки)
    filled: bool = False   # NEW: прогнан ли через fill_slide
```
Дефолты безопасны для старых `plan.json`: старые слайды получают `brief=""` → сборка их не трогает (см. цель сборки). Миграция не нужна.

### `AgentResult` (webapp/chat_agent.py) — +1 поле
```python
class AgentResult(BaseModel):
    reply: str
    changed: bool = False
    go_to: int | None = None
    build: bool = False    # NEW: фронт должен запустить сборку (POST .../build)
```

### `Intent` — +действие `build_now`
`_INTENT_SYSTEM` дополнить: `build_now` — пользователь просит собрать/приступить к сборке всей деки.

---

## Endpoint (webapp/app.py)

### `POST /api/drafts/{session_id}/build`
- Guard: `_draft_or_404(request, session_id, user, mutate=True)`.
- Цель сборки: слайды с `brief and not filled and not freeform`.
- Если целей нет → `HTTPException(400, "нечего собирать — аутлайн пуст")`.
- `await run_in_threadpool(chat_agent.build_outline, session_id)`.
- После — план уже сохранён и отрендерен внутри `build_outline`; вернуть `JSONResponse(draft.load_plan(session_id).model_dump())`.
- Синхронно (fill ≈ секунды/слайд).

---

## Агент (webapp/chat_agent.py)

### `classify` / `_INTENT_SYSTEM`
Добавить действие `build_now`. Остальные без изменений.

### `run_turn`
- **`add`**: НЕ жарит слайд. `plan = draft.add_slide(plan, DraftSlide(brief=intent.topic or message, filled=False))`; `save_plan`; reply «Добавил в план: {topic}.», `changed=True`, `go_to=len(slides)`. Убрать из ветки `add` вызовы `_pick_template`/`fill_slide` (переезжают в `build_outline`).
- **`plan`** (ядро П2): сгенерировать структурный аутлайн через `client.chat_json` — список `{title, brief}` (Pydantic `OutlineDraft`), **дописать** их как незаполненные `DraftSlide(brief=..., filled=False)` (заголовок кладём в `content={"title": ...}` чтобы панель/дигест показывали название), `save_plan`; reply — краткий текст аутлайна; `changed=True`. Если генерация упала — fallback на старое текстовое обсуждение (`_talk(_PLAN_SYSTEM…)`), `changed=False`.
- **`build_now`**: НЕ жарит синхронно (может быть много слайдов). Вернуть `AgentResult(reply="Собираю деку…", build=True)`. Реальную сборку делает фронт через endpoint.
- `rewrite/delete/move/retitle/chat` — без изменений.

### `build_outline(session_id, *, client=None)` — NEW
```
client = client or _kimi()
library = TemplateLibrary.load()
plan = draft.load_plan(session_id)
for i, s in enumerate(plan.slides, start=1):
    if not (s.brief and not s.filled and not s.freeform):
        continue
    tid = _pick_template(client, library, s.brief, ctx=context_brief(plan, load_chat(session_id), i))
    spec = library.get(tid)
    sp = SlidePlan(index=i, type=spec.type, template_id=tid, content={"brief": s.brief})
    try:
        sp = fill_slide(client, library, sp, deck_title=plan.title)
        plan = draft.update_slide(plan, i, content=sp.content, template_id=tid)
    except Exception:
        plan = draft.update_slide(plan, i, template_id=tid)  # оставить пустой, не падать
    plan.slides[i-1].filled = True
    draft.save_plan(session_id, plan)          # save после КАЖДОГО (устойчивость)
draft_render.render_draft(session_id, plan)    # финальный рендер
```
Примечание: `draft.update_slide` уже принимает `content`/`template_id`; поле `filled` ставим прямым присваиванием (как `freeform` в app.py:353). Импорт `draft_render` внутри функции, чтобы не тянуть цикл импорта.

### `_slide_digest`
Для слайда без title показывать `brief` (чтобы контекст агента отражал аутлайн).

---

## Фронт

### webapp/static/editor.html
- В `<aside class="chat">` добавить блок аутлайна над `#chatLog`:
  ```html
  <div class="outline hidden" id="outline">
    <div class="outline-head">План деки</div>
    <ol class="outline-list" id="outlineList"></ol>
    <button class="btn btn-accent hidden" id="buildDeck">Собрать деку</button>
  </div>
  ```
- Стили в styles.css — минимальные (список + бейджи «в плане»/«готов»).

### webapp/static/editor.js
- `renderOutline()`: из `draftPlan.slides` → `<li>№ · (content.title||brief) · бейдж(filled?"готов":"в плане")</li>`. Вызывать после каждого `fetchPlan()` в chat-режиме.
- `setupChatMode()`: показать `#outline` и `#buildDeck`, обновить копирайт панели.
- `doBuild()`: guard (есть незаполненные слайды) → показать оверлей «Собираю деку…» → `POST /api/drafts/{id}/build` → `fetchPlan()`+`renderOutline()`+`loadDeck()` → скрыть оверлей. Ошибки — в чат/alert.
- `#buildDeck` onclick → `doBuild()`.
- **Текст-триггеры (вариант B):** в `sendAgent()` перед fetch — regex по `message`:
  `/\b(собери|собирай|соберём|приступ|поехали|погнали|go|build)\b/i` или «готово, собирай» → вместо `/agent` вызвать `doBuild()` и выйти.
- Honor LLM-путь: если ответ `/agent` содержит `res.build === true` → вызвать `doBuild()`.
- После `add`/`plan` (`res.changed`) → `renderOutline()` (в дополнение к текущим `fetchPlan`+`loadDeck`).

---

## Что фиксируем по умолчанию
- Прогресс сборки — индетерминантный оверлей «Собираю деку…» (синхронный endpoint). Пер-слайдовый SSE-прогресс — отдельная итерация.
- Именование: новая кнопка «Собрать деку» (в панели чата), существующая «Собрать через движок» (тулбар) — не трогаем.

## Границы
Только App2. Не трогаем gateway/Caddy/навигацию/egress. Личность — из заголовков шлюза.

## Тест-план (в конце — реальный браузер + чат через MCP)
1. Поднять App2 локально (preview), открыть `/slides`, создать чат-черновик.
2. «сделай презентацию про X» → панель наполнилась аутлайном, дека — плейсхолдеры.
3. «добавь слайд про Y», «удали слайд», «переставь» → аутлайн обновляется, `fill_slide` не зовётся (быстро).
4. Кнопка «Собрать деку» → слайды заполняются, бейджи → «готов», превью реальное.
5. Текст-триггер «собирай» в чате → тот же build.
6. Скриншоты до/после, проверка console/network на ошибки.
