# Браузерное приложение для слайдов (без Telegram) — дизайн

**Дата:** 2026-06-16
**Статус:** утверждён, готов к написанию плана
**Размещение:** **отдельное приложение** в `SLIDES_APK` (`C:\Users\Глеб\Documents\SLIDES_APK`).
Движок переиспользуется из соседних проектов `Slides_bot` и `HTML_Slides_Skill` через импорт,
без копирования кода и без изменений в них.

## 1. Цель и контекст

Сейчас единственная точка входа в движок генерации презентаций — Telegram-бот
(`Slides_bot`). Нужно отдельное **браузерное приложение** для **одного пользователя
на localhost**: пользователь открывает localhost и проходит все сценарии через
браузерный интерфейс, без привязки к Telegram.

Движок (LangGraph-пайплайн, LLM-роли Cloud.ru, рендереры, пакет `htmlslides`)
переиспользуется как зависимость. `Slides_bot` и `HTML_Slides_Skill` не трогаются;
Telegram-бот продолжает работать параллельно.

### Ключевое наблюдение

Telegram в `Slides_bot` — тонкий слой ввода/вывода. Реальная работа от него не зависит:
`scripts/live_run.py` уже запускает весь пайплайн без Telegram и без Celery,
перехватывая прогресс через `worker.progress.publish`. Браузерное приложение — это
новый адаптер ввода/вывода поверх того же движка.

## 2. Решения (зафиксированы в брейнсторминге)

| Тема | Решение |
|---|---|
| Размещение | Отдельный проект в `SLIDES_APK`. Движок — зависимость (`slides_bot`, `htmlslides`) |
| Охват режимов | 3: `verstai`, `design`, `htmlnew`. `html`, `audit`, `brief` — вне scope |
| Переименование (только UI) | `verstai` → «Ребрендинг PPTX по шаблону»; `design` → «Генерация PPTX без шаблона»; `htmlnew` → «HTML-презентация». Внутренние id режимов (`Mode.*`) не меняются |
| Архитектура | Один процесс FastAPI. Без Redis, Celery, MinIO/S3, Docker |
| API-ключ и конфиг | Только из `.env` приложения (`CLOUDRU_API_KEY`, опц. `CLOUDRU_BASE_URL`). В браузере ничего не вводится |
| Редактирование текста | Только для HTML-презентации. PPTX-режимы отдают `.pptx` как есть |
| Экспорт | PPTX-режимы → `.pptx`. HTML-режим → PNG всех слайдов (ZIP) **или** сама HTML-дека |
| Разрешение PNG | Фиксированное 1920×1080 |
| Раскладка редактора | Вариант A: крупный слайд по центру + лента миниатюр слева + навигация ◀▶ |
| Механизм редактирования | Вариант 1: `deck.html` в `<iframe>`, `contenteditable` на тексте, PNG рендерится на сервере |
| История | Вариант B: список последних ~10 сборок на диске + кнопка «Очистить» |
| Платформы | Mac и Windows. Пути через `pathlib`, без хардкода |
| Аутентификация | Нет (single-user localhost) |

## 3. Зависимость от движка и конфиг

Приложение импортирует из `Slides_bot`:
`graph.graph` (`_build_graph`), `graph.designer.graph` (`build_designer_graph`),
`worker.tasks.htmlnew` (`run_htmlnew`), `worker.progress`, `schemas.session`
(`Mode`, `SessionInput`, `SessionState`, `ProgressEvent`, `Stage`), и из
`HTML_Slides_Skill` — пакет `htmlslides` (включая `pipeline.screenshot`).

**Установка зависимостей** (документируется в README приложения):
`slides_bot` и `htmlslides` ставятся editable/path-инсталляцией
(`pip install -e ../Slides_bot`, `pip install -e ../HTML_Slides_Skill/html-slides-skill`)
либо через path-зависимости в `pyproject.toml` приложения. Также `playwright` +
`playwright install chromium`.

