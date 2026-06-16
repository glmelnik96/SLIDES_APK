"""Role → (model, thinking-toggle, max_tokens) registry.

Single source of truth for the pipeline. Each LangGraph node imports its
RoleSpec from here and never hard-codes a model name.

Empirical findings — see <project>/memory/cloudru_fm_api.md.

Mapping rationale (M3, v0.9 batch):
- Only Kimi-K2.6 is multimodal on Cloud.ru FM → Brief (01) and Visual
  Verifier (10) MUST use it. Vision-grounding also fixes Kimi's
  text-only brief instability ("often invalid" at 3.7s).
- DeepSeek-V4-Pro is non-reasoning, ~0.7–3.8s on JSON → 02 Classifier
  and 04 Designer (terse lookup-table reasoning).
- GLM-5.1 thinking-OFF is the stable nested-JSON workhorse → 03
  Distributor, 05 Icons, 06 Infographic, 07 Copy Editor.
- GLM-5.1 thinking-ON gives harshest critic recall (score=10 vs
  DeepSeek 30) → kept for BRAND_GUARDIAN_CRITIC and AUTOFIX (used
  outside the v0.9 batch loop, in future autofix iterations).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Role(str, Enum):
    # v0.9 batch agents (M3)
    BRIEF_PARSER = "brief_parser"            # 01 — Kimi vision
    CLASSIFIER = "classifier"                # 02 — DeepSeek
    DISTRIBUTOR = "distributor"              # 03 — GLM OFF
    DESIGNER = "designer"                    # 04 — DeepSeek
    ICON_PICKER = "icon_picker"              # 05 — GLM OFF
    INFOGRAPHIC_MAKER = "infographic_maker"  # 06 — GLM OFF
    COPY_EDITOR = "copy_editor"              # 07 — GLM OFF
    VISUAL_VERIFIER = "visual_verifier"      # 10 — Kimi vision

    # Reserved for future autofix / critic loops (not used in M3 batch flow)
    BRAND_GUARDIAN_CRITIC = "brand_guardian_critic"
    AUTOFIX = "autofix"
    OUTLINE_BUILDER = "outline_builder"
    PIXEL_JUDGE = "pixel_judge"

    # ── /design from-scratch designer skill (separate graph) ───────────────
    ART_DIRECTOR = "art_director"            # combined locked-stub — GLM ON
    SLIDE_COMPOSER = "slide_composer"        # Composition DSL emit — GLM OFF
    BRAND_CRITIC_V2 = "brand_critic_v2"      # READY/NOT-READY gate — GLM ON


@dataclass(frozen=True)
class RoleSpec:
    model: str
    max_tokens: int
    temperature: float = 0.0
    # extra_body passes per-request kwargs to the underlying OpenAI SDK call.
    # Used to disable reasoning where supported.
    extra_body: dict[str, Any] = field(default_factory=dict)
    # Vision roles set this to True so the dispatcher can validate inputs.
    requires_vision: bool = False


# GLM-5.1 thinking-OFF: the only toggle that actually disables reasoning.
_GLM_THINKING_OFF = {"chat_template_kwargs": {"enable_thinking": False}}
# Kimi-K2.6 thinking-OFF: partial reduction, text-only. Ignored on vision.
_KIMI_THINKING_OFF = {"thinking": {"type": "disabled"}}


ROLES: dict[Role, RoleSpec] = {
    # ── v0.9 batch agents ──────────────────────────────────────────────
    Role.BRIEF_PARSER:
        # Kimi vision — reasoning ~7-9k chars (counted under completion_tokens
        # per cloudru_fm_api.md). 14-slide deck observation 2026-06-04:
        # successful retry used completion_tokens=16785 (reasoning 35k chars
        # + content 12k chars). 18000 covers that envelope with margin and
        # avoids the +75s auto-bump round-trip on every run.
        RoleSpec(model="moonshotai/Kimi-K2.6", max_tokens=18000, requires_vision=True),
    Role.CLASSIFIER:
        # Per-deck DeckClassification with optional native blocks
        # (kpi/table/flow can be large). Empirically a 15-slide deck with
        # several natives hits ~3500 output tokens — 2500 truncated big.
        RoleSpec(model="deepseek-ai/DeepSeek-V4-Pro", max_tokens=4000),
    Role.DISTRIBUTOR:
        # Per-deck ContentAssignment, GLM OFF for nested-JSON stability.
        # 2500 truncated mid-string on a 15-slide deck (Unterminated string
        # at pos 9363 on 2026-06-04 live run). Russian Cyrillic content +
        # per-placeholder diff strings push output past 8KB on big decks.
        # 8000: pre-emptive headroom — regularly hit 6000 cap triggering the
        # auto-bump retry (+20-40s); cheaper to avoid the second call.
        RoleSpec(model="zai-org/GLM-5.1", max_tokens=8000, extra_body=_GLM_THINKING_OFF),
    Role.DESIGNER:
        # LayoutPlan over the deck; DeepSeek terse table-lookup style.
        RoleSpec(model="deepseek-ai/DeepSeek-V4-Pro", max_tokens=2000),
    Role.ICON_PICKER:
        # 2026-06-04 live: 14-slide deck hit length-cap at 1500; auto-bump
        # to 3000 succeeded with completion_tokens=2556. 3000 is the steady
        # state and avoids the +13s round-trip.
        RoleSpec(model="zai-org/GLM-5.1", max_tokens=3000, extra_body=_GLM_THINKING_OFF),
    Role.INFOGRAPHIC_MAKER:
        # Shape lists with EMU coordinates can grow; 4000 truncated big-deck
        # output mid-shape. 2026-06-04 live: 14-slide deck hit length-cap
        # at 6000; auto-bump to 12000 succeeded at completion_tokens=2813
        # (with compact JSON prompt nudge). 8000: empirical max output is
        # ~2813 tokens, so 12000 was waste; 8000 still gives >2.8x headroom.
        RoleSpec(model="zai-org/GLM-5.1", max_tokens=8000, extra_body=_GLM_THINKING_OFF),
    Role.COPY_EDITOR:
        # 15-slide deck × per-slot diff strings → 2000 truncated big deck.
        # 2026-06-04 live: 14-slide deck hit length-cap at 4000; auto-bump
        # to 8000 succeeded with completion_tokens=5453. 10000: pre-emptive
        # headroom — regularly hit 8000 cap triggering auto-bump (+20-40s).
        RoleSpec(model="zai-org/GLM-5.1", max_tokens=10000, extra_body=_GLM_THINKING_OFF),
    Role.VISUAL_VERIFIER:
        # Kimi vision always reasons (~5-9k chars), tokens counted under
        # completion_tokens. 5-dim rubric × 15 slides + ghost-deck narrative
        # = ~16k char output for big decks. 2026-06-04 live: 14-slide deck
        # twice hit length-cap at 12000, auto-bump to 24000 succeeded at
        # completion_tokens=16443 and 22502. 20000 is the empirical ceiling
        # and saves ~150s × 2 (≈5 min per pipeline) vs always-bumping.
        RoleSpec(model="moonshotai/Kimi-K2.6", max_tokens=20000, requires_vision=True),

    # ── Reserved (autofix / future loops) ──────────────────────────────
    Role.BRAND_GUARDIAN_CRITIC:
        # Thinking ON intentionally — empirically yielded the harshest, most
        # accurate verdict on synthetic brand violations (score=10 vs 30).
        RoleSpec(model="zai-org/GLM-5.1", max_tokens=2500),
    Role.AUTOFIX:
        RoleSpec(model="zai-org/GLM-5.1", max_tokens=2500),
    Role.OUTLINE_BUILDER:
        RoleSpec(model="zai-org/GLM-5.1", max_tokens=1200, extra_body=_GLM_THINKING_OFF),
    Role.PIXEL_JUDGE:
        # Kimi vision always reasons; single-slide verdict output is tiny
        # (ok + a few issue strings) but reasoning alone eats ~4-4.7k tokens,
        # so 2000 truncated to content_len=0 and forced an auto-bump retry on
        # every QA call (observed 2026-06-08 live). 6000 covers reasoning +
        # verdict with margin and removes the wasted round-trip.
        RoleSpec(model="moonshotai/Kimi-K2.6", max_tokens=6000, requires_vision=True),

    # ── /design designer skill ─────────────────────────────────────────────
    Role.ART_DIRECTOR:
        # Combined tonality+motifs in ONE call (q3 A/B verdict 2026-06-08:
        # combined beat split on cost, latency AND quality). Thinking ON —
        # q3 used GLM-5.1 thinking-ON and emitted ~1572 completion tokens
        # over 4180 reasoning chars. 2500 covers it with margin.
        RoleSpec(model="zai-org/GLM-5.1", max_tokens=2500),
    Role.SLIDE_COMPOSER:
        # Per-slide Composition DSL (blocks on a 12×10 grid). GLM OFF for
        # stable nested JSON. Charts/nodes/cards push output up; 6000 mirrors
        # the DISTRIBUTOR envelope for big slides.
        RoleSpec(model="zai-org/GLM-5.1", max_tokens=6000, extra_body=_GLM_THINKING_OFF),
    Role.BRAND_CRITIC_V2:
        # The gate. Thinking ON for harshest recall (same rationale as the
        # legacy BRAND_GUARDIAN_CRITIC). Emits READY/NOT-READY + reasons.
        RoleSpec(model="zai-org/GLM-5.1", max_tokens=2500),
}
