"""DeckPlan → спецификация Figma Design (фреймы 1920×1080).

Сервис не вызывает Plugin API сам: отдаёт JSON, из которого агент/плагин
собирает design-файл. Источник языка — слайдовый гайд
https://www.figma.com/design/oawKNdlwcvb2jNikd60tC8
"""
from __future__ import annotations

from typing import Any

from .models import DeckPlan, SlidePlan

GUIDE_URL = (
    "https://www.figma.com/design/oawKNdlwcvb2jNikd60tC8/"
    "%D0%A1%D0%BB%D0%B0%D0%B9%D0%B4%D0%BE%D0%B2%D1%8B%D0%B9-%D0%B3%D0%B0%D0%B9%D0%B4"
    "?node-id=85-102"
)
GUIDE_FILE_KEY = "oawKNdlwcvb2jNikd60tC8"
MASTERS_FILE_KEY = "twHT0n7CSexQnHnYQZ4u0a"
MASTERS_URL = "https://www.figma.com/design/twHT0n7CSexQnHnYQZ4u0a/E-course-masters-SLIDES_APK"

SLIDE_W, SLIDE_H = 1920, 1080

TOKENS = {
    "green": "#26D07C",
    "graphite": "#222222",
    "white": "#FFFFFF",
    "grey1": "#F2F2F2",
    "carrot": "#FF4517",
    "font": "SB Sans Display",
}


def _hex_rgb(hex_color: str) -> dict[str, float]:
    h = hex_color.lstrip("#")
    return {
        "r": int(h[0:2], 16) / 255,
        "g": int(h[2:4], 16) / 255,
        "b": int(h[4:6], 16) / 255,
    }


def _text_layer(name: str, characters: str, *, x: int, y: int, w: int,
                size: int, color: str, weight: str = "SemiBold") -> dict[str, Any]:
    return {
        "type": "TEXT",
        "name": name,
        "characters": characters,
        "x": x, "y": y, "width": w,
        "fontSize": size,
        "fontWeight": weight,
        "fills": [_hex_rgb(color)],
    }


def _frame_for_slide(slide: SlidePlan, *, theme: str) -> dict[str, Any]:
    bg = TOKENS["graphite"] if theme == "dark" else TOKENS["white"]
    fg = TOKENS["white"] if theme == "dark" else TOKENS["graphite"]
    content = slide.content or {}
    title = (
        str(content.get("title") or content.get("label")
            or content.get("heading") or "")
    )
    layers: list[dict[str, Any]] = [
        _text_layer("chrome.ru", "cloud.ru", x=60, y=62, w=400, size=30, color=fg),
        _text_layer("title", title or f"Слайд {slide.index}",
                    x=60, y=180, w=1800, size=54, color=fg),
    ]
    tid = slide.template_id or "blank"
    if tid == "course-toc":
        y = 360
        for i, item in enumerate(content.get("items") or [], start=1):
            label = item.get("label", "") if isinstance(item, dict) else str(item)
            layers.append(_text_layer(
                f"item-{i}", f"{i:02d}  {label}",
                x=60, y=y, w=1700, size=30, color=fg, weight="Regular"))
            y += 56
    elif tid == "course-quiz":
        y = 360
        for i, opt in enumerate(content.get("options") or [], start=1):
            text = opt.get("text", "") if isinstance(opt, dict) else str(opt)
            mark = (opt.get("mark") or "") if isinstance(opt, dict) else ""
            color = TOKENS["green"] if mark == "ok" else (
                TOKENS["carrot"] if mark == "bad" else fg)
            layers.append(_text_layer(
                f"opt-{i}", f"{'ABCD'[i-1] if i <= 4 else i}  {text}",
                x=60, y=y, w=1700, size=30, color=color, weight="Regular"))
            y += 72
    elif tid == "course-section":
        layers.append(_text_layer(
            "nav", "  ·  ".join(
                (s.get("label") if isinstance(s, dict) else str(s))
                for s in (content.get("sections") or [])
            ),
            x=60, y=1000, w=1800, size=18, color=TOKENS["green"], weight="Regular"))
    else:
        subtitle = str(content.get("subtitle") or content.get("lead") or "")
        if subtitle:
            layers.append(_text_layer(
                "subtitle", subtitle, x=60, y=280, w=1800, size=30,
                color=fg, weight="Regular"))
    return {
        "name": f"{slide.index:02d} {tid}",
        "template_id": tid,
        "width": SLIDE_W,
        "height": SLIDE_H,
        "background": _hex_rgb(bg),
        "layers": layers,
    }


def deck_to_figma_spec(plan: DeckPlan, *, theme: str = "dark") -> dict[str, Any]:
    """JSON, достаточный чтобы собрать design-файл 1 слайд = 1 FRAME."""
    return {
        "version": 1,
        "editorType": "design",
        "guideUrl": GUIDE_URL,
        "guideFileKey": GUIDE_FILE_KEY,
        "mastersFileKey": MASTERS_FILE_KEY,
        "mastersUrl": MASTERS_URL,
        "pageSize": {"width": SLIDE_W, "height": SLIDE_H},
        "tokens": TOKENS,
        "title": plan.title or "Курс",
        "theme": theme,
        "slides": [_frame_for_slide(s, theme=theme) for s in plan.slides],
    }
