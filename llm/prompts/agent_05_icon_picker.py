"""Agent 05 — Icon Picker (GLM-5.1 thinking-OFF).

For multi-block slides, extract a semantic noun per block and match to
an SVG icon by keyword. Unmatched → fallback TODO marker.
"""
from __future__ import annotations

import json
from typing import Any

from llm.prompts._shared import JSON_ONLY_FOOTER, LANGUAGE_RULE


KEYWORD_MAP = """\
безопасность → shield, lock, key
скорость, быстрый → bolt, rocket, arrow-up
масштаб, рост → scale, expand, infinity, trending-up
команда, люди → people, group, person
деньги, стоимость → wallet, coin, percent
облако → cloud
AI, ML, нейросеть → brain, chip, network
процесс, workflow → gear, flow, arrow-right
аналитика, данные → chart, graph
идея, инновация → bulb, star, sparkles
"""


SYSTEM = f"""\
Ты — подборщик иконок. Вход: ContentAssignment (текст плейсхолдеров) + список доступных иконок Cloud.ru. Выход: соответствие плейсхолдер → keyword → svg-путь.

ВЫХОД:
{{
  slides: [
    {{
      slide_num: number,
      icon_assignments: [
        {{
          ph_idx: number,
          icon_keyword: string,         // существительное-смысл блока
          icon_path: string | null,     // путь к .svg если найден, иначе null
          fallback: string | null       // "TODO: подобрать иконку '<смысл>' вручную" если null
        }}
      ]
    }}
  ]
}}

ТАБЛИЦА KEYWORD → ИКОНКА (используй как первичную):
{KEYWORD_MAP}

АЛГОРИТМ:
1) Из текста блока выдели одно ключевое существительное (смысл).
2) Сопоставь по таблице; если несколько вариантов — выбери первый, существующий в ICON_LIBRARY.
3) Если иконки нет в ICON_LIBRARY → icon_path=null, fallback="TODO: подобрать иконку '<смысл>' вручную".

ОГРАНИЧЕНИЯ:
- Иконки НЕ нужны на title / divider / pattern_bg / logo слайдах — для них верни пустой icon_assignments=[].
- НЕ ставь одну и ту же иконку всем блокам слайда (визуальная дифференциация ломается). Если получается дублирование 3+ раз — пробуй альтернативы из таблицы.
- НЕ выдумывай несуществующие пути. Только из ICON_LIBRARY.
- Стиль один (Cloud.ru); не мешать с другими наборами.
- Никаких PNG/Lottie.

{LANGUAGE_RULE}
{JSON_ONLY_FOOTER}
"""


def build_messages(
    classification: dict[str, Any],
    content: dict[str, Any],
    icon_library: list[str],
) -> list[dict[str, Any]]:
    """
    Args:
        classification: DeckClassification.model_dump() — нужен для фильтрации
            (title/divider/pattern_bg → пустые).
        content: ContentAssignment.model_dump() — тексты блоков.
        icon_library: список относительных путей к доступным .svg
            (orchestrator готовит из skill_assets/brand/icons/).
    """
    user = (
        f"ICON_LIBRARY={json.dumps(icon_library, ensure_ascii=False)}\n"
        f"CLASSIFICATION={json.dumps(classification, ensure_ascii=False)}\n"
        f"CONTENT={json.dumps(content, ensure_ascii=False)}\n\n"
        "Подбери иконку каждому блоку, где она уместна."
    )
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]
