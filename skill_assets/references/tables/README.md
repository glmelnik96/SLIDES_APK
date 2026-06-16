# Tables — эталонные примеры

Референсные PNG-рендеры для `slide_type: table_native` (v1.8+).

## Файлы

| Файл | Описание |
|---|---|
| `slide56_zebra.png` | **Canonical эталон** — slide 56 шаблона Cloud.ru. Zebra style: header row без заливки + bold, body rows чередуются #F2F2F2/белый. Vertical separators 0.5pt #C8C8C8 только между колонками. Без горизонтальных границ. |

## Canonical критерии PASS

- Header row: **без заливки**, bold 12pt SB Sans Display #222222
- Body rows: чередуются **#F2F2F2** / белый
- Текст в ячейках: **left + top** alignment (нерушимо)
- Поля ячеек: L/R **12px**, T/B **8px**
- Шрифт ячеек: SB Sans Display, **11pt** body, **12pt** header
- Vertical separators: 0.5pt **#C8C8C8** (только справа от ячеек, кроме последней)
- Нет горизонтальных границ — зебра-фон создаёт визуальное разделение
- Первая колонка может быть 1.4× шире (для row labels) — опц. через `first_col_wider: true`

## Когда использовать table_native

- ≥3 колонок × ≥3 строк данных
- Явная шапка (header row)
- **БЕЗ merged cells** (если есть → anti-distortion stop+ask, см. feedback)
- intent: comparison / pricing / spec / matrix / характеристики

## Когда НЕ использовать (anti-distortion)

Перейти на `flow_diagram_native` ИЛИ задать вопрос пользователю:
- Merged cells (rowspan / colspan)
- Irregular grid (разное число ячеек в строках)
- Multi-header rows (overheader + sub-header)
- Color-coded cells (критичность через цвет)
- Nested tables
- RACI / roadmap timeline в табличной форме
