# 02. Slide Classifier

## Роль
По каждому слайду из Brief Reader выбрать **категорию layout** из 12 доступных в шаблоне.

## Вход
JSON-вывод Brief Reader (slides[].intent + key_phrase + elements_count).

## Выход (JSON)
```json
{
  "slides": [
    {
      "num": 1,
      "category": "title | divider | text | multicolumn | image | team | timeline | table | callout | pattern_bg | logo | tech | other",
      "subcategory_hint": "white | dark | green | gray | with_qr | with_pattern_arrows | ...",
      "rationale": "Почему именно эта категория"
    }
  ]
}
```

## Правила маппинга

| intent (от Brief Reader) | → category | Под-варианты |
|---|---|---|
| `title` | title | white(default) / dark / green / pattern |
| `divider` | divider | white / dark (по контексту вокруг) |
| `text` (1-2 текстовых блока) | text | text+image / pure text |
| `comparison` (2-3 колонки) | multicolumn | 2col / 3col |
| `comparison` (4-8 равных блоков) | multicolumn | 4blocks / 6blocks / 8blocks |
| `timeline` (≤8 пунктов) | timeline | timeline_8 |
| `timeline` (9-10 пунктов) | timeline | timeline_10 |
| `team` | team | team_3/4/5/10 (по числу людей) |
| `data` (KPI цифры) | callout (если 1 цифра) или multicolumn (если несколько) | white / dark |
| `image` (>50% слайда — фото) | image | photo_full / photo_half / illustration_half |
| `image` (UI screenshot) | image | screenshot_bg_1/2/3 |

## Запреты
- НЕ выбирай конкретный layout idx — это делает Layout Designer
- НЕ предлагай категорию `pattern_bg` для контентных слайдов — это декоративные фоны для разделителей/акцентов
- НЕ ставь `tech` без явной причины
- НЕ группируй разные intent в одну категорию

## Особые случаи
- Первый слайд → всегда `title`
- Последний слайд презентации → может быть `logo` (закрывающий) или `divider`
- Если данных недостаточно для классификации → `text` (безопасный default)

## Auto-detect `slide_type` (v0.20+ native renders)

Помимо `category`, классификатор должен определить `slide_type` для native rendering (build_v9 + kpi/image/chart_renderer):

### `slide_type: "kpi_native"`
Триггеры (любой):
- intent = `data` И в content есть 1-3 крупные числа (не текст), процентные показатели
- key_phrase содержит «KPI», «метрики», «результаты», «N% / N+»
- Числа сопровождаются короткими описаниями (1-2 строки)

Output:
```json
{"slide_type": "kpi_native", "kpi": {
  "title": "...", "numbers": [{"value": "84", "desc": "...", "pct": true, "accent": false}, ...]
}}
```

### `slide_type: "chart_pptx_native"` ⭐ DEFAULT для charts (v1.4+)
Триггеры:
- В draft присутствует chart-like структура (series, axis, time-data, percentage breakdown)
- intent = `chart` или `analytics`
- Контент описывается как **график**, **диаграмма**, **trend**, **breakdown**

**КРИТИЧНО (canonical rule v1.4):** все диаграммы и графики в скилле **обязаны** быть редактируемыми
PowerPoint chart (а не PNG-картинкой). Default → `chart_pptx_native`. Это позволяет пользователю
открыть «Изменить данные» / Edit Data в PowerPoint и менять цифры через встроенный Excel.

Chart types (выбрать по структуре):
- `area_stacked` — temporal data, multiple categories
- `area_100` — temporal data, доли в процентах (sum to 100%)
- `bar` / `bar_stacked` — comparison values (Q1, Q2, ...) или horizontal ranking
- `line` — trend over time, multiple series
- `pie` — single composition / distribution

