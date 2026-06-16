"""Нормализованная структура входного документа (контракт parsers -> planner)."""
from __future__ import annotations

import base64
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field, field_serializer, field_validator


class TextBlock(BaseModel):
    kind: Literal["text"] = "text"
    text: str


class ListBlock(BaseModel):
    kind: Literal["list"] = "list"
    items: list[str]
    ordered: bool = False


class TableBlock(BaseModel):
    kind: Literal["table"] = "table"
    rows: list[list[str]]            # первая строка может быть шапкой


class ImageBlock(BaseModel):
    kind: Literal["image"] = "image"
    src: Optional[str] = None        # путь/URL (md)
    data: Optional[bytes] = None     # бинарь (docx/pptx); в JSON — base64
    mime: str = ""
    alt: str = ""

    @field_validator("data", mode="before")
    @classmethod
    def _data_from_base64(cls, v):
        return base64.b64decode(v) if isinstance(v, str) else v

    @field_serializer("data", when_used="json")
    def _data_to_base64(self, v: Optional[bytes]) -> Optional[str]:
        return None if v is None else base64.b64encode(v).decode("ascii")


class CodeBlock(BaseModel):
    kind: Literal["code"] = "code"
    text: str
    lang: str = ""


Block = Annotated[
    Union[TextBlock, ListBlock, TableBlock, ImageBlock, CodeBlock],
    Field(discriminator="kind"),
]


class Section(BaseModel):
    heading: str = ""
    level: int = 0                   # 0 = преамбула без заголовка
    blocks: list[Block] = Field(default_factory=list)


class InputDoc(BaseModel):
    title: str = ""
    sections: list[Section] = Field(default_factory=list)


def parse_file(path: str | Path) -> InputDoc:
    """Диспетчер по расширению: .md/.markdown/.txt, .docx, .pptx."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".md", ".markdown", ".txt"):
        from .md import parse_md
        return parse_md(p.read_text("utf-8-sig"))
    if suffix == ".docx":
        from .docx import parse_docx
        return parse_docx(p)
    if suffix == ".pptx":
        from .pptx import parse_pptx
        return parse_pptx(p)
    raise ValueError(f"unsupported input format: {suffix}")
