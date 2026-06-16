"""PPTX -> InputDoc: слайд = секция; текст-фреймы, таблицы, картинки."""
from __future__ import annotations

from pathlib import Path

from pptx import Presentation
from pptx.enum.shapes import MSO_SHAPE_TYPE

from .base import ImageBlock, InputDoc, ListBlock, Section, TableBlock, TextBlock


def parse_pptx(path: str | Path) -> InputDoc:
    prs = Presentation(str(path))
    doc = InputDoc()
    for number, slide in enumerate(prs.slides, start=1):
        title_shape = slide.shapes.title
        heading = (title_shape.text or "").strip() if title_shape is not None else ""
        if number == 1 and heading and not doc.title:
            doc.title = heading
        section = Section(heading=heading or f"Слайд {number}", level=1)
        for shape in slide.shapes:
            if title_shape is not None and shape.shape_id == title_shape.shape_id:
                continue
            if getattr(shape, "has_table", False):
                section.blocks.append(TableBlock(
                    rows=[[cell.text.strip() for cell in row.cells]
                          for row in shape.table.rows]))
                continue
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                image = shape.image
                section.blocks.append(
                    ImageBlock(data=image.blob, mime=image.content_type))
                continue
            if shape.has_text_frame:
                paragraphs = [p.text.strip()
                              for p in shape.text_frame.paragraphs if p.text.strip()]
                if len(paragraphs) > 1:
                    section.blocks.append(ListBlock(items=paragraphs))
                elif paragraphs:
                    section.blocks.append(TextBlock(text=paragraphs[0]))
        doc.sections.append(section)
    return doc
