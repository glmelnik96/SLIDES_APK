"""Agent 04 — Layout Designer (DeepSeek-V4-Pro).

Picks donor ``layout_idx`` from the Cloud.ru template for each slide
classified by Agent 02. Applies anti-monotony (no 3-in-a-row same donor).

The donor lookup table is generated from
``skill_assets/brand/donor-slot-map.yaml`` at import time so the prompt
always matches the actual template + slot map. The previous hand-written
table referenced template indices that didn't exist (1, 9, 25-as-text,
69, 94...) and caused every live deck to render with template defaults
where body content should have been.
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from graph import donor_map
from llm.prompts._shared import JSON_ONLY_FOOTER, LANGUAGE_RULE


def _build_donor_table() -> str:
    """Markdown-ish table of every valid donor: idx | category | hint | max_chars.

    One line per donor — kept compact so DeepSeek can scan it inline.
    """
    rows = donor_map.donor_summary()
    lines = [
        "| idx | category               | max_chars | use_when |",
        "|-----|------------------------|-----------|----------|",
    ]
    for r in rows:
        cat = r["category"][:24].ljust(22)
        uw = (r["use_when"] or r["description"])[:80].replace("|", "/")
        lines.append(f"| {r['idx']:>3} | {cat} | {r['max_chars']:>9} | {uw} |")
    return "\n".join(lines)


def _build_category_map() -> str:
    """SlideCategory → canonical donor candidates derived from the YAML.

    Format: ``category | candidates`` where candidates is a comma-separated
    list of valid donor indices the designer may pick from.
    """
    eq = donor_map.category_equivalence()
    bridge = {
        "title":       ("title_open", "title_dark"),
        "divider":     ("divider",),
        "text":        ("content_text",),
        "multicolumn (default 2col)":  ("content_2col",),
        "multicolumn (3col)":          ("content_3col",),
        "multicolumn (4block)":        ("content_4block",),
        "image (grid/cards)":          ("image_grid",),
        "image (main/photo)":          ("image_main",),
        "image (screenshot)":          ("screenshot",),
        "callout":     ("callout",),
        "kpi (native preferred)":      ("kpi",),
        "table":       ("table",),
        "timeline":    ("timeline",),
        "logo (final)":                ("logo_finale",),
    }
    lines = ["| classifier category       | valid donors |", "|---|---|"]
    for label, buckets in bridge.items():
        ids: list[int] = []
        for b in buckets:
            ids.extend(eq.get(b, []))
        # Dedupe preserving order.
        seen: set[int] = set()
        ids = [i for i in ids if not (i in seen or seen.add(i))]
        lines.append(f"| {label} | {', '.join(map(str, ids)) or '—'} |")
    return "\n".join(lines)


def _build_tone_groups() -> str:
    tg = donor_map.tone_groups()
    return "\n".join(f"{k}: {v}" for k, v in tg.items())


@lru_cache(maxsize=1)
def _build_system_prompt() -> str:
    """Lazily compose the SYSTEM prompt.

    Reads ``donor-slot-map.yaml`` on first call only — keeps module import
    side-effect-free so containers that lack ``skill_assets/`` (e.g. the
    bot, which only needs Celery task registration) can still import the
    pipeline graph without crashing.
    """
    donor_table = _build_donor_table()
    category_map = _build_category_map()
    tone_groups = _build_tone_groups()
    valid_ids = sorted(donor_map.valid_donor_ids())
    return f"""\
Ты — подборщик donor-слайдов из шаблона Cloud.ru. Вход: DeckClassification (категории и subcategory_hint). Выход: LayoutPlan — donor (layout_idx) на каждый слайд.

ВЫХОД:
{{
  slides: [
    {{
      num: number,
      layout_idx: number,            // donor — ТОЛЬКО из списка валидных (см. ниже)
      layout_name: string,           // короткое имя по таблице
      rationale: string,             // 1 фраза
      slot_styles_override: object   // {{}} если не нужно
    }}
  ]
}}

ВАЛИДНЫЕ DONOR-ИНДЕКСЫ (использовать ТОЛЬКО эти):
{valid_ids}
Любой другой layout_idx = ошибка. Слайды 1, 2, 3, 9 — это служебные слайды самого шаблона ("Шаблон презентации", "Привет!", "Содержание", "Слайды-Разделители"), их НЕЛЬЗЯ выбирать.

КАТЕГОРИЯ → КАНДИДАТЫ (выбирай из перечисленных):
{category_map}

ПОЛНАЯ ТАБЛИЦА ДОНОРОВ (idx, category, max_chars, use_when):
{donor_table}

ТОНОВЫЕ ГРУППЫ (для anti-monotony):
{tone_groups}

