#!/usr/bin/env python3
"""
effects_util.py — единый, НАДЁЖНЫЙ съём любых эффектов (теней/glow/reflection/
softEdge) с фигуры. Бренд Cloud.ru: плоско, эффектов нет ВООБЩЕ (user 2026-06-02).

Зачем отдельный модуль: раньше каждый рендер снимал только `effectLst`/`effectDag`
из `spPr`, но НЕ трогал тематическую ссылку `<p:style><a:effectRef>` — а именно она
на дефолтном шаблоне даёт «фантомную» тень даже после очистки spPr. Теперь снимаем
обе: и явные эффекты, и effectRef (idx=0). Это убирает эффекты на ЛЮБОМ канвасе.

effectRef idx=0 трогает ТОЛЬКО эффект; fillRef/lnRef/fontRef остаются — поэтому
безопасно и для clone-доноров (их заливка/шрифт из темы не меняются)."""
from pptx.oxml.ns import qn

_EFFECT_TAGS = (
    "effectLst", "effectDag",
    "outerShdw", "innerShdw", "prstShdw",
    "glow", "reflection", "softEdge",
)


def strip_effects(el):
    """Снять все эффекты с элемента фигуры (`shape._element`). Возвращает True,
    если что-то изменилось. Обрабатывает spPr/grpSpPr + p:style/effectRef."""
    changed = False
    for sppr_tag in ("p:spPr", "p:grpSpPr"):
        spPr = el.find(qn(sppr_tag))
        if spPr is None:
            continue
        for tag in _EFFECT_TAGS:
            for e in spPr.findall(qn(f"a:{tag}")):
                spPr.remove(e)
                changed = True
    style = el.find(qn("p:style"))
    if style is not None:
        eref = style.find(qn("a:effectRef"))
        if eref is not None:
            if eref.get("idx") != "0":
                eref.set("idx", "0")
                changed = True
            for child in list(eref):       # убрать вложенный цвет эффекта
                eref.remove(child)
                changed = True
    return changed


def strip_effects_recursive(shapes):
    """Рекурсивно снять эффекты со всех фигур (вкл. группы). Возвращает счётчик."""
    n = 0
    for sh in shapes:
        if strip_effects(sh._element):
            n += 1
        if getattr(sh, "shape_type", None) == 6:  # GROUP
            try:
                n += strip_effects_recursive(sh.shapes)
            except Exception:
                pass
    return n
