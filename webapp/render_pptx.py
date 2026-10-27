"""Сохранённая дека -> .pptx.

Основной путь — редактируемый PPTX (htmlslides.topptx): текст, плашки, линии и
SVG становятся родными объектами PowerPoint, и правят их в PowerPoint как свои.

Схемы (шаблон diagram) рисует engine/diagram.js уже в браузере: в разметке деки
у .diagram-host только JSON, и конвертеру переносить нечего — слайд вышел бы с
одним заголовком. Поэтому область схемы снимаем браузером и кладём картинкой
поверх собранного слайда; заголовок и подзаголовок остаются текстом.

Запасной путь — прежний экспорт картинками: каждый слайд одним PNG во весь
кадр. Им отдаём файл, если редактируемый не собрался, — человек получает
презентацию в любом случае, а причина уходит в лог.
"""
from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path

from webapp import render_png

_LOG = logging.getLogger(__name__)

# 16:9 at PowerPoint's EMU scale (914400 EMU = 1 inch). 13.333in x 7.5in is the
# canonical widescreen slide; the 1920x1080 PNG shares that aspect, so a full-bleed
# placement neither letterboxes nor distorts.
_EMU_PER_INCH = 914400
_SLIDE_W = int(13.333 * _EMU_PER_INCH)
_SLIDE_H = int(7.5 * _EMU_PER_INCH)

# Слайдовый пиксель в EMU — тот же масштаб, что у конвертера (12192000 / 1920).
_EMU_PX = 6350
# Область схемы, px: .diagram-host в deck.css (left 60, top 300, 1800×720).
_DIAGRAM_BOX = (60, 300, 1800, 720)


def build_pptx(pngs: dict[int, Path], out_pptx: Path) -> Path:
    from pptx import Presentation
    from pptx.util import Emu

    prs = Presentation()
    prs.slide_width = Emu(_SLIDE_W)
    prs.slide_height = Emu(_SLIDE_H)
    blank = prs.slide_layouts[6]  # the built-in "Blank" layout — no placeholders
    for index in sorted(pngs):
        slide = prs.slides.add_slide(blank)
        slide.shapes.add_picture(str(pngs[index]), 0, 0,
                                 width=Emu(_SLIDE_W), height=Emu(_SLIDE_H))
    prs.save(str(out_pptx))
    return out_pptx


def export_pictures(deck_html: Path, out_pptx: Path,
                    pngs: dict[int, Path] | None = None) -> Path:
    """PPTX картинками: слайд = один PNG во весь кадр. Выглядит 1-в-1, но не правится."""
    if pngs is None:
        pngs = render_png.render_slides(deck_html)
    return build_pptx(pngs, out_pptx)


def _diagram_slides(deck_html: Path) -> list[int]:
    """Номера слайдов-схем (с 1) — тех, чью картинку рисует diagram.js."""
    from lxml import html as LH

    sections = LH.parse(str(deck_html)).xpath('//section[contains(@class,"slide")]')
    return [i for i, s in enumerate(sections, start=1)
            if s.get("data-template") == "diagram"]


def _paste_diagrams(deck_html: Path, out_pptx: Path, indices: list[int]) -> None:
    """Положить снятые браузером схемы поверх слайдов готового PPTX."""
    from PIL import Image
    from pptx import Presentation
    from pptx.util import Emu

    x, y, w, h = _DIAGRAM_BOX
    prs = Presentation(str(out_pptx))
    with tempfile.TemporaryDirectory(prefix="pptx-diagrams-") as tmp:
        shots = render_png._screenshot_slides(deck_html, indices, Path(tmp),
                                              viewport=(1920, 1080))
        for i in indices:
            buf = io.BytesIO()
            with Image.open(shots[i]) as im:
                im.crop((x, y, x + w, y + h)).save(buf, format="PNG")
            buf.seek(0)
            pic = prs.slides[i - 1].shapes.add_picture(
                buf, Emu(x * _EMU_PX), Emu(y * _EMU_PX),
                Emu(w * _EMU_PX), Emu(h * _EMU_PX))
            pic.name = "Схема"
    prs.save(str(out_pptx))


def export_pptx(deck_html: Path, out_pptx: Path) -> Path:
    """Редактируемый PPTX; не собрался — картинками, как раньше."""
    from htmlslides.topptx.convert import convert

    try:
        convert(deck_html, out_pptx)
        diagrams = _diagram_slides(deck_html)
        if diagrams:
            _paste_diagrams(deck_html, out_pptx, diagrams)
        return out_pptx
    except Exception:  # noqa: BLE001 — файл человек получит в любом случае
        _LOG.warning("pptx: редактируемый экспорт %s не собрался — отдаём "
                     "картинками", deck_html, exc_info=True)
        return export_pictures(deck_html, out_pptx)
