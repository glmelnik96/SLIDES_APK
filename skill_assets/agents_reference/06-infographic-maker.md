# 06. Infographic Maker

## Роль
Когда слайд содержит схему / процесс / связи / диаграмму — генерировать спецификацию инфографики, которую `build_pptx.py` может построить как нативные shapes PowerPoint.

## Вход
- Slide Classifier вывод (category)
- Content Distributor вывод (контент в placeholder'ах)

## Выход (JSON)
```json
{
  "slide_num": 7,
  "infographic_type": "process | flow | tree | comparison | matrix | chart_bar | chart_pie | none",
  "shapes": [
    {
      "type": "rectangle | rounded_rect | arrow | line | circle | text",
      "left_emu": 1000000, "top_emu": 1500000, "width_emu": 2000000, "height_emu": 1000000,
      "fill_color": "#26D07C",
      "stroke_color": "#222222",
      "stroke_width_pt": 1,
      "text": "Этап 1",
      "font": "SB Sans Display",
      "font_size_pt": 18,
      "font_color": "#FFFFFF"
    }
  ]
}
```

## Правила построения

### Типы инфографик и сетки
- **Process (стрелки):** N rectangles в ряд, между ними arrow shapes. Высота 100-150 px, расстояние = 60 px (виртуально)
- **Flow:** rectangles + connecting lines, может быть нелинейный
- **Tree:** иерархия, parent → children через линии
- **Comparison:** 2 колонки vs / vs табличка
- **Matrix:** 2×2 grid
- **Chart:** для данных — гистограмма / pie через Chart placeholder

### Цвета фигур
- Главный шаг/блок: Green `#26D07C` (заливка-акцент)
- Промежуточные: Gray `#F2F2F2`
- **Текст ВСЕГДА `#222222`** на Green / Gray / White (Problem #2, 2026-05-29).
- Текст White `#FFFFFF` — **только на тёмном** (графит `#222222` / чёрный).
- ❌ Запрещено: белый текст на зелёной плашке, зелёный текст на белом.
- Стрелки/линии: Black `#222222`, толщина 1pt (square caps)

### Шрифт и начертание (Problem #3, 2026-05-29)
- Обычный текст: `"font": "SB Sans Display"`.
- **Эмфаза/заголовки шага: `"font": "SB Sans Display Semibold"`** — НЕ bold-флаг.
- ❌ Никогда не ставить отдельный признак «bold»: жирность только через face Semibold.

### Размеры
- Слайд real px: 1280×720 (EMU 12192000×6858000)
- 1 px = 9525 EMU
- Микромодуль 2px = 19050 EMU
- Стандартный отступ 40 px = 381000 EMU

### Запреты
- НЕ использовать скругления > 4px (запрет брендбука: «без скруглений»)
- НЕ использовать градиенты в фигурах
- НЕ накладывать тени, glow, отражения
- НЕ ставить текст < 10pt
- НЕ выходить за поля слайда (40 px отступ от края)

## Если инфографика не нужна
Возвращай `{"infographic_type": "none", "shapes": []}` — слайд останется со стандартным контентом placeholder'ов.

## Пример (process)

**Вход:** category=`timeline`, контент 4 этапа: "Анализ", "Дизайн", "Разработка", "Запуск"

**Выход:**
```json
{
  "slide_num": 7,
  "infographic_type": "process",
  "shapes": [
    {"type": "rectangle", "left_emu": 1000000, "top_emu": 2500000, "width_emu": 2000000, "height_emu": 1000000, "fill_color": "#26D07C", "stroke_color": "none", "text": "Анализ", "font": "SB Sans Display", "font_size_pt": 18, "font_color": "#222222"},
    {"type": "arrow", "left_emu": 3050000, "top_emu": 2900000, "width_emu": 400000, "height_emu": 200000, "fill_color": "#222222"},
    {"type": "rectangle", "left_emu": 3500000, "top_emu": 2500000, "width_emu": 2000000, "height_emu": 1000000, "fill_color": "#F2F2F2", "stroke_color": "#222222", "stroke_width_pt": 1, "text": "Дизайн", "font": "SB Sans Display", "font_size_pt": 18, "font_color": "#222222"},
    {"type": "arrow", "left_emu": 5550000, "top_emu": 2900000, "width_emu": 400000, "height_emu": 200000, "fill_color": "#222222"},
    {"type": "rectangle", "left_emu": 6000000, "top_emu": 2500000, "width_emu": 2000000, "height_emu": 1000000, "fill_color": "#F2F2F2", "stroke_color": "#222222", "stroke_width_pt": 1, "text": "Разработка", "font": "SB Sans Display", "font_size_pt": 18, "font_color": "#222222"},
    {"type": "arrow", "left_emu": 8050000, "top_emu": 2900000, "width_emu": 400000, "height_emu": 200000, "fill_color": "#222222"},
    {"type": "rectangle", "left_emu": 8500000, "top_emu": 2500000, "width_emu": 2000000, "height_emu": 1000000, "fill_color": "#F2F2F2", "stroke_color": "#222222", "stroke_width_pt": 1, "text": "Запуск", "font": "SB Sans Display", "font_size_pt": 18, "font_color": "#222222"}
  ]
}
```

## MVP-замечание
В первой версии скилла фокус на process, comparison и chart_bar. Tree и matrix — TODO.
