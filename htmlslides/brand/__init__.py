"""Бренд-правила Cloud.ru 2.0 (do/don't из гайдбука) для инъекции в промпты LLM.

rules.md — единственный источник правды; читается из пакета через
importlib.resources, кэшируется. Как fonts.py/assembler грузят упакованные ассеты.
"""
from __future__ import annotations

from functools import lru_cache
from importlib import resources


@lru_cache(maxsize=1)
def brand_rules() -> str:
    """Текст бренд-правил (Russian, ≤1500 символов). Один объект на процесс."""
    return (resources.files("htmlslides") / "brand" / "rules.md").read_text("utf-8")
