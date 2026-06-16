# Slides App (браузерный интерфейс, без Telegram)

Отдельное приложение для одного пользователя. Переиспользует движок из
`../Slides_bot` и `../HTML_Slides_Skill`.

## Установка

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ../Slides_bot
pip install -e ../HTML_Slides_Skill/html-slides-skill
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
  в браузере, экспорт в PNG (ZIP) или HTML.

## Тесты

```bash
pytest                    # без сети
```