Output:
```json
{"slide_type": "chart_pptx_native", "chart": {
  "type": "area_100|area_stacked|bar|bar_stacked|line|pie",
  "title": "...", "caption": "...",
  "x": [...], "series": [{"name": "...", "data": [...]}, ...],
  "accent_idx": 0
}}
```

### `slide_type: "chart_native"` (legacy / специальные кейсы)
Использовать ТОЛЬКО когда нужен pixel-perfect canonical стиль с эффектами,
которые PowerPoint native chart не поддерживает: прозрачные overlapping areas,
custom annotations (vertical lines с подписями), особая стилистика осей.
**По умолчанию НЕ использовать** — это PNG-картинка, не редактируется в PPT.

### `slide_type: "table_native"` ⭐ (v1.8+)
Триггеры (ВСЕ должны выполняться):
- В исходнике или брифе есть **табличные данные**: ≥3 колонок × ≥3 строк данных + явная шапка (header row)
- НЕТ merged cells / irregular grid — структура регулярная
- intent включает таблицу/сравнение: `table` / `comparison` / `pricing` / `spec` / `matrix`
- key_phrase содержит «таблица», «сравнение», «vs», «характеристики», «прайс», «параметры»

**Назначение:** настоящая редактируемая PowerPoint table (slide.shapes.add_table()).
Пользователь в PowerPoint:
- двигает границы колонок мышкой
- добавляет/удаляет строки кнопкой `+`
- меняет данные ячеек напрямую
- применяет/меняет table style одной командой

**Стиль:** zebra (single canonical option в v1.8) — slide 56 шаблона Cloud.ru:
- Header row: без заливки (белый), bold 12pt #222222
- Body rows: чередуются `#F2F2F2` (серый) / белый
- Vertical separators 0.5pt `#C8C8C8` между колонками (НЕ горизонтальные)
- Текст SB Sans Display, 11pt body / 12pt header
- Поля ячеек: L/R 12px, T/B 8px
- **Выравнивание: left + top** (canonical, нерушимо)
- Первая колонка 1.4× шире остальных (если `first_col_wider: true`, default)

Output:
```json
{"slide_type": "table_native", "dark": false, "table": {
  "header": "Сравнение тарифов",
  "subtitle": "опц. подзаголовок 11pt",
  "style": "zebra",
  "headers": ["Тариф", "Старт", "Бизнес", "Энтерпрайз"],
  "data": [
    ["Цена/мес", "10 000 ₽", "50 000 ₽", "По запросу"],
    ["Лимит API", "10K req/день", "100K", "Безлимит"],
    ["SLA", "99.5%", "99.9%", "99.99%"]
  ],
  "first_col_wider": true
}}
```

**Управление границами (`borders` config, опц.):**

По умолчанию — только **внутренние вертикали** между колонками (canonical
правило 2026-05-26). Если нужны другие границы — добавь поле `borders`:

```json
"borders": {
  "vertical": true,        // внутренние вертикали между колонками (default true)
  "horizontal": false,     // внутренние горизонтали между строками (default false)
  "outer_top": false,      // внешняя верхняя (default false)
  "outer_bottom": false,   // внешняя нижняя (default false)
  "outer_left": false,     // внешняя левая (default false)
  "outer_right": false,    // внешняя правая (default false)
  "color": "#434343",      // default #434343
  "width_pt": 1.0          // default 1.0
}
```

Аналог опций PowerPoint UI «Границы». Линии рисуются как отдельные shape lines
поверх таблицы (гарантия visibility во всех приложениях: PowerPoint Mac/Win,
Keynote, LibreOffice). Менять через `borders.color` и `borders.width_pt` —
например `width_pt: 0.5` для тонких линий (но учти: в LibreOffice render может
быть не виден, в PowerPoint/Keynote — норм).

**Когда сменить дефолт:**
- Если в исходнике явно есть border-grid (рамки вокруг ячеек) → `outer_*` + `horizontal: true`
- Если нужна только сетка снизу row labels → `horizontal: true`
- Если нужна полная сетка как Excel → все 7 флагов `true`