ANTI-MONOTONY (детерминированно):
- НЕ ставь один и тот же layout_idx 3 раза подряд.
- Если 2 предыдущих слайда уже того же idx → возьми другого кандидата из той же категории или соседний idx из tone-группы.
- Чередуй light/dark: ≤40% тёмных слайдов в колоде.

ЖЁСТКИЕ ПРАВИЛА (нарушение = брак):
- Donor 21 / 22 (content_text_white / content_text_dark) — это EMPHASIS-карточки на ОДИН абзац-вывод (body в нижней половине слайда). НЕ выбирай их для перечислений (≥4 коротких пункта), длинных body (>250 chars), wall-of-text. Для перечислений из 4+ строк бери donor 28 (content_2col) или 34 (content_3col) — body там в верхней части, без пустого верха. Live run3.slide6 (адреса по городам) и run4.slide9 (IoTDM-параграфы) визуально провалились из-за этого выбора.
- НЕ используй donor 25 (logo_green_caption / "Спасибо за внимание") для слайдов категории "text" или "multicolumn" — он только для ФИНАЛЬНОГО логослайда.
- НЕ используй donor 78 / 86 нигде кроме самого последнего слайда (logo finale).
- НЕ выбирай donor НЕ из VALID_IDS — оркестратор это отбросит и подставит дефолт.
- Категория "title" → donor 4 (белый) или 6/7 (тёмный) — НЕ donor 1 (это не донор, это титул шаблона).
- Категория "divider" → donor 12 (зелёный) / 13 (тёмный) / 10 (alt green) / 62 — НЕ donor 9 (это не донор).
- Категория "logo" → donor 25 ИЛИ donor 78 (с продуктовой выкладкой) ИЛИ donor 86 (фото-финал) — только в самом конце.
- Donor team-фото (49/50/51/71) — ТОЛЬКО когда есть реальные фотографии людей. Их текстовые слоты не замаплены, поэтому команда «только имена+должности» рендерится пустыми рамками + утечкой mock-текста (live deck_b.slide5). Текстовую команду без фото Агент 02 уводит в flow_diagram_native card_grid (layout_idx=0) — не подбирай ей team-донор.
- KPI native (slide_type=kpi_native) → layout_idx по умолчанию 43 (light) или 44 (dark).
- chart_pptx_native / table_native / flow_diagram_native / image_native → layout_idx=0, rationale="native render".

ПЛОТНОСТЬ КОНТЕНТА → ВЫБОР КОЛОНОЧНОСТИ (порог по словам на блок описания):
- 2 колонки: до ~45 слов на колонку.
- 3 колонки (donor 34): до ~35 слов на колонку.
- 4 блока (donor 29): до ~25 слов на блок.
- 6 блоков (donor 33): до ~20 слов на блок.
- 8 блоков (donor 35): до ~15 слов на блок.
Если на блок приходится БОЛЬШЕ слов, чем порог — НЕ ужимай в плотную сетку: выбери donor с меньшим числом блоков (больше места) ИЛИ оставь native-пресет (flow_diagram_native card_grid рендерит плотнее и аккуратнее, его выбирает Агент 02). Слишком плотная сетка с длинным текстом = «стена текста», брак у Visual Verifier.

ТЕСТ КОНТЕКСТА ДЛЯ ИЗОБРАЖЕНИЙ:
- Прежде чем выбрать image-donor (image_main/screenshot/image_grid) спроси: «если убрать картинку, слайд потеряет смысл?». ДА (картинка несёт контент — график, скриншот, схема) → image-donor оправдан. НЕТ (картинка декоративная) → выбери контентный donor, картинка только засорит.
- image_main (73/86) — картинка ЕСТЬ главный контент. Не используй его для текстовых слайдов «с картинкой для красоты».

OVERFLOW STRATEGY (применяй по порядку):
1) Donor с большим max_chars / меньшим числом блоков в той же категории (даёт больше места под слот).
2) Split уже сделан Агентом 02; если всё ещё переполнено — note в rationale, оркестратор может ещё раз сплитнуть.
3) Понизь размер в slot_styles_override (≥12pt) — последняя мера, только для заголовка/коротких подписей.
4) Если ничего не помогает — предпочти native-пресет (rationale: "overflow → native preset") вместо переполненного donor.

SLOT_STYLES_OVERRIDE (только при необходимости):
- {{"size_pt": <int>}} — если канонический размер не подходит (overflow); минимум 12pt.
- {{"remove_shapes": [<ph_idx>]}} — если плейсхолдер не нужен.
- Цвета НЕ менять.

{LANGUAGE_RULE}
{JSON_ONLY_FOOTER}
"""


def build_messages(classification: dict[str, Any]) -> list[dict[str, Any]]:
    user = (
        f"CLASSIFICATION={json.dumps(classification, ensure_ascii=False)}\n\n"
        "Подбери donor для каждого слайда. ТОЛЬКО валидные индексы. Применяй anti-monotony."
    )
    return [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": user},
    ]
