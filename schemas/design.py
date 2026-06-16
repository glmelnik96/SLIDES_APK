"""Schemas for the /design from-scratch designer skill.

`DesignStub` is the LOCKED art-direction contract: the art_director emits it
ONCE for the whole deck (q3 verdict: one combined call), and every downstream
node reads it verbatim — never mutates it (DKeken locked-stub pattern #1).

The per-slide `Composition` DSL lives in
``renderers/designer/composition_dsl.py``; it is the slide_composer's output.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Brand accent is code-consistent #26D07C (see llm.prompts._shared.BRAND_PALETTE).
BRAND_GREEN = "#26D07C"

# The do-not list carried into every composer/critic prompt (DKeken #3).
FORBIDDEN_DEFAULT = [
    "glassmorphism", "neon", "gradient", "shadow",
    "rounding>4px", "green_flood", "non_brand_color",
]


class PaletteRoles(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bg: str
    text: str
    accent: str = BRAND_GREEN


class TypeScale(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title_pt: int = 44
    body_pt: int = 16
    kpi_pt: int = 72


class MotifMix(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sparkle_density: Literal["none", "low", "med"] = "low"
    portal_usage: Literal["none", "dividers", "cover"] = "none"
    geometry: Literal["flat", "isometric", "mixed"] = "flat"
    decor: Literal["none", "outline_corners", "full"] = "outline_corners"
    density_target: Literal["airy", "balanced", "dense"] = "balanced"


class DesignStub(BaseModel):
    """The single locked art-direction decision for the whole deck."""
    model_config = ConfigDict(extra="forbid")
    tonality: Literal["light", "dark", "mixed"] = "light"
    # Share of dark slides, 0..0.4 (budgeted by the planner onto cover/divider).
    dark_ratio: float = Field(default=0.0, ge=0.0, le=0.4)
    palette_roles: PaletteRoles
    type_scale: TypeScale = Field(default_factory=TypeScale)
    motif_mix: MotifMix = Field(default_factory=MotifMix)
    forbidden: list[str] = Field(default_factory=lambda: list(FORBIDDEN_DEFAULT))
    rationale: str = ""


class CriticVerdict(BaseModel):
    """brand_critic_v2 output for one slide (or the deck)."""
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["READY", "NOT-READY"] = "READY"
    reasons: list[str] = Field(default_factory=list)


class PixelVerdict(BaseModel):
    """pixel_judge output: a VISION verdict on ONE rendered slide PNG.

    ``ok`` is a holistic "looks correct" gate; ``issues`` lists concrete visual
    defects (text overflow/clipping, empty/sparse slide, overlap, off-canvas,
    illegible contrast) phrased as fixable instructions for the composer.
    """
    model_config = ConfigDict(extra="forbid")
    ok: bool = True
    issues: list[str] = Field(default_factory=list)
