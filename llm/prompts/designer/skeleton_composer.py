"""skeleton_composer — fills an archetype SKELETON's content dict for ONE slide.

Unlike the free-grid ``slide_composer`` (which places typed blocks on a 12×10
grid), this composer never touches layout: it picks ONE skeleton layout from a
short candidate list and returns only the CONTENT that layout needs. The
skeleton (``renderers.designer.layouts``) owns all geometry, so the output is a
small Composition with ``layout`` + ``content`` and no ``blocks``.

Output validates against ``renderers.designer.composition_dsl.Composition``.
"""
from __future__ import annotations

import json
from typing import Any

from llm.prompts._shared import JSON_ONLY_FOOTER

# Per-layout content schema, shown to the model so it fills exactly the right
# keys. Geometry is NOT the model's concern — the skeleton handles it.
_LAYOUT_SCHEMAS = {
    "cover_green": '{"title": "...", "subtitle": "..."?}',
    "cover_dark": '{"title": "...", "subtitle": "..."?}',
    "section_divider": '{"title": "...", "kicker": "Раздел 02"?}',
    "bullet_list": '{"title": "...", "intro": "..."?, "bullets": ["...", "..."]}',
    "points_3": '{"title": "...", "points": [{"head":"...","text":"..."} ×3]}',
    "points_4": '{"title": "...", "points": [{"head":"...","text":"..."} ×4]}',
    "points_6": '{"title": "...", "points": [{"head":"...","text":"..."} ×6]}',
    "points_8": '{"title": "...", "points": [{"head":"...","text":"..."} ×8]}',
    "table_zebra": ('{"title": "...", "headers": ["...","..."], '
                    '"rows": [["...","..."], ...], "accent_col": null|<int>}'),
    "chart_columns": ('{"title": "...", "categories": ["..."], '
                      '"series": [{"name":"...","values":[1,2]}], '
                      '"accent_idx": 0, "data_provenance": "native|estimated"}'),
    "roadmap_timeline": ('{"title": "...", "milestones": '
                         '[{"label":"Q1","text":"...","accent":false}]}'),
}

_GUIDANCE = """\
ВЫБОР МАКЕТА (layout):
- Если кандидатов несколько (например title-body), выбери наиболее подходящий:
  * points_3/4/6/8 — когда контент распадается на 3/4/6/8 ОТДЕЛЬНЫХ направлений,
    у каждого короткий заголовок (head) + одно предложение (text). Выбирай число
    точек по фактическому количеству пунктов (ровно 3, 4, 6 или 8).
  * bullet_list — обычный список тезисов без выраженных под-заголовков, или когда
    пунктов 1-2, 5, 7 или больше 8.
- Если кандидат один — используй его.

ЗАПОЛНЕНИЕ КОНТЕНТА (content):
- Заполняй ТОЛЬКО ключи из схемы выбранного макета. Никаких grid/EMU/blocks.
- ТЕКСТ СВЯЩЕНЕН: переноси формулировки ДОСЛОВНО и на языке оригинала. НЕ
  переводи, НЕ перефразируй, НЕ выдумывай факты, числа, заголовки и пункты.
- Для points.head делай КОРОТКИЙ заголовок (1-3 слова) из сути пункта; head и
  text бери из реального текста, не выдумывай.
- Для table/chart переноси значения из SLIDE_CONTENT ДОСЛОВНО (headers/rows/
  categories/series/values). data_provenance="estimated" только если числа сняты
  с растрового графика, иначе "native".
- Для timeline label — короткая метка (год/квартал/этап), text — одна строка.
- НЕ создавай пункты-заполнители. Если реального текста мало — меньше пунктов или
  bullet_list. Лучше меньше, чем выдуманное.
- accent (timeline) / accent_col (table) / accent_idx (chart) — необязательны;
  выдели максимум ОДИН ключевой элемент, иначе оставь по умолчанию.
"""


def _schema_block(layouts: list[str]) -> str:
    lines = [f'- "{name}": {_LAYOUT_SCHEMAS[name]}' for name in layouts
             if name in _LAYOUT_SCHEMAS]
    return "\n".join(lines)


def build_messages(stub: dict[str, Any], slide: dict[str, Any], archetype: str,
                   layouts: list[str]) -> list[dict[str, Any]]:
    tone_line = (
        'tone — "dark" для тёмных обложек/разделителей (cover_dark/section_divider '
        'при тёмной тональности), иначе "light"; для cover_green — "green".'
    )
    system = f"""\
Ты — дизайнер Cloud.ru 2.0. Тебе дан locked design-stub (НЕ менять), контент
одного слайда и СПИСОК доступных макетов-скелетов. Геометрию НЕ задаёшь —
скелет сам расставит всё по местам. Твоя задача: выбрать ОДИН макет и заполнить
его контент-словарь.

ДОСТУПНЫЕ МАКЕТЫ И ИХ СХЕМЫ КОНТЕНТА:
{_schema_block(layouts)}

{_GUIDANCE}

СХЕМА ВЫВОДА (Composition, строго; blocks НЕ заполняй):
{{
  "slide_num": <int>,
  "tone": "light|dark|green",
  "background": {{"kind":"white|graphite|green|dots"}},
  "layout": "<один из доступных макетов>",
  "content": {{ ... по схеме выбранного макета ... }}
}}
{tone_line}
Фон обычно white для светлых контент-слайдов, graphite для тёмных, green только
для cover_green. Скелет сам красит фон, но укажи согласованный kind.

{JSON_ONLY_FOOTER}
"""
    user = (
        f"LOCKED_STUB={json.dumps(stub, ensure_ascii=False)}\n"
        f"ARCHETYPE={archetype}\n"
        f"AVAILABLE_LAYOUTS={json.dumps(layouts, ensure_ascii=False)}\n"
        f"SLIDE_CONTENT={json.dumps(slide, ensure_ascii=False)}\n\n"
        "Выбери макет и верни Composition с layout и content."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