**Шим конфига.** Движок читает настройки через `bot.config.get_settings()`
(`Slides_bot/bot/config.py`), где `TELEGRAM_BOT_TOKEN` — обязательное поле. Приложение
**до первого импорта движка** проставляет в окружение плейсхолдеры для обязательных,
но неиспользуемых полей (`TELEGRAM_BOT_TOKEN=unused`), чтобы `Settings()` валидировался.
От пользователя в `.env` требуется только `CLOUDRU_API_KEY` (и опц. `CLOUDRU_BASE_URL`).
`extra="ignore"` в Settings гарантирует, что лишние ключи не мешают.

## 4. Архитектура

Запуск: `python -m webapp` (или `uvicorn webapp.app:app`). Обёртки: `start.sh` (Mac),
`start.bat` (Win).

**Стек:** FastAPI + WebSocket (прогресс), статика HTML/CSS/JS без сборочного тулчейна.

**Запуск пайплайна.** Каждая генерация выполняется в фоновом потоке
(`asyncio.run_in_executor` / threadpool), как `live_run.py`:
- графы компилируются «сырыми» билдерами **без чекпоинтера**:
  - `verstai` → `graph.graph._build_graph().compile()`
  - `design` → `graph.designer.graph.build_designer_graph().compile()`
  - `htmlnew` → прямой вызов `worker.tasks.htmlnew.run_htmlnew` (не LangGraph)
- это устраняет зависимость от Redis (`get_compiled_*` используют `RedisSaver` — не вызываются).

**Мост прогресса.** `worker.progress.publish` монкипатчится на функцию, которая
прокидывает `ProgressEvent` из рабочего потока в asyncio-цикл
(`asyncio.run_coroutine_threadsafe`, ссылка на loop сохраняется при старте) →
в WebSocket активной сессии. Формат — `schemas.session.ProgressEvent`.
`publish` — модульная глобаль, поэтому в первой версии допускается одна активная
сборка за раз (для single-user достаточно); параллельные запросы сериализуются.

**Файлы.** Вход, `deck.html`, `result.pptx`, PNG лежат в рабочей папке сессии
(`SLIDESBOT_WORKDIR`, по умолчанию `tempfile.gettempdir()/slidesapp/sessions`).
Все пути — `pathlib`, кросс-платформенно.

### Раскладка проекта (`SLIDES_APK`)

```
webapp/
  __main__.py        — uvicorn-запуск
  app.py             — FastAPI: маршруты, монтирование статики, WS-эндпоинт
  config_shim.py     — проставляет плейсхолдеры env ДО импорта движка
  runner.py          — запуск пайплайна в потоке + установка моста прогресса
  pipeline_bridge.py — выбор и компиляция графа по режиму (без чекпоинтера)
  history.py         — JSON-индекс сборок на диске: список / открыть / очистить
  render_png.py      — обёртка над htmlslides.pipeline.screenshot: PNG → ZIP
  deck_edit.py       — приём отредактированного HTML, перезапись deck.html
  static/
    index.html       — главный экран (выбор режима, загрузка, история)
    editor.html      — редактор HTML-деки
    app.js           — клиент: WS-прогресс, contenteditable, навигация, экспорт
    styles.css
pyproject.toml       — зависимости (fastapi, uvicorn, + path: slides_bot, htmlslides)
.env.example         — CLOUDRU_API_KEY, опц. CLOUDRU_BASE_URL
start.sh             — запуск на Mac
start.bat            — запуск на Windows
README.md            — установка зависимостей и запуск
```

## 5. Пользовательский поток и экраны

1. **Главный экран** (`index.html`):
   - выбор режима — 3 карточки с UI-именами;
   - drag-and-drop загрузка файла: `verstai`/`design` → `.pptx`; `htmlnew` → md/txt/docx/pptx;
   - кнопка «Создать»;
   - блок **История**: последние ~10 сборок (режим, имя, дата, действия) + «Очистить».