### ⛔ Когда НЕ выбирать `table_native` — anti-distortion stop (v1.8)

См. [feedback_anti_distortion_safety.md] в memory — общее правило: при
обнаружении объекта, который может **потерять смысл** при упрощении в
canonical → **СТОП, спросить пользователя**.

Конкретно для таблиц — если в исходнике одно из:
- **Merged cells** (объединённые ячейки) — ячейка занимает 2+ колонки или строки
- **Irregular grid** — разное количество ячеек в строках
- **Multi-header rows** — несколько строк шапки (overheader + sub-header)
- **Cell-level color coding** — критичность через цвет (красный = блокер, и т.п.)
- **Nested tables** — таблица в ячейке
- **Visual accents** на отдельных ячейках (стрелки между ячейками, иконки)
- **RACI / responsibility matrix** (специальная семантика R/A/C/I)
- **Roadmap timeline** в табличном виде

→ **СТОП. ОПИСАТЬ что нашёл. ОБЪЯСНИТЬ риск. СПРОСИТЬ:**
> «Я вижу [конкретный объект]. Если перевести в canonical table_native zebra,
> можно потерять [смысл]. Варианты:
> 1. flow_diagram_native — гибкая компоновка плашек (теряем редактирование таблицы)
> 2. table_native упрощённо — сольём merged cells (теряем визуальную группировку)
> 3. Оставить как есть с предупреждением о бренд-нарушении
> 4. Сделать вручную через специальный шаблон
> Что выбираешь?»

**Запрет:** никогда не «решать самостоятельно» при обнаружении anti-distortion
триггера. Всегда спросить.

### ⭐ Распознавание таблиц в картинках (v1.8)

По аналогии со схемами-в-картинках (v1.7): если в source `.pptx` есть embedded
PNG/JPG, который ЯВЛЯЕТСЯ таблицей (≥3 cols × ≥3 rows регулярной сетки) →
**реконструировать** через LLM vision в `table_native` (НЕ оставлять image_native).

Алгоритм:
1. LLM смотрит на PNG → проверяет «таблица или не таблица».
2. Эвристика «таблица» (≥2 из):
   - Видны прямоугольные ячейки в регулярной сетке
   - Видна строка шапки (выделена жирным/фоном)
   - Видны разделители колонок
   - Текст в ячейках выровнен в столбцах
3. Если таблица:
   - Извлечь headers (текст шапки)
   - Извлечь data rows (текст ячеек по строкам)
   - Output → `table_native` с zebra стилем
4. **Если есть merged cells в картинке** → anti-distortion stop+ask.

### `slide_type: "image_native"`
Триггеры:
- В draft есть **embedded image** (картинка как файл)
- Картинка = главный контент (>50% площади или sole content)
- intent = `screenshot` / `photo` / `illustration_main`

Output:
```json
{"slide_type": "image_native", "image": {
  "title": "...", "image_path": "path/to/img.png", "caption": "..."
}}
```

### `slide_type: "flow_diagram_native"` ⭐ (v1.6+, обновлено v1.7)
Триггеры (любой):
- В draft присутствует **схема / блок-диаграмма / процесс**: блоки + стрелки между ними
- intent = `schema` / `flow` / `pipeline` / `process` / `architecture`
- key_phrase содержит «схема», «архитектура», «pipeline», «flow», «процесс»,
  «стадии», «этапы», «phases»
- Source slide содержит SmartArt, либо группу прямоугольников + connectors
- **⭐ v1.7 — Source slide содержит embedded PNG/JPG-картинку, которая ЯВЛЯЕТСЯ
  схемой** (блоки с подписями + стрелки между ними; не фото, не скриншот UI,
  не иллюстрация). См. раздел «Распознавание схем в картинках» ниже.

