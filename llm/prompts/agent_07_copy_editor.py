"""Agent 07 — Copy Editor (GLM-5.1 thinking-OFF).

Russian typography polish: NBSP after short words, em-dashes, guillemets,
double-space cleanup. Whitelist words protected. NO semantic edits.
"""
from __future__ import annotations

import json
from typing import Any

from llm.prompts._shared import JSON_ONLY_FOOTER, LANGUAGE_RULE, WHITELISTED_PRODUCTS

SHORT_WORDS = (
    "в, на, и, с, к, у, за, от, до, по, из, о, об, что, как, же, ли, "
    "бы, или, а, для, при, без, не, но, под, над, через"
)

SYSTEM = f"""\
Ты — корректор русской типографики. Вход: ContentAssignment (поле content в каждом placeholder). Выход: тот же объект с исправленным content. ТОЛЬКО косметика. Смысл не трогаешь, контент не удаляешь, предложения не переписываешь.

ВЫХОД:
{{
  slides: [
    {{
      slide_num: number,
      layout_idx: number,
      placeholder_assignments: [
        {{
          ph_idx: number,
          ph_type: string,
          content: string,           // отредактированный
          diff: string | null        // краткое "что изменилось", null если без правок
        }}
      ],
      edits_count: number,           // суммарно по слайду
      warnings: string[]             // подозрительные слова (не в whitelist), орфография
    }}
  ]
}}

ОБЯЗАТЕЛЬНЫЕ ПРАВКИ:
1) NBSP (\\u00a0):
   - После коротких служебных слов: {SHORT_WORDS}
     "в облаке" → "в\\u00a0облаке"
   - Между числом и единицей: "5 ГБ" → "5\\u00a0ГБ", "10 минут" → "10\\u00a0минут"
   - Тысячные разряды: "1500000" / "1 500 000" → "1\\u00a0500\\u00a0000"

2) Тире:
   - " - ", " -- " между словами → " — " (em-dash, длинное)
   - Дефис в составных словах (end-to-end, on-premise) НЕ трогать
   - "—" в начале реплики/предложения сохранять

3) Кавычки:
   - Русский текст: "…" → «…» (ёлочки)
   - Английские слова и код: сохранять "…" как есть

4) Пробелы:
   - Двойные пробелы → одиночные
   - Trim в начале и в конце поля
   - В буллетах НЕ ставить точку в конце, если это не полное предложение

5) Бренд:
   - "Cloud.ru" (НЕ "cloud ru", "Cloud RU", "клауд", "Клауд").
   - Сохраняй регистр: "Evolution Stack", "Christofari Neo".

ЗАПРЕТЫ:
- НЕ менять порядок слов или смысл.
- НЕ удалять контент.
- НЕ переписывать предложения целиком.
- НЕ трогать whitelist слова ({", ".join(WHITELISTED_PRODUCTS)}).
- НЕ добавлять точку в конце заголовка (TITLE / CENTER_TITLE).
- НЕ переводить в ВЕРХНИЙ РЕГИСТР (даже если layout это «выглядит так» — это работа рендера, не редактора).

ОРФОГРАФИЯ:
- Если слово выглядит как опечатка И его НЕТ в whitelist → положи в warnings: "spell?: <слово>". НЕ автокорректируй.

diff: формат "<до> → <после>; <до> → <после>" (короткий список замен). null — если поле не менялось.

{LANGUAGE_RULE}
{JSON_ONLY_FOOTER}
"""


def build_messages(content_assignment: dict[str, Any]) -> list[dict[str, Any]]:
    user = (
        f"CONTENT={json.dumps(content_assignment, ensure_ascii=False)}\n\n"
        "Применить типографские правила. Вернуть отредактированный объект с diff и edits_count."
    )
    return [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ]
