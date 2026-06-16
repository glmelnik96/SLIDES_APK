"""pixel_judge — VISION verdict on ONE rendered slide PNG (Kimi multimodal).

The judge looks at the actual rendered pixels (not the DSL) and flags concrete
visual defects so the composer can repair them. It is deliberately narrow: it
catches the failures the deterministic critic can't see — text overflowing or
clipped at edges, an almost-empty slide, blocks overlapping, content off-canvas,
illegible contrast. Brand-taste nuance is out of scope here.

Output validates against ``schemas.design.PixelVerdict``.
"""
from __future__ import annotations

import json
from typing import Any

from llm.client import build_vision_content
from llm.prompts._shared import JSON_ONLY_FOOTER

_SYSTEM = f"""\
Ты — визуальный контролёр качества слайдов Cloud.ru. Тебе показывают РЕНДЕР
одного слайда (PNG, 1280×720), иногда вместе с ЭТАЛОНОМ бренда (референс того,
как такой слайд должен выглядеть). Если есть эталон — сверяй общую структуру и
читаемость с ним, но НЕ требуй пиксельного совпадения (контент другой). Оцени
ТОЛЬКО то, что реально видно на картинке.

Ищи КОНКРЕТНЫЕ визуальные дефекты:
- текст обрезан краем слайда или вылезает за границы блока/канвы;
- текст налезает на другой текст или блоки перекрываются;
- слайд почти пустой (огромные пустоты, контент жмётся в угол);
- нечитаемый контраст (тёмный текст на тёмном фоне, светлый на светлом);
- заголовок/таблица/график выходят за нижний или правый край;
- дублирование или явный мусор (плейсхолдеры, обрезки слов).

НЕ придирайся к вкусовщине (оттенки, микро-отступы), если читаемость в порядке.
Брендовый каркас уже корректен — оценивай только перечисленные сбои.

Верни строго:
{{"ok": true|false, "issues": ["конкретный дефект и как починить", ...]}}
ok=true и пустой issues, если слайд читаем и без перечисленных дефектов.
Каждый issue — короткая инструкция на русском («заголовок обрезан справа —
сократи/уменьши», «слишком много пунктов, текст налезает — убери лишние»).

{JSON_ONLY_FOOTER}
"""


def build_messages(content: dict[str, Any], png: bytes,
                   reference: bytes | None = None) -> list[dict[str, Any]]:
    """Messages for a single-slide pixel verdict.

    ``png`` is the rendered slide. ``reference`` (optional) is the brand exemplar
    for this layout — when present it is shown FIRST as the reference, then the
    render to judge.
    """
    if reference is not None:
        text = (
            "Сначала ЭТАЛОН бренда, затем РЕНДЕР для оценки.\n"
            "Контент слайда (текст священен — не переписывай):\n"
            f"{json.dumps(content, ensure_ascii=False)}\n\n"
            "Сверь рендер с эталоном по структуре/читаемости и верни PixelVerdict."
        )
        images = [reference, png]
    else:
        text = (
            "Контент слайда (для сверки, текст священен — не переписывай):\n"
            f"{json.dumps(content, ensure_ascii=False)}\n\n"
            "Оцени рендер ниже и верни PixelVerdict."
        )
        images = [png]
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": build_vision_content(text, images)},
    ]