**Назначение:** редактируемая схема прямо средствами PowerPoint — пользователь
может двигать блоки, переписывать текст в них, переподключать стрелки.
Не PNG-картинка.

Output:
```json
{"slide_type": "flow_diagram_native", "dark": false, "flow": {
  "header": "Pipeline Architecture",
  "subtitle": "опц. подзаголовок 11pt",
  "subtitle_url": "опц. https://... 9pt серым",
  "blocks": [
    {"id": "b1", "x": 35, "y": 180, "w": 240, "h": 60,
     "lines": ["Parse Input"], "font_sizes": [13], "bolds": [true]},
    {"id": "b2", "x": 320, "y": 180, "w": 240, "h": 60,
     "lines": ["Plan"], "font_sizes": [13], "bolds": [true]}
  ],
  "arrows": [
    {"from": "b1", "to": "b2", "side": "right"}
  ],
  "groups": [
    {"label": "Phase 1", "x": 27, "y": 154, "w": 256, "h": 100}
  ],
  "labels": [
    {"x": 35, "y": 122, "w": 600, "h": 20, "text": "подпись",
     "font_size": 11, "bold": false, "align": "left"}
  ],
  "decor": {"enabled": true, "x_start": 950, "y_start": 625,
            "count": 4, "size": 38, "gap": 12}
}}
```

**Композиция (canonical v1.7):**
- Slide canvas — 1280×720 px. Все x/y/w/h в пикселях.
- **Safe-area**: блоки и группы должны лежать в `SAFE_TOP=140 .. SAFE_BOTTOM=660`
  по вертикали и `SAFE_LEFT=30 .. SAFE_RIGHT=1250` по горизонтали.
