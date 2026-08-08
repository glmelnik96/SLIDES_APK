"""Роль 3 — Vision-QA: PNG слайда + контент-бриф -> PASS либо список правок.

Запускается только на слайдах, проваливших линтер, и на всех freeform.
Бюджет: 1 круг autofix (держит оркестратор).
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from ..brand import brand_rules
from .client import KimiClient, image_part

QA_SYSTEM = """\
Ты — QA-ревьюер слайда презентации в бренде Cloud.ru 2.0.
Дан скриншот слайда и его контент-бриф. Верни ТОЛЬКО JSON:
{"passed": true, "fixes": []}
либо {"passed": false, "fixes": ["конкретная правка", "..."]}

Проверяй:
- переполнение/обрезку текста, наезды блоков друг на друга;
- читаемость и контраст; белый текст на зелёном — запрещён;
- больше одного зелёного акцента на слайде — запрещено;
- тени, градиенты, курсив, подчёркивания, скругления — запрещены;
- вылет контента за поля слайда;
- соответствие бренд-правилам ниже.

Если на слайде авто-схема (SVG: узлы со стрелками, воронка, круги) — её рисует
детерминированный движок: формы узлов (ромбы условий, пилюли начала/конца,
полупрозрачные круги), стрелки и раскладка легитимны и скруглениями/тенями НЕ
считаются. Замечания к схеме давай только про обрезанный или нечитаемый текст
в узлах — формулируй как «сократи текст узла X».

БРЕНД-ПРАВИЛА:
""" + brand_rules() + """
Правки формулируй как инструкции редактору контента (сократи X, убери Y)."""

# Описание палитры темы для ревьюера (судит контраст по правильным цветам).
_THEME_NOTE = ("Тема слайда: {theme}. Тёмная = графит #222 фон, белый текст, "
               "зелёный #26D07C акцент; светлая = белый фон, графит #222 текст, "
               "зелёный акцент (зелёный текст на белом запрещён).")


class QAVerdict(BaseModel):
    passed: bool
    fixes: list[str] = Field(default_factory=list)


def review_slide(client: KimiClient, png_path: str | Path, *, brief: str,
                 theme: str = "dark", max_tokens: int = 12288) -> QAVerdict:
    text = _THEME_NOTE.format(theme=theme) + f"\n\nКонтент-бриф слайда:\n{brief}"
    messages = [
        {"role": "system", "content": QA_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": text},
            image_part(png_path),
        ]},
    ]
    return client.chat_json(messages, QAVerdict, max_tokens=max_tokens)
