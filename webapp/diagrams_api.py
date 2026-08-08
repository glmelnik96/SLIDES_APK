"""Каталог типов диаграмм для UI-пикера редактора.

Тонкая прослойка над htmlslides.diagrams: отдаёт карточки «большого блока
выбора» (включая будущие волны с пометкой «скоро») и собирает контент
однослайдовой деки-превью конкретного типа — тем же движком, что и боевой
рендер, чтобы превью 1:1 совпадало с результатом.
"""
from __future__ import annotations

from htmlslides.diagrams import (AVAILABLE_KINDS, catalog_for_ui, get_type,
                                 sample_spec, spec_json)


def catalog() -> list[dict]:
    """Карточки всех типов для пикера (kind, рус. имя, «когда брать», доступность)."""
    return catalog_for_ui()


def preview_content(kind: str) -> dict:
    """Контент слайда-превью для типа диаграммы. KeyError на неизвестном или
    ещё не реализованном kind — роут превращает его в 404."""
    t = get_type(kind)                       # KeyError: неизвестный kind
    if kind not in AVAILABLE_KINDS:
        raise KeyError(f"diagram kind not implemented yet: {kind}")
    # when_to_use в subtitle не кладём: описание длиннее слот-капа (70) и уже
    # написано на карточке пикера — в превью шапка только с именем типа.
    return {"title": t.display_name,
            "diagram": spec_json(sample_spec(kind))}
