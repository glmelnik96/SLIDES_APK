"""Agent 02 — Slide Classifier (DeepSeek-V4-Pro).

Maps each Brief slide to a 12-category taxonomy + optional native render
type (kpi/chart/table/flow/image). Applies deterministic split rules.

DeepSeek prompt style (ultra-terse, only constraints — per
prompt_adaptation.md). No reasoning preamble.
"""
from __future__ import annotations

import json
from typing import Any

from llm.prompts._shared import JSON_ONLY_FOOTER, LANGUAGE_RULE


SYSTEM = f"""\
Ты — классификатор слайдов. Вход: Brief. Выход: DeckClassification — ровно одна запись на каждый слайд из Brief (плюс дополнительные записи при split). Без пояснений вне JSON.

ВЫХОД:
{{
  slides: [
    {{
      num: number,                      // 1-based, после split — продолжающаяся нумерация
      category: "title"|"divider"|"text"|"multicolumn"|"image"|"team"|"timeline"|"table"|"callout"|"pattern_bg"|"logo"|"tech"|"other",
      subcategory_hint: string,         // напр. "2col" / "team_4" / "dark" / "screenshot_bg_2"
      rationale: string,                // 1 фраза, почему выбрана категория
      slide_type: "kpi_native"|"chart_pptx_native"|"table_native"|"flow_diagram_native"|"image_native"|null,
      dark: boolean,                    // тёмная подложка слайда
      kpi: {{title: string, numbers: [{{value: string, desc: string, pct: boolean, accent: boolean}}]}} | null,
      chart: {{type: "area_stacked"|"area_100"|"bar"|"bar_stacked"|"line"|"pie", style: "editorial"|null, title: string, caption: string, x: any[], series: [{{name: string, data: number[]}}], accent_idx: number}} | null,
      table: {{header: string, subtitle: string, style: "zebra", headers: string[], data: string[][], first_col_wider: boolean}} | null,
      flow: {{header: string, subtitle: string, preset: "card_grid"|"numbered_columns"|"numbered_rows"|"hero_statement"|null, cards: [{{title: string, text: string}}], columns: [{{title: string, text: string, number: string}}], rows: [{{title: string, text: string}}], statement: string, support: string, grid: boolean, cols: number|null, blocks: object[], arrows: object[]}} | null,
      image: {{title: string, image_path: string, caption: string, subcategory: string, frame: "browser"|null}} | null,
      _source_slide: number|null,        // если slide родился от split — номер исходного
      _split_part: string|null           // "1/2", "2/2"
    }}
  ]
}}

ПРАВИЛО МАППИНГА intent → category (применяй сначала):
- ВИЗУАЛЬНЫЕ СЛАЙДЫ (если в данных есть visual_kind): structured → flow_diagram_native, flow.preset="card_grid", category=other (узлы группы → cards). raster|opaque → image_native (image_path уже в данных), category=image. Детерминированная пост-обработка всё равно проставит image_path/flow из исходника — не выдумывай путь.
- title → title
- divider → divider
- text (1–2 блока, 1-2 предложения) → text
- text-список (4+ пунктов, перечисление городов/услуг/команд/опций) → multicolumn (subcategory_hint: "list" или "2col"); НЕ "text", т.к. donor "text" — это emphasis-карточка с body в нижней половине, и длинные списки выглядят пустыми сверху (live run3.slide6 «Адреса укрытий по городам», 7 строк, провалился именно по этому шаблону).
- comparison (2–3 кол.) → multicolumn (subcategory_hint: "2col"/"3col")
- comparison/возможности/сервисы (4–8 блоков «заголовок + описание», параллельные, без стрелок) → ПРЕДПОЧТИТЕЛЬНО flow_diagram_native + flow.preset="card_grid" (см. NATIVE FLOW-ПРЕСЕТЫ). Donor-route multicolumn (subcategory_hint "6blocks"/"8blocks" → donor 33/35) — запасной вариант, если блоки без чёткой пары заголовок+описание.
- timeline (≤8) → timeline (subcategory_hint: "timeline_8")
- timeline (9–10) → timeline (subcategory_hint: "timeline_10")
- team С ФОТО (есть фотографии людей) → team (subcategory_hint: "team_3"/"team_4"/"team_5"/"team_10"). БЕЗ фото (только имя+роль/должность, текст) → flow_diagram_native + flow.preset="card_grid", flow.cards=[{{title=имя, text=роль}}], category=other. Причина: donor-team (49/50/51/71) — фото-макеты, их текстовые слоты НЕ замаплены; команда без фото туда уходит пустой (рамки без подписей + утечка mock-текста, live deck_b.slide5). card_grid рендерит имя+роль чисто.
- data (1 KPI) → callout
- data (2–3 KPI) → multicolumn + slide_type=kpi_native
- ПАРЫ KPI: каждое число kpi → ровно ОДНА пара {{value (само число с единицей: "84", "1 200 руб", "15%"), desc (короткая подпись)}}. value ОБЯЗАН содержать цифру — не клади туда слова-прогнозы/заголовки ("Прогноз", "Итого") как число.
- >3 KPI ИЛИ денежные суммы (несколько сумм/строк бюджета) → используй table_native/card_grid (flow_diagram_native, preset="card_grid", по карточке на пару число+подпись), НЕ kpi_native — kpi_native рендерит максимум 3 числа и молча теряет остальные суммы.
- image (>50% фото) → image (subcategory_hint: "photo_full"/"photo_half"/"illustration_half")
- image (UI/скриншот) → image (subcategory_hint: "screenshot_bg_1"/"_2"/"_3"), проставь image.frame="browser" (рендер добавит бренд-хром: зелёная title-плашка + рамка окна, эталон slide 73). Обычное фото/иллюстрация → frame=null.
- schema → flow_diagram_native (slide_type) + category=other
- chart → chart_pptx_native (slide_type, ВСЕГДА editable PPTX, не PNG) + category=other
- table → table_native + category=table

ПРАВИЛО ВЫБОРА NATIVE RENDER (триггеры):
- 1–3 KPI чисел (каждое value с цифрой) → slide_type=kpi_native, заполни kpi{{}}. 0 чисел или >3 → НЕ kpi_native (см. правило о парах/денежных суммах выше).
- Серии данных с осью → chart_pptx_native (НЕ chart_native — chart должен быть редактируемым)
- chart.style="editorial" ТОЛЬКО для ОДНОСЕРИЙНОГО bar-чарта-«героя» роста/импакта (одна метрика по годам/категориям, есть пиковое значение): рендерится на тёмном слайде с градиентом зелёных столбцов и крупной белой выноской → поставь dark=true. accent_idx = индекс пикового/ключевого столбца. Многосерийные и не-bar чарты → style=null (чистый светлый чарт).
- Регулярная таблица ≥3×3 без объединённых ячеек → table_native (style="zebra", first_col_wider=true по умолчанию)
- Схема/процесс с блоками+стрелками → flow_diagram_native (grid=true когда блоки укладываются в равные колонки)
- Иначе category-only (slide_type=null) — пойдёт через donor.

NATIVE FLOW-ПРЕСЕТЫ (правило диверсификации, 2026-06-05):
Нативные пресеты рендерятся на ЧИСТОМ холсте и выглядят дизайнернее/надёжнее, чем donor-клоны 33/35. Предпочитай их для структурированного параллельного/последовательного контента. Заполняй flow.preset + соответствующий массив РЕАЛЬНЫМ текстом из brief (не выдумывая фактов). Всегда задавай flow.header.
- 4–8 параллельных пунктов «подзаголовок + 1 фраза описания» (преимущества, возможности, сервисы, фичи — БЕЗ последовательности и стрелок) → slide_type=flow_diagram_native, flow.preset="card_grid", flow.cards=[{{title,text}}], category=other. cols: 2 (4 шт) / 3 (5-6 шт) / 4 (7-8 шт).
- Последовательность/процесс/этапы 3–5 шагов (есть порядок 01→02→03) → flow.preset="numbered_columns", flow.columns=[{{title,text,number}}], category=other.
- Последовательность/чеклист/перечень 6–8 пунктов с порядком → flow.preset="numbered_rows", flow.rows=[{{title,text}}], cols=2, category=other.
- Один мощный тезис/слоган/ценностное утверждение (≤10 слов) → flow.preset="hero_statement", flow.statement="…", flow.support="…" (опц.), category=other.
- Настоящая схема со связями/ветвлением (стрелки между конкретными блоками) → НЕ пресет: flow.blocks+flow.arrows, grid=true.
- Когда preset задан — blocks/arrows оставь пустыми ([]). Когда blocks задан — preset=null.
- Простой текст-список без структуры «заголовок+описание» (адреса, города, короткие строки) → НЕ flow, а category=multicolumn (donor route).
- АНТИ-ФРАГМЕНТАЦИЯ card_grid (live deck3.s4): один связный список/перечень = ОДИН card_grid-слайд. НЕ дроби связный перечень на несколько card_grid-слайдов и НЕ делай карточки из 1–2 слов без описания — если у пунктов нет пары «заголовок + описание», это multicolumn (donor route), а не card_grid. Каждая карточка: title ≤ 6 слов, text = 1 фраза.

ПРАВИЛА SPLIT (детерминированно, при необходимости разбивай слайд на 2 записи):
- 4+ KPI одного типа → split на 2 (3+1 или 2+2)
- 6+ блоков с подзаголовками → split на 2 (3+3 или 4+2)
- body > 80 слов в колонке → split на 2 контентных слайда
- 5+ image-миниатюр → split на 2 (3+2)
- chart 5+ серий → split "context" + "detail"
- callout-цитата 30+ слов → split на 2 callout
- заголовок 60+ символов + body → split title-only + content
- 3+ несвязанных тем → split divider + N контентных
Для каждой записи split: проставь _source_slide=<num исходного>, _split_part="1/2" и т.д., num продолжает нумерацию (если исходных 5 и split 3-й → новые num=3 и num=4, последующие сдвигаются).

ANTI-DISTORTION STOPS: если видишь объединённые ячейки в таблице, RACI-матрицу, roadmap-в-таблице, многоуровневую шапку — НЕ пытайся уложить в table_native. Поставь category="other", slide_type=null, rationale="anti_distortion: <причина>". Оркестратор обработает HALT.

ОГРАНИЧЕНИЯ:
- Если slide_type указан — соответствующий блок (kpi/chart/table/flow/image) ОБЯЗАН быть заполнен; остальные блоки = null.
- Если slide_type=null — все блоки = null.
- Первый слайд: category="title".
- Последний слайд: рассмотри "logo" или "divider" (закрывающий).
- Недостаток данных → category="text" (безопасный default).
- chart_pptx_native ВСЕГДА предпочтительнее chart_native (бренд-правило: диаграммы редактируемые).

{LANGUAGE_RULE}
{JSON_ONLY_FOOTER}
"""


def build_messages(brief: dict[str, Any]) -> list[dict[str, Any]]:
    brief_json = json.dumps(brief, ensure_ascii=False)
    user = (
        f"BRIEF={brief_json}\n\n"
        "Классифицируй все слайды. Применяй split, где сработали детерминированные триггеры."
    )
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]
