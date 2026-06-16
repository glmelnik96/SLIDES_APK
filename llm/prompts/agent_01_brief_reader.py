"""Agent 01 — Brief Reader (Kimi-K2.6 vision).

Reads parsed draft (text from .pptx/.docx/.md) + optional rendered PNGs
and emits a `Brief` (schemas.slides.Brief): topic, audience, tone, per-slide
intent and key_phrase.

WS-E directives applied:
- Kimi vision: "describe what you SEE, then JSON" — vision needs grounding
  before commitment (per prompt_adaptation.md).
- Compact schema as TypeScript-like signature, no role-play preamble.
- Explicit no-prose terminator.
"""
from __future__ import annotations

import json
from typing import Any

from llm.client import VisionImage
from llm.prompts._shared import JSON_ONLY_FOOTER, LANGUAGE_RULE


SYSTEM = f"""\
Ты — экстрактор брифа. Читаешь сырое содержимое черновика презентации (текст и опционально рендеры слайдов в PNG) и выдаёшь СТРУКТУРИРОВАННЫЙ JSON. Не редактируешь текст, не добавляешь содержимое, не даёшь дизайн-советов.

ВЫХОДНАЯ СХЕМА:
{{
  topic: string,                    // 5–7 слов, тема всей колоды
  audience: "executives"|"engineers"|"sales"|"clients"|"investors"|"unknown",
  tone: "formal"|"informal"|"analytical"|"sales"|"unknown",
  slide_count: number,
  key_messages: string[],           // 3–7 коротких тезисов всей колоды
  has_numbers: boolean,             // есть ли KPI/проценты/деньги/даты
  has_quotes: boolean,
  has_team: boolean,
  has_timeline: boolean,
  slides: [
    {{
      num: number,                   // 1-based
      raw_title: string|null,
      raw_body: string[],            // буллеты/абзацы как пришли, без редактуры
      intent: "title"|"divider"|"text"|"comparison"|"timeline"|"team"|"data"|"image"|"callout"|"schema"|"chart"|"table",
      key_phrase: string,            // 5–7 слов, главная мысль слайда
      elements_count: number,        // буллеты+картинки+таблицы+KPI
      needs_visual: boolean          // нужен рендер/инфографика
    }}
  ]
}}

ПРАВИЛО ТИТУЛЬНОГО СЛАЙДА (intent=title):
- raw_title — это НАЗВАНИЕ продукта/презентации, НЕ дата/событие/площадка.
- Если самая крупная строка — дата или название мероприятия ("Cloud Tech Day · 9 Июня 2026"), помести её в raw_body, а raw_title возьми из строки с названием продукта.

ПРАВИЛА КЛАССИФИКАЦИИ intent:
- title — первый слайд, короткое название продукта/презентации
- divider — 1–3 слова, разделитель раздела
- text — заголовок + 1–2 абзаца/буллета
- comparison — "vs/против/до-после", две колонки данных
- timeline — даты, "этап 1/2/3", процесс шагами
- team — ФИО + роли
- data — KPI, %, деньги, числа
- image — иллюстрация доминирует, текста минимум
- callout — одна короткая фраза, акцент-блок
- schema/chart/table — диаграмма/график/таблица как основное содержимое

ОГРАНИЧЕНИЯ:
- Запрещено выдумывать содержание. Если поле непонятно — ставь "unknown" или []/null.
- raw_title и raw_body — оригинал, без правок.
- key_messages извлекай ТОЛЬКО из заголовков и первых буллетов слайдов.
- Если данных мало для определения audience/tone — "unknown".

ПОРЯДОК РАССУЖДЕНИЯ (только в reasoning-трейсе модели, НЕ в content):
1. Кратко опиши, что видишь на каждом слайде (если есть PNG).
2. Сопоставь с распарсенным текстом.
3. Только потом сформируй JSON.

{LANGUAGE_RULE}
{JSON_ONLY_FOOTER}
"""


def build_messages(
    parsed_deck: dict[str, Any],
    images: list[VisionImage] | None = None,
) -> tuple[list[dict[str, Any]], list[VisionImage]]:
    """Compose messages for Agent 01.

    Args:
        parsed_deck: output of `parse_pptx`/`parse_md`/`parse_docx`, the
            ParsedDeck shape (slides[].title/body/text_runs/...).
        images: optional list of rendered slide PNGs (bytes / Path / data-URL).
            Brief Reader runs vision-grounded when present.

    Returns:
        (messages, images) — pass straight into ``LLMCall(messages=..., images=...)``.
    """
    images = list(images or [])
    deck_json = json.dumps(parsed_deck, ensure_ascii=False, indent=None)

    user_text = (
        "ВХОД: распарсенный черновик (JSON ниже). "
        "Если приложены PNG-рендеры слайдов — сверь визуальное содержимое "
        "со структурой текста (картинки, KPI, схемы могут не отражаться в тексте).\n\n"
        f"PARSED_DECK={deck_json}\n\n"
        "ВЕРНИ: Brief по схеме из SYSTEM."
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user_text},
    ]
    return messages, images
