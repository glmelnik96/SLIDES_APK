"""Стоимость прогона в рублях по токенам (прайс MiniMax).

Тариф провайдера (за 1 млн токенов):
  • входные (prompt)      — 240,22 ₽
  • генерируемые (output) — 1008,85 ₽

Значения можно переопределить через окружение (SLIDES_INPUT_RUB_PER_1M /
SLIDES_OUTPUT_RUB_PER_1M) — на случай смены прайса без передеплоя. Считаем в
рублях и округляем до копеек: это деньги, показываем «≈ 22,34 ₽».
"""
from __future__ import annotations

import os

_PER_MILLION = 1_000_000

INPUT_RUB_PER_1M: float = float(os.environ.get("SLIDES_INPUT_RUB_PER_1M", "240.22"))
OUTPUT_RUB_PER_1M: float = float(os.environ.get("SLIDES_OUTPUT_RUB_PER_1M", "1008.85"))


def cost_rub(prompt_tokens: int, completion_tokens: int) -> float:
    """Токены → рубли, округление до копеек (2 знака)."""
    rub = (
        prompt_tokens / _PER_MILLION * INPUT_RUB_PER_1M
        + completion_tokens / _PER_MILLION * OUTPUT_RUB_PER_1M
    )
    return round(rub, 2)