2. **Экран прогресса:** WebSocket-стрим стадий и процентов (parse → … → done).
3. **Результат:**
   - **PPTX-режимы** → карточка «Готово» + кнопка **Скачать .pptx**.
   - **HTML-режим** → **редактор** (раскладка A). Клик по тексту → правка на месте.
     Кнопки: **Сохранить**, **Скачать PNG (ZIP)**, **Скачать HTML**.

## 6. Редактор и экспорт (Вариант 1)

**Структура деки** (из `htmlslides.assembler`): один self-contained HTML —
`<div class="deck-stage">` с секциями `<section class="slide">` (1920×1080),
инлайн CSS + JS-движок (`window.deck.goTo(i)`).

**Редактирование:** `deck.html` грузится в `<iframe>`. `app.js`:
- включает `contenteditable` на текстовых узлах внутри `.slide`;
- лента миниатюр + ◀▶ управляют через `window.deck.goTo()`;
- «Сохранить» → POST `outerHTML` документа iframe на сервер.

**Сервер (`deck_edit.py`):** принимает HTML, перезаписывает `deck.html` сессии.
Доверенный локальный ввод от того же пользователя; запись только в папку сессии.

**Экспорт PNG (`render_png.py`):** по сохранённой `deck.html` запускает
`htmlslides.pipeline.screenshot.screenshot_slides` (Playwright, viewport 1920×1080)
для всех слайдов → PNG → ZIP (`slide-01.png`, …).

**Экспорт HTML:** отдать `deck.html` напрямую.

## 7. История (Вариант B)

JSON-индекс на диске (в рабочей папке, напр. `sessions/history.json`). Запись:
`{id, mode, source_filename, created_at, result_path, kind: pptx|html}`.
Эндпоинты:
- `GET /api/history` — список (последние ~10, новые сверху);
- открыть запись — вернуться к редактору (html) или скачать (pptx);
- `POST /api/history/clear` — стереть индекс и связанные файлы сессий.

## 8. API (эскиз)

- `GET /` — главный экран.
- `POST /api/jobs` — multipart: режим + файл. Сохраняет вход, стартует поток, отдаёт `session_id`.
- `WS /ws/{session_id}` — стрим `ProgressEvent` до терминального события.
- `GET /api/jobs/{id}/result` — скачать `.pptx` (PPTX-режимы).
- `GET /editor?session={id}` — страница редактора.
- `GET /api/jobs/{id}/deck` — сырой `deck.html` для `<iframe>` (inline). С `?download=1` — отдать как файл (Content-Disposition attachment).
- `POST /api/jobs/{id}/deck` — сохранить отредактированный HTML.
- `GET /api/jobs/{id}/png.zip` — ZIP с PNG всех слайдов.
- `GET /api/history`, `POST /api/history/clear`.

## 9. Обработка ошибок

- Сбой пайплайна → терминальное `failed` в WS с текстом; экран показывает ошибку и «Начать заново».
- Нет `CLOUDRU_API_KEY` → понятное сообщение на старте сервера.
- Playwright/Chromium не установлен → подсказка `playwright install chromium` при экспорте PNG.
- Неверный тип файла для режима → отказ с сообщением до запуска.

## 10. Тестирование

- **Юнит (без сети):** выбор графа по режиму; мост прогресса (`ProgressEvent` → WS-сообщение);
  маппинг истории (добавление/чтение/очистка); перезапись `deck.html`; шим конфига.
- **Интеграция (лёгкая):** прогон `htmlnew` на маленьком вводе → проверка появления
  `deck.html`, затем `render_png` → непустой ZIP. Playwright-зависимое пропускается,
  если Chromium недоступен.
- **Живые прогоны Cloud.ru** — опционально (по аналогии с маркером `slow` в `Slides_bot`).

## 11. Вне scope

Аутентификация; мультипользовательность; `/resume` и чекпоинты; режимы `html`, `audit`,
`brief`; редактирование PPTX в браузере; выбор разрешения PNG; деплой за пределы localhost;
любые изменения в `Slides_bot` и `HTML_Slides_Skill`.
