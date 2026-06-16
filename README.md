# Slides App (браузерный интерфейс, без Telegram)

Самодостаточное приложение для одного пользователя: генерация презентаций на
моделях Cloud.ru через браузер. Движок (LangGraph-пайплайн, LLM-роли, рендереры,
пакет `htmlslides`, бренд-шаблон и шрифты) **вендорен внутрь репозитория** — внешние
проекты не нужны.

## Что внутри

```
webapp/        — FastAPI-приложение (UI, очередь, прогресс, редактор, чат)
graph/         — LangGraph-пайплайн (verstai / design)
llm/           — клиент Cloud.ru FM + роли/промпты
worker/        — обёртки запуска пайплайна и прогресс
htmlslides/    — генератор HTML-деки (режим htmlnew, Kimi)
renderers/     — рендереры
schemas/       — Pydantic-модели
skill_assets/  — бренд-шаблон Cloud.ru (.pptx)
bot/           — только config.py + logging_setup.py (без Telegram)
```

## Установка

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env   # вписать CLOUDRU_API_KEY
```

## Запуск

```bash
python -m webapp          # или ./start.sh (Mac) / start.bat (Windows)
```

Открыть http://127.0.0.1:8000

## Режимы

- **Ребрендинг PPTX по шаблону** — вход .pptx → .pptx.
- **Генерация PPTX без шаблона** — вход .pptx → .pptx.
- **HTML-презентация** — вход md/txt/docx/pptx → HTML-дека; текст редактируется
  в браузере, есть чат-правки по слайдам, экспорт в PNG (ZIP) или HTML.

## Очередь

До 5 сборок в системе (1 выполняется, до 4 ждут). Параллельно не запускаются
намеренно: узкое место — общий лимит RPS аккаунта Cloud.ru, а не CPU.

## Тесты

```bash
pytest                    # без сети
```
