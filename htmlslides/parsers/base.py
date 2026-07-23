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
    notes: str = ""                  # заметки докладчика (pptx): контекст для
    #                                  планировщика; в контент слайда НЕ идут
    #                                  (иначе утекут в режиме «Точный перенос»)


class InputDoc(BaseModel):
    title: str = ""
    sections: list[Section] = Field(default_factory=list)


def decode_smart(data: bytes) -> str:
    """Декодировать байты текстового документа, угадывая кодировку.

    Реальные .md/.txt приходят не только в UTF-8: на Windows это сплошь и рядом
    Windows-1251 или UTF-16 («Юникод» в Блокноте). Декодирование как UTF-8
    роняло весь билд (UnicodeDecodeError). Пробуем осмысленную цепочку:
    UTF-16 при наличии BOM → UTF-8 (в т.ч. с BOM) → Windows-1251. cp1251
    принимает почти любой байт, поэтому финальный replace — лишь страховка от
    неопределённых в нём позиций, чтобы парсер никогда не падал на входе.
    """
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_text_smart(path: str | Path) -> str:
    """Прочитать текстовый документ с диска, угадывая кодировку (см. decode_smart)."""
    return decode_smart(Path(path).read_bytes())


def parse_file(path: str | Path) -> InputDoc:
    """Диспетчер по расширению: .md/.markdown/.txt, .docx, .pptx."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".md", ".markdown", ".txt"):
        from .md import parse_md
        return parse_md(read_text_smart(p))
    if suffix == ".docx":
        from .docx import parse_docx
        return parse_docx(p)
    if suffix == ".pptx":
        from .pptx import parse_pptx
        return parse_pptx(p)
    raise ValueError(f"unsupported input format: {suffix}")
