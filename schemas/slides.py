"""Slide-level Pydantic contracts.

Single source of truth for the 10-agent v0.9 batch pipeline (M3 full scope).
Each model corresponds to a JSON shape produced or consumed by an agent
documented in `skill_assets/agents_reference/`. The orchestrator validates LLM
responses against these models — if validation fails we re-prompt once before
halting the slide.

Design notes
------------
- `extra="allow"` on output models that quote skill JSON verbatim. The skill
  itself is forgiving on extra fields, and we don't want to refuse a valid
  agent reply just because it added a `_note` or `rationale` we didn't list.
- Geometry uses pixels at 1280×720 canvas (slide_type=flow_diagram_native) or
  EMU (infographic shapes). Both kept verbatim from skill conventions.
- `Plan` is the build_v9 ground truth: a slide is *either* `clone_from_slide`
  (donor route) *or* `slide_type` + the matching data block (native renders).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ─── Shared enums ────────────────────────────────────────────────────────────

SlideIntent = Literal[
    "title", "divider", "text", "comparison", "timeline",
    "team", "data", "image", "callout", "schema", "chart", "table",
]
"""Brief Reader → Slide Classifier. Free-form ``other`` lives in `extra` field."""

SlideCategory = Literal[
    "title", "divider", "text", "multicolumn", "image", "team",
    "timeline", "table", "callout", "pattern_bg", "logo", "tech", "other",
]

SlideType = Literal[
    "kpi_native", "image_native", "chart_native", "chart_pptx_native",
    "flow_diagram_native", "table_native",
]

BrandVerdict = Literal["OK", "WARN", "FAIL"]
ProcessVerdict = Literal["READY", "NEEDS_REWORK"]
SlideVerdict = Literal["READY", "REJECT", "NEEDS_REWORK"]


# ─── 01 Brief Reader ─────────────────────────────────────────────────────────

class BriefSlide(BaseModel):
    model_config = ConfigDict(extra="allow")
    num: int
    raw_title: str | None = None
    raw_body: list[str] = Field(default_factory=list)
    intent: str = "text"  # Literal kept loose — agent may say "schema"/"chart"
    key_phrase: str = ""
    elements_count: int = 0
    needs_visual: bool = False


class Brief(BaseModel):
    """Output of Agent 01 — passed into 02 (Classifier) and 03 (Distributor)."""
    model_config = ConfigDict(extra="allow")
    topic: str
    audience: str = "unknown"
    tone: Literal["formal", "informal", "analytical", "sales", "unknown"] = "unknown"
    slide_count: int
    key_messages: list[str] = Field(default_factory=list)
    has_numbers: bool = False
    has_quotes: bool = False
    has_team: bool = False
    has_timeline: bool = False
    slides: list[BriefSlide]


# ─── 02 Slide Classifier ────────────────────────────────────────────────────
# Each native slide_type carries its own typed config block.

class KpiNumber(BaseModel):
    model_config = ConfigDict(extra="allow")
    value: str
    desc: str = ""
    pct: bool = False
    accent: bool = False


class KpiConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str = ""
    numbers: list[KpiNumber] = Field(default_factory=list)


class ChartSeries(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    data: list[float | int]


class ChartConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal["area_stacked", "area_100", "bar", "bar_stacked", "line", "pie"]
    title: str = ""
    caption: str = ""
    x: list[Any] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)
    accent_idx: int = 0


class TableConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    header: str
    subtitle: str = ""
    style: Literal["zebra"] = "zebra"
    headers: list[str]
    data: list[list[str]]
    first_col_wider: bool = True
    borders: dict[str, Any] | None = None


class FlowBlock(BaseModel):
    model_config = ConfigDict(extra="allow")
    # Grid mode (preferred): row/col + lines.
    # Explicit mode: x/y/w/h + lines.
    id: str | None = None
    row: int | None = None
    col: int | None = None
    x: int | None = None
    y: int | None = None
    w: int | None = None
    h: int | None = None
    lines: list[str] = Field(default_factory=list)
    font_sizes: list[int] = Field(default_factory=list)
    bolds: list[bool] = Field(default_factory=list)
    fill: Literal["gray", "green", "white"] | None = None

    @model_validator(mode="after")
    def _backfill_lines(self):
        """Если агент положил текст блока в `text`/`title`/`label`/`caption`
        (extra-поля) вместо канонического `lines`, перенести его в `lines` —
        иначе flow_renderer рисует пустые рамки (dl1 slide-7 defect: блоки
        Recorder/OBS/SMN/Timeline пришли с lines=[] и text="Recorder")."""
        if not self.lines:
            extra = self.__pydantic_extra__ or {}
            collected = []
            for key in ("title", "text", "label", "caption"):
                v = extra.get(key)
                if isinstance(v, str) and v.strip():
                    collected.append(v.strip())
                elif isinstance(v, list):
                    collected.extend(str(x).strip() for x in v if str(x).strip())
            if collected:
                self.lines = collected
        return self


class FlowArrow(BaseModel):
    # populate_by_name lets us roundtrip through `model_dump()` (which emits
    # field names — `src`/`dst`) without losing validation on re-parse.
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    # Either id-based (`from`/`to` block id strings) or grid-based (lists).
    # `from` is a Python keyword → expose via alias.
    src: str | list[int] = Field(alias="from")
    dst: str | list[int] = Field(alias="to")
    side: Literal["right", "left", "top", "bottom"] | None = None


class FlowConfig(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    header: str = ""
    subtitle: str = ""
    subtitle_url: str = ""
    grid: bool = False
    cols: int | None = None
    font_size: int | None = None
    blocks: list[FlowBlock] = Field(default_factory=list)
    arrows: list[FlowArrow] = Field(default_factory=list)
    groups: list[dict[str, Any]] = Field(default_factory=list)
    labels: list[dict[str, Any]] = Field(default_factory=list)
    decor: dict[str, Any] | None = None


class ImageConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str = ""
    image_path: str
    caption: str = ""


class SlideClassification(BaseModel):
    """Per-slide output of Agent 02. Either category-only (donor route)
    or native (slide_type + matching data block)."""
    model_config = ConfigDict(extra="allow")
    num: int
    category: SlideCategory
    subcategory_hint: str = ""
    rationale: str = ""

    # Native render branch (optional, mutually exclusive with donor route).
    slide_type: SlideType | None = None
    kpi: KpiConfig | None = None
    chart: ChartConfig | None = None
    table: TableConfig | None = None
    flow: FlowConfig | None = None
    image: ImageConfig | None = None
    dark: bool = False

    # Split bookkeeping (Agent 02 may emit more slides than were in the Brief).
    _source_slide: int | None = None
    _split_part: str | None = None

    @model_validator(mode="after")
    def _native_block_present(self) -> "SlideClassification":
        if self.slide_type is None:
            return self
        required = {
            "kpi_native": self.kpi,
            "chart_native": self.chart,
            "chart_pptx_native": self.chart,
            "table_native": self.table,
            "flow_diagram_native": self.flow,
            "image_native": self.image,
        }[self.slide_type]
        if required is None:
            raise ValueError(
                f"slide_type={self.slide_type} requires its matching data block"
            )
        return self


class DeckClassification(BaseModel):
    model_config = ConfigDict(extra="allow")
    slides: list[SlideClassification]


# ─── 04 Layout Designer ─────────────────────────────────────────────────────

class LayoutChoice(BaseModel):
    """Layout Designer (Agent 04) output. `donor` is the 1-based slide number
    in `Cloud.ru_Template_2026.pptx` — the key under `donors:` in
    `skill_assets/brand/donor-slot-map.yaml`. The orchestrator copies this
    value verbatim into `PlanSlide.clone_from_slide` for build_v9.
    """
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    num: int
    # 0 = native render (chart/table/flow/image) — no donor applicable per
    # Agent 04 prompt; orchestrator routes those slides through native builders.
    donor: int = Field(ge=0, le=101, alias="layout_idx")
    layout_name: str = ""
    rationale: str = ""
    slot_styles_override: dict[str, Any] = Field(default_factory=dict)


class LayoutPlan(BaseModel):
    model_config = ConfigDict(extra="allow")
    slides: list[LayoutChoice]


# ─── 03 Content Distributor ─────────────────────────────────────────────────

PlaceholderType = Literal[
    "TITLE", "CENTER_TITLE", "SUBTITLE", "BODY", "CONTENT",
    "PICTURE", "OBJECT", "OTHER",
]


# Semantic slot names emitted by donor_map.slot_specs_for_layouts (keys of
# the donor YAML) → canonical OOXML PlaceholderType. The LLM mirrors back
# whatever ph_type it sees in the prompt's SLOT_SPECS, so without a
# normalizer Pydantic literal_error rejects the whole batch (39× failures
# observed on 14-slide live run 2026-06-04). Mapping table is exhaustive
# wrt donor-slot-map.yaml keys; anything unmapped falls through to OTHER.
_SLOT_NAME_TO_OOXML: dict[str, str] = {
    "title": "TITLE",
    "center_title": "CENTER_TITLE",
    "subtitle": "SUBTITLE",
    "body": "BODY",
    "content": "CONTENT",
    "picture": "PICTURE",
    "image": "PICTURE",
    "logo": "PICTURE",
    "object": "OBJECT",
    "other": "OTHER",
}


def _normalize_ph_type(v: Any) -> Any:
    """Coerce ph_type into the PlaceholderType literal.

    Distributor LLM occasionally mirrors lowercase / semantic slot names
    from SLOT_SPECS instead of the uppercase OOXML enum the schema
    expects. Uppercase first (handles "title" → "TITLE"); then map known
    multi-column body variants ("COL1_BODY", "COL2_BODY"...) and any
    *_BODY / *_CONTENT prefix to canonical BODY / CONTENT; everything
    else unknown → OTHER. Non-string inputs pass through untouched so
    Pydantic produces its normal type error.
    """
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return "BODY"
    upper = s.upper()
    if upper in {"TITLE", "CENTER_TITLE", "SUBTITLE", "BODY", "CONTENT",
                 "PICTURE", "OBJECT", "OTHER"}:
        return upper
    # Multi-column body / content variants (col1_body, body_left, etc.)
    if "BODY" in upper:
        return "BODY"
    if "CONTENT" in upper:
        return "CONTENT"
    if "TITLE" in upper:
        return "TITLE"
    if "PICTURE" in upper or "IMAGE" in upper or "LOGO" in upper:
        return "PICTURE"
    # Lowercase semantic name lookup
    if s.lower() in _SLOT_NAME_TO_OOXML:
        return _SLOT_NAME_TO_OOXML[s.lower()]
    return "OTHER"


class PlaceholderAssignment(BaseModel):
    model_config = ConfigDict(extra="allow")
    ph_idx: int
    ph_type: PlaceholderType = "BODY"
    content: str = ""
    # Set by Copy Editor — diff summary of the edits applied.
    diff: str | None = None

    @field_validator("ph_type", mode="before")
    @classmethod
    def _coerce_ph_type(cls, v: Any) -> Any:
        return _normalize_ph_type(v)


class ContentAssignment(BaseModel):
    """Agent 03 output (per slide). After Agent 07 edits, the same model
    carries the cleaned text + edits_count."""
    model_config = ConfigDict(extra="allow")
    slide_num: int
    layout_idx: int
    placeholder_assignments: list[PlaceholderAssignment]
    dropped_content: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    edits_count: int = 0  # populated by Copy Editor


# ─── 05 Icon Picker ─────────────────────────────────────────────────────────

class IconAssignment(BaseModel):
    model_config = ConfigDict(extra="allow")
    ph_idx: int
    icon_keyword: str
    icon_path: str | None = None
    fallback: str | None = None


class IconAssignments(BaseModel):
    model_config = ConfigDict(extra="allow")
    slide_num: int
    icon_assignments: list[IconAssignment] = Field(default_factory=list)


# ─── 06 Infographic Maker ───────────────────────────────────────────────────

InfographicType = Literal[
    "process", "flow", "tree", "comparison", "matrix",
    "chart_bar", "chart_pie", "none",
]


class InfographicShape(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: Literal[
        "rectangle", "rounded_rect", "arrow", "line", "circle", "text",
    ]
    left_emu: int
    top_emu: int
    width_emu: int
    height_emu: int
    fill_color: str = "#F2F2F2"
    stroke_color: str = "none"
    stroke_width_pt: float = 0.0
    text: str = ""
    font: str = "SB Sans Display"
    font_size_pt: int = 14
    font_color: str = "#222222"


class InfographicSpec(BaseModel):
    model_config = ConfigDict(extra="allow")
    slide_num: int
    infographic_type: InfographicType = "none"
    shapes: list[InfographicShape] = Field(default_factory=list)


# ─── 08 Brand Guardian (Python — brand_guardian.py JSON report) ─────────────

class BrandViolation(BaseModel):
    model_config = ConfigDict(extra="allow")
    severity: Literal["FAIL", "WARN", "NOTE"]
    rule: str
    msg: str
    fix: str = ""


class SlideBrandReport(BaseModel):
    model_config = ConfigDict(extra="allow")
    slide_num: int
    verdict: BrandVerdict
    violations: list[BrandViolation] = Field(default_factory=list)
    score: int = 100


class BrandReport(BaseModel):
    """Aggregated output of `brand_guardian.py` — per-deck."""
    model_config = ConfigDict(extra="allow")
    verdict: BrandVerdict
    score_avg: int = 100
    slides: list[SlideBrandReport] = Field(default_factory=list)

    @field_validator("score_avg", mode="before")
    @classmethod
    def _round_score_avg(cls, v: Any) -> Any:
        if isinstance(v, float):
            return round(v)
        return v


# ─── 10 LLM Visual Verifier ─────────────────────────────────────────────────

class HardChecks(BaseModel):
    model_config = ConfigDict(extra="allow")
    text_replaced: bool = True
    semantics_ok: bool = True
    no_overflow: bool = True
    no_overlap: bool = True
    contrast_ok: bool = True
    aspect_ok: bool = True


class FiveDim(BaseModel):
    """Designer-style 5-dimensional rubric — each 1..5."""
    model_config = ConfigDict(extra="allow")
    philosophy: int = Field(ge=1, le=5)
    hierarchy: int = Field(ge=1, le=5)
    detail: int = Field(ge=1, le=5)
    function: int = Field(ge=1, le=5)
    innovation: int = Field(ge=1, le=5)
    comments: dict[str, str] = Field(default_factory=dict)


class VisualSlideVerdict(BaseModel):
    model_config = ConfigDict(extra="allow")
    num: int
    intent: str = ""
    actual: str = ""
    hard_checks: HardChecks = Field(default_factory=HardChecks)
    slide_verdict: SlideVerdict
    fivedim: FiveDim | None = None
    score: int = 0
    issues: list[BrandViolation] = Field(default_factory=list)

    @field_validator("score", mode="before")
    @classmethod
    def _round_score(cls, v: Any) -> Any:
        if isinstance(v, float):
            return round(v)
        return v


class GhostDeckTest(BaseModel):
    model_config = ConfigDict(extra="allow")
    passed: bool
    narrative: str = ""
    issues: list[str] = Field(default_factory=list)


class VisualVerdict(BaseModel):
    """Agent 10 output."""
    model_config = ConfigDict(extra="allow")
    llm_verdict: ProcessVerdict
    score_avg: int = 0
    ghost_deck_test: GhostDeckTest | None = None
    slides: list[VisualSlideVerdict] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)

    @field_validator("score_avg", mode="before")
    @classmethod
    def _round_score_avg(cls, v: Any) -> Any:
        # Kimi-vision returned ``64.8`` on the 2026-06-04 live run, which
        # tripped Pydantic's int_from_float. Round to nearest int instead
        # of failing — the 0.x precision loss is meaningless for a 0-100
        # rubric. Same coercion is applied to ``BrandReport`` and
        # ``VerifierVerdict`` below.
        if isinstance(v, float):
            return round(v)
        return v


# ─── 09 Process Verifier (orchestration) ────────────────────────────────────

class SlideChecklistResult(BaseModel):
    model_config = ConfigDict(extra="allow")
    checks_passed: int = 0
    issues: list[str] = Field(default_factory=list)


class VerifierVerdict(BaseModel):
    """Agent 09 — synthesises validate_plan + brand_guardian + visual_validator
    + LLM Visual Verifier into the single READY/NEEDS_REWORK decision."""
    model_config = ConfigDict(extra="allow")
    verdict: ProcessVerdict
    score_avg: int = 0
    checklist_results: dict[str, SlideChecklistResult] = Field(default_factory=dict)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)


# ─── Plan (build_v9.py input — assembled by orchestrator) ───────────────────

class PlanSlide(BaseModel):
    """One slide in the build_v9 plan. Either donor route (`clone_from_slide`
    + `slots`) OR native render (`slide_type` + matching data block).

    Slot values in `slots` are arbitrary by design — build_v9 walks the
    donor-slot-map.yaml schema and applies them at run time.
    """
    model_config = ConfigDict(extra="allow")

    # Donor route — 1-based slide number in Cloud.ru_Template_2026.pptx.
    clone_from_slide: int | None = Field(default=None, ge=1, le=101)
    slots: dict[str, Any] = Field(default_factory=dict)
    slot_styles_override: dict[str, Any] = Field(default_factory=dict)

    # Native route
    slide_type: SlideType | None = None
    kpi: KpiConfig | None = None
    chart: ChartConfig | None = None
    table: TableConfig | None = None
    flow: FlowConfig | None = None
    image: ImageConfig | None = None
    dark: bool = False

    @model_validator(mode="after")
    def _one_route(self) -> "PlanSlide":
        if self.slide_type is None and self.clone_from_slide is None:
            raise ValueError(
                "PlanSlide requires either clone_from_slide or slide_type"
            )
        if self.slide_type is not None and self.clone_from_slide is not None:
            raise ValueError(
                "PlanSlide cannot set both clone_from_slide and slide_type"
            )
        return self


class Plan(BaseModel):
    """The `plan.json` consumed by `build_v9.py`."""
    model_config = ConfigDict(extra="allow")
    slides: list[PlanSlide]
    # Optional metadata that orchestration may attach.
    _validation: dict[str, Any] | None = None


# ─── parse_pptx output (input to Brief Reader) ──────────────────────────────

class ParsedImage(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    left_emu: int
    top_emu: int
    width_emu: int
    height_emu: int


class ParsedSlide(BaseModel):
    model_config = ConfigDict(extra="allow")
    num: int
    layout_name: str
    layout_idx_in_master: int | None = None
    title: str | None = None
    body: list[str] = Field(default_factory=list)
    text_runs: list[str] = Field(default_factory=list)
    images: list[ParsedImage] = Field(default_factory=list)
    shapes_count: int = 0
    tables_count: int = 0
    # Extracted table grids: [{"headers": [...], "rows": [[...]], "regular": bool}]
    tables: list[dict[str, Any]] = Field(default_factory=list)


class ParsedDeck(BaseModel):
    model_config = ConfigDict(extra="allow")
    file: str
    slide_count: int
    slide_size: dict[str, int]
    slides: list[ParsedSlide]