- Блоки серые (#F2F2F2), текст графит (#222222).
- **Текст в блоках** — выравнивание **по левому** (`align: "left"`) и
  **по верхнему** (`vanchor: "top"`) краю. Поля внутри: 12px со всех + 16 снизу
  (это уже в коде flow_renderer).
- **Стрелки** — тёмно-серые `#434343`, толщина 1pt, открытая галочка
  (`type='arrow'`) размер 8 (`w='lg', len='med'`). **Только горизонтали или
  вертикали** — диагонали запрещены кодом (бросит ValueError). Ломаная стрелка =
  несколько последовательных add_arrow с промежуточными точками; только
  последний сегмент несёт голову (with_head=False для всех кроме последнего).
- Заголовок вписывается в штатный TITLE-placeholder шаблона: 20pt SemiBold CAPS, позиция (35,38)/963×54 (Problem #6) — единообразно у всех native-типов, через `header`/`title`.
- Группировка фаз — пунктирный rect через `groups[]`.
- Декор Cloud.ru (зелёные стрелки ↗, нативные фигуры-группы, геометрия `brand/icons/brand_arrow.svg`, 1pt) — опц., через `decor.enabled=true`.

**🔴 Грид-композиция схем (canonical 2026-05-29) — ПРЕДПОЧТИТЕЛЬНЫЙ способ для схем с блоками.**
Не хардкодь x/y/w/h. Задавай блоки ЛОГИЧЕСКИ — рендерер сам считает раскладку
(**рамка под текст + сетка + единый кегль, без overflow и без разнобоя размеров**):
```json
"flow": {
  "grid": true, "cols": 4, "font_size": 16,
  "blocks": [ {"row": 0, "col": 0, "fill": "gray", "lines": ["Заголовок", "описание"]}, ... ],
  "arrows": [ {"from": [0,0], "to": [0,1]} ]   // ссылки на ячейки [row,col]
}
```
- колонки равной ширины; высота строки = под самый длинный блок строки; единые зазоры;
- **стрелки по ячейкам** `[row,col]`. Направление рендерер определяет сам (одна строка →
  горизонталь, одна колонка → вертикаль). **Branching** (пайплайн → несколько выходов):
  диагональная связь рисуется **ортогональным Z-маршрутом** (вправо→вверх/вниз→вправо) с
  резервом под наконечник — диагонали не нужны и запрещены;
- зелёная плашка-акцент = `"fill": "green"` (текст внутри графит, Problem #2).

**🔴 Нагруженные схемы — залитые ПАНЕЛИ вместо пунктира (2026-05-29).**
Секционные/высоконагруженные схемы группируй **залитыми серыми панелями**, а НЕ
пунктирными рамками (меньше пунктира = читается чище):
```json
"groups": [ {"style": "panel", "x": 30, "y": 175, "w": 590, "h": 240, "label": "СЕКЦИЯ"} ]
```
Блоки ВНУТРИ панели делай `"fill": "white"` (белые карточки на сером фоне). Пунктир
(`groups` без `style`) — только для лёгких схем с 1-2 фазовыми группами.

**Когда растягивать схему на всю safe-area** (правило 2026-05-06):
- **Растягиваем** (заполнить всю SAFE_W × SAFE_H): схема многорядная (≥2 ряда
  блоков) И широкая (≥3 колонки). Это даёт сбалансированную нагруженную схему.
- **Не растягиваем** (естественная высота, вертикально центрируем): линейный
  pipeline в 1 ряд (даже с 4+ колонками); простая схема <3 колонок; контент
  малый. Растягивание сделало бы блоки гигантскими с теряющимся текстом.

**Чеклист после рендера** (обязательный, выполняется LLM Visual Verifier по PNG):
1. Весь ли текст помещается в блоки (без обрезаний / неожиданных переносов)? Да → OK.
2. Если нет → **увеличить фреймы блока** в пределах safe-area. После — снова шаг 1.
3. Если фреймы упёрлись в safe-area → **уменьшить шрифт** (минимум 10pt). После — снова шаг 1.
4. Если 10pt не помогает → **пересмотреть композицию**: разбить заголовок на
   2 строки, сократить текст (с подтверждения пользователя), или сменить
   структуру (меньше колонок, больше рядов).

**Композиционные подсказки для классификатора:**
- 3–4 блока в строку → колонки шириной ~235px, gap 22px, X start 175 (если есть тег слева) или 35
- 2 строки блоков → row 1 на y=180, row 2 на y=270, vertical arrows между серединами
- ≤ 8 блоков на слайде — иначе split на 2 слайда
- Подписи блоков ≤ 4 строк по 30 символов

**Если нет triggers** для native — использовать стандартный flow `clone_from_slide` (donor catalog).

### ⭐ Распознавание схем в картинках (v1.7+)

**Проблема (наблюдалась 2026-05-26):** если в source `.pptx` слайд содержит
**embedded PNG/JPG**, а сама картинка — это схема (нарисованная в Figma /
draw.io / скриншот whiteboard), классификатор по умолчанию выбирал
`image_native` и оставлял картинку как есть. Это нарушает canonical правило
v1.6: «все диаграммы должны быть редактируемыми».

**Что делать:**

1. **Когда видишь embedded image на слайде — посмотри на неё** (LLM vision).
   Это часть pipeline: классификатор имеет доступ к extracted image файлам
   (через `extract_images.py` → `draft_images_<name>/`).

2. **Эвристика «схема vs. не схема»** — это **схема**, если выполнено ≥2 из:
   - Видны прямоугольные блоки с текстом
   - Видны стрелки или линии между блоками
   - Видны подписи / labels / lanes (Phase 1, Stage 2…)
   - Структура «flow» (направленный граф), а не сетка/фото/screenshot UI

3. **Если это схема** — НЕ выбирай `image_native`. Вместо этого:
   - Выбери `slide_type: "flow_diagram_native"`
   - **Реконструируй структуру** из картинки в JSON-конфиг (`flow.blocks`,
     `flow.arrows`, `flow.groups`, `flow.labels`):
     - Идентифицируй каждый блок: текст в нём + ориентировочные координаты
       (нормализуй пропорции картинки к safe-area 1280×720).
     - Идентифицируй стрелки: from-block → to-block (через id ссылки) + side
       (right/left/top/bottom).
     - Идентифицируй группы / lanes / phases — если есть пунктирная рамка или
       заголовок над группой блоков.
     - Заголовок слайда (`flow.header`) — из текста на слайде вокруг картинки
       (title / подпись), либо из текста ВНУТРИ картинки в её верхнем-левом
       углу.
   - Применяй canonical-стиль flow_diagram_native (не пытайся скопировать
     цвета из картинки) — все блоки `fill: "gray"`, стрелки тёмно-серые,
     группы пунктирные, декор Cloud.ru.

4. **Если это НЕ схема** (фото, скриншот UI, иллюстрация, абстракт, инфографика
   с большим количеством мелких деталей которые не уложить в блоки+стрелки) —
   оставляй `image_native` как раньше.

5. **Если несколько схем на одном слайде** — каждая схема становится отдельным
   output-слайдом (apply split decision из правил выше).

**Output пример** (реконструкция из картинки):
```json
{"slide_type": "flow_diagram_native", "dark": false, "flow": {
  "header": "Architecture overview",
  "blocks": [
    {"id": "ingest", "x": 30, "y": 200, "w": 240, "h": 80,
     "lines": ["Ingest", "Streaming + batch"], "font_sizes": [14, 11]},
    {"id": "store",  "x": 320, "y": 200, "w": 240, "h": 80,
     "lines": ["Store", "S3 + Postgres"], "font_sizes": [14, 11]},
    {"id": "serve",  "x": 610, "y": 200, "w": 240, "h": 80,
     "lines": ["Serve", "API gateway"], "font_sizes": [14, 11]}
  ],
  "arrows": [
    {"from": "ingest", "to": "store", "side": "right"},
    {"from": "store",  "to": "serve", "side": "right"}
  ]
}}
```

**Запрет:** никогда не оставлять картинку-схему как `image_native`. Если вообще
сомневаешься «схема или нет?» — **уточнить у пользователя в чате**: «Эта картинка
выглядит как схема — переисовать как редактируемую (flow_diagram_native)? Или
оставить картинкой?»

## Routing decision tree

```
Есть anti-distortion триггер (merged cells, RACI, roadmap, color-coded
  ячейки, иконки-акценты, custom annotations)?
    → STOP + ASK пользователя (см. feedback_anti_distortion_safety.md)

Есть chart-like data (series, axis)? → chart_pptx_native (DEFAULT, editable)
  fallback chart_native только если нужен PNG со спец-эффектами
Есть регулярная таблица ≥3×3 с явной шапкой, БЕЗ merged cells? → table_native (v1.8+)
Есть схема/процесс/архитектура с блоками+стрелками? → flow_diagram_native (v1.6+)
Есть 1-3 KPI числа без текста-параграфа? → kpi_native
Есть embedded image как main content? → image_native
Иначе → standard category + clone_from_slide donor
```

## Split decision (v1.4) — разгрузка перегруженных слайдов

Если source slide перегружен (превышает canonical density limits §8 из canonical-rules) — **разбить на несколько output slides**.

### Триггеры split

| Источник имеет | Решение |
|---|---|
| 4+ KPI цифр одного типа | split на 2 kpi_native (3+1 или 2+2) |
| 6+ блоков с подзаголовками | split на 2 multicolumn (3+3 или 4+2) |
| body > 80 слов на одну колонку | split на 2 content slides (paragraph+paragraph) |
| 5+ image-thumbnails | split на 2 image_grid (3+2) |
| Чарт с 5+ series | упростить до 2-3 series + split «context» + «detail» chart |
| callout с цитатой 30+ слов | разбить цитату на 2 callout (часть 1 + часть 2) |
| Title 60+ chars + body | split на title-slide + content-slide |
| 3+ topics без structural связи | split на divider + N content slides |

### Decision algorithm

```python
def should_split(slide):
    issues = count_density_issues(slide)
    if issues == 0:
        return [slide]  # OK
    if issues == 1 and severity == "minor":
        return [slide]  # допустимо
    # Перегружен — split
    return split_into_subslides(slide)
```

### Output format при split

```json
{
  "num": 5,
  "_source_slide": 3,
  "_split_part": "1/2",
  "category": "multicolumn",
  ...
},
{
  "num": 6,
  "_source_slide": 3,
  "_split_part": "2/2",
  "category": "multicolumn",
  ...
}
```

### Принципы split

- **Логическая связность**: split по semantic boundaries, не механически
- **Разные donors**: для variety использовать разные idx для одной категории (anti-monotony)
- **Continuity hint**: в title/footer первого/второго слайда указать "1/2", "2/2" или contextual hint
- **Tone balance**: в split не делать обе части тёмными подряд (canonical §2)

### НЕ split когда

- Слайд имеет много элементов **по канонической композиции** (donor 33 = 6 блоков by design)
- Source слайд — title или divider (они и так минимальны)
- Source слайд — chart с 3+ series если все нужны для narrative

## Subjective overload check (designer judgment)

Помимо чек-листа выше, классификатор должен **глазами оценить** source slide и спросить:

1. **«Если бы это был мой слайд, я бы сделал split?»** — если да, делай split
2. **«Глаз пробегает по слайду за 5 секунд?»** — если нет, перегружен
3. **«Take-away мгновенный или приходится вчитываться?»** — если вчитываться, перегружен
4. **«Сколько отдельных идей на слайде?»** — если 3+ независимых, split

### Эвристика «принцип одного слайда»

Canonical §7 пункт 2 (slide 87 шаблона): **«Один слайд = одна мысль»**.

Если source имеет:
- 1 KPI цифру → 1 output slide ✓
- 3 KPI цифры одной темы → 1 output slide (kpi_native 3 цифры) ✓
- 5 KPI цифр **разных тем** → split на 2 (по semantic groups)
- 3 КПЕ разных тем + 4 process steps → 2-3 slides (отдельный KPI слайд + отдельный process слайд)

### Examples of split decisions

| Source content | Split decision |
|---|---|
| Title 200 chars + spикер + 5 KPI + 4 mehanizma | **4 slides:** title (короткий) → divider → KPI → 4 mehanizma |
| 6 product features списком | **2 slides:** features 1-3 + features 4-6 (3+3 multicolumn) |
| Chart 5 series with intro text | **2 slides:** intro text + chart simplified |
| Quote 50 слов | **2 callouts:** часть 1 setup + часть 2 punchline |
| Team of 12 people | **2 slides:** team_5 + team_5+2 (split by role/department) |

## Контекстные правила
- Если 2 соседних слайда выходят в одной категории — отметь это, Layout Designer должен выбрать **разные idx** в той же категории, чтобы был ритм
- При перепаде темы (новый раздел) — рекомендуй `divider` перед

## Пример

**Вход:** 5 слайдов, intent: `[title, data, data, data, image]`

**Выход:**
```json
{
  "slides": [
    {"num": 1, "category": "title", "subcategory_hint": "white", "rationale": "Открывающий слайд, нейтральный фон"},
    {"num": 2, "category": "callout", "subcategory_hint": "white", "rationale": "Одна крупная цифра — Важная информация"},
    {"num": 3, "category": "multicolumn", "subcategory_hint": "3col", "rationale": "3 KPI цифры рядом"},
    {"num": 4, "category": "multicolumn", "subcategory_hint": "3col_alternative", "rationale": "Та же сетка, но другой idx — для ритма"},
    {"num": 5, "category": "image", "subcategory_hint": "illustration_half", "rationale": "Финальный слайд с визуалом"}
  ]
}
```
