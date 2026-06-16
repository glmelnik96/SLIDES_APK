"""Composition DSL — relative-grid description of a from-scratch designed slide.

This is the contract the LLM `slide_composer` emits and `native_assembler`
consumes. The LLM never touches EMU or placeholder indices; it places blocks
on a 12x10 grid. The assembler converts grid cells to EMU and draws native
python-pptx shapes.

q2 prototype: intentionally minimal but real. Proves the DSL -> native-shapes
path renders a clean, on-brand, structurally-valid slide deterministically.
"""
from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

# 12 columns x 10 rows over the 1280x720 canvas (matches _shared.CANVAS_PX).
GRID_COLS = 12
GRID_ROWS = 10


class Grid(BaseModel):
    """A rectangular span on the 12x10 grid. c,r are 1-based top-left."""
    model_config = ConfigDict(extra="forbid")
    c: int = Field(ge=1, le=GRID_COLS)
    r: int = Field(ge=1, le=GRID_ROWS)
    cs: int = Field(ge=1, le=GRID_COLS)
    rs: int = Field(ge=1, le=GRID_ROWS)


class TitleBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["title"] = "title"
    text: str
    grid: Grid
    size_pt: int = 44
    accent_underline: bool = True


class BodyBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["body"] = "body"
    bullets: list[str]
    grid: Grid
    size_pt: int = 16


class KpiBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["kpi"] = "kpi"
    num: str           # e.g. "+47%"
    desc: str          # e.g. "рост ARR год к году"
    grid: Grid


class ChartSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    values: list[float]


class ChartBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["chart"] = "chart"
    chart_type: Literal["bar", "hbar", "pie", "line", "area", "area_100"] = "bar"
    categories: list[str]
    series: list[ChartSeries]
    grid: Grid
    # Index of the series that gets the ONE green accent; others get pastel.
    accent_idx: int = 0
    # "native"  → values came from a real source chart (use verbatim).
    # "estimated" → values were OCR'd/read off a raster chart; render a
    #               "оценка по графику" footnote so a report never lies.
    data_provenance: Literal["native", "estimated"] = "native"


class TableBlock(BaseModel):
    """A native (Excel-/PowerPoint-editable) zebra table.

    Mirrors the template's slide-56 zebra style: white header row with SemiBold
    graphite text, body rows alternating gray/white, thin vertical separators.
    One column MAY be tinted (``accent_col``) to highlight it — a brand TINT,
    not green (matches the template's single blue column on slide 52). The green
    one-accent rule does not apply to data tables.
    """
    model_config = ConfigDict(extra="forbid")
    role: Literal["table"] = "table"
    headers: list[str]
    rows: list[list[str]]
    grid: Grid
    first_col_wider: bool = True        # widen the first (label) column 1.4x
    accent_col: int | None = None       # index of the ONE tinted column, if any


class NodeBlock(BaseModel):
    """A single labelled box in a flow/architecture diagram."""
    model_config = ConfigDict(extra="forbid")
    role: Literal["node"] = "node"
    text: str
    grid: Grid
    accent: bool = False  # the ONE node that may carry the green plashka


class ConnectorBlock(BaseModel):
    """A directed arrow between two grid cells (by node index in the slide)."""
    model_config = ConfigDict(extra="forbid")
    role: Literal["connector"] = "connector"
    src: int            # index of source NodeBlock within the slide's nodes
    dst: int            # index of destination NodeBlock
    rhombus: bool = False  # arrow seated on a 45° green rhombus backing


class CardBlock(BaseModel):
    """A team-member or comparison card: heading + sub + optional plate."""
    model_config = ConfigDict(extra="forbid")
    role: Literal["card"] = "card"
    heading: str        # name / column title
    sub: str = ""       # role / column value
    grid: Grid
    plate: bool = True  # gray/portal-square backing plate behind the card
    accent: bool = False


class MilestoneBlock(BaseModel):
    """One point on a horizontal timeline axis."""
    model_config = ConfigDict(extra="forbid")
    role: Literal["milestone"] = "milestone"
    label: str          # short marker, e.g. a year
    text: str = ""      # one-line description under the tick
    grid: Grid
    accent: bool = False


class DecorBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["decor"] = "decor"
    kind: Literal["sparkle", "outline_corner", "portal"]
    anchor: Literal["top_left", "top_right", "bottom_left", "bottom_right"]
    density: Literal["low", "med"] = "low"
    # portal-only: number of staircase squares and which corner it grows from.
    portal_squares: int = 3


Block = Union[
    TitleBlock, BodyBlock, KpiBlock, ChartBlock, TableBlock,
    NodeBlock, ConnectorBlock, CardBlock, MilestoneBlock, DecorBlock,
]


class Background(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["white", "graphite", "green", "dots"] = "white"


class Composition(BaseModel):
    """One designed slide.

    Two rendering modes:
      * Skeleton mode (preferred) — set ``layout`` to an archetype name and
        ``content`` to its content dict; the assembler dispatches to
        ``renderers.designer.layouts`` which owns the whole slide. ``blocks``
        is then ignored.
      * Free mode (legacy) — leave ``layout`` None and place ``blocks`` on the
        12x10 grid; the assembler de-overlaps/clamps/reflows them.
    """
    model_config = ConfigDict(extra="forbid")
    slide_num: int
    tone: Literal["light", "dark", "green"] = "light"
    background: Background = Field(default_factory=Background)
    layout: str | None = None
    content: dict[str, Any] = Field(default_factory=dict)
    blocks: list[Block] = Field(default_factory=list)
