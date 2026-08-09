"""Платформа диаграмм: данные-как-истина, детерминированный SVG-рендер.

Принцип (доказан типизированными слайдами и SVG-графиками): LLM/пользователь
отдаёт СТРУКТУРУ (узлы/связи/значения) по строгой Pydantic-схеме, а рендерит её
детерминированный движок engine/diagram.js на дизайн-токенах темы. Гибридная
раскладка: авто-layout по типу + ручные сдвиги узлов (offsets) в самих данных.

Модули:
- schema.py  — Pydantic-контракт DiagramSpec (discriminated union по kind);
- catalog.py — каталог типов с метаданными для UI-пикера и LLM-промпта.
"""
from .catalog import AVAILABLE_KINDS, catalog_for_ui, get_type, sample_spec
from .schema import (CANVAS_H, CANVAS_W, DiagramValidationError, parse_diagram,
                     spec_json, validate_diagram)

__all__ = [
    "AVAILABLE_KINDS", "catalog_for_ui", "get_type", "sample_spec",
    "CANVAS_H", "CANVAS_W", "DiagramValidationError", "parse_diagram",
    "spec_json", "validate_diagram",
]
