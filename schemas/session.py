"""Session-level Pydantic models — used both as LangGraph state and as
the contract between bot and worker. Kept intentionally minimal in M2;
slide-level schemas land in M3 (designer) and M4 (validation).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class Mode(str, Enum):
    VERSTAI = "verstai"
    AUDIT = "audit"
    BRIEF = "brief"
    DESIGN = "design"  # /design — from-scratch native-vector designer skill
    HTML = "html"      # /html — LLM-authored HTML/CSS → Chromium PNG → pptx
    HTMLNEW = "htmlnew"  # /htmlnew — htmlslides package: doc → self-contained HTML deck
    HTMLPOLISH = "htmlpolish"  # rebuild a manual/chat draft (DeckPlan) through the engine QA pass


class Stage(str, Enum):
    """Coarse-grained pipeline stages — what the user sees in the progress message."""
    QUEUED = "queued"
    PARSING = "parsing"
    CLASSIFYING = "classifying"
    DESIGNING = "designing"
    RENDERING = "rendering"
    VALIDATING = "validating"
    AUTOFIXING = "autofixing"
    FINALIZING = "finalizing"
    DONE = "done"
    CANCELLED = "cancelled"
    FAILED = "failed"
    HALTED = "halted"


class SessionInput(BaseModel):
    """What the bot hands to the worker when enqueueing a job."""
    model_config = ConfigDict(use_enum_values=False)

    session_id: str = Field(default_factory=lambda: uuid4().hex[:16])
    user_id: int
    chat_id: int
    # Telegram message_id of the progress message that the bot will edit.
    progress_message_id: int
    mode: Mode
    # S3 key of the input artefact (pptx for verstai/audit, doc/docx for brief).
    input_s3_key: str | None = None
    # Original upload filename (Telegram doc.file_name). Used to name the output
    # file `{session_id}_{source}.pptx` so a deck can be tied back to its run.
    source_filename: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    exact_transfer: bool = False        # точный перенос 1-в-1 (в обход ИИ)


class SessionState(BaseModel):
    """LangGraph state. Serialised into RedisSaver on every checkpoint.

    Keep it JSON-roundtrippable — no datetime in tight loops, no Enum that the
    serialiser can't handle. We use `str` for stage and ISO-8601 strings for
    timestamps to stay safe across langgraph-checkpoint-redis versions.
    """
    model_config = ConfigDict(extra="forbid")

    # Identity
    session_id: str
    user_id: int
    chat_id: int
    progress_message_id: int
    mode: Literal["verstai", "audit", "brief", "design", "html", "htmlnew",
                  "htmlpolish"]
    created_at_iso: str

    # Pipeline
    stage: str = Stage.QUEUED.value
    progress_pct: int = 0
    # Free-form short label appended to the stage in the progress message.
    stage_detail: str = ""

    # Inputs / outputs
    input_s3_key: str | None = None
    source_filename: str | None = None
    exact_transfer: bool = False        # точный перенос 1-в-1 (в обход ИИ)
    result_s3_key: str | None = None
    report_s3_key: str | None = None

    # Diagnostics
    autofix_iterations: int = 0
    brand_score: int | None = None
    errors: list[str] = Field(default_factory=list)
    # Notes are short user-visible lines included in the final summary.
    notes: list[str] = Field(default_factory=list)

    # Pipeline artefacts. Each node stores its parsed output (model_dump'ed
    # from the corresponding Pydantic schema) under a stable key:
    #   parsed_deck, brief, classification, layouts, content,
    #   icons, infographics, copy_edited, plan, brand_report,
    #   visual_verdict, verifier_verdict.
    # Keeping this as a flat dict avoids declaring 12 typed fields on
    # SessionState while keeping JSON-roundtrippability for RedisSaver.
    # Type-safe accessors live in `graph/state_io.py` (added in next chunk).
    artefacts: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_input(cls, inp: SessionInput) -> "SessionState":
        return cls(
            session_id=inp.session_id,
            user_id=inp.user_id,
            chat_id=inp.chat_id,
            progress_message_id=inp.progress_message_id,
            mode=inp.mode.value,
            created_at_iso=inp.created_at.isoformat(),
            input_s3_key=inp.input_s3_key,
            source_filename=inp.source_filename,
            exact_transfer=inp.exact_transfer,
        )


class ProgressEvent(BaseModel):
    """Wire format for the Redis pub/sub channel `progress:{session_id}`."""
    session_id: str
    stage: str
    progress_pct: int
    detail: str = ""
    # Set on terminal events so the bot can swap UI/strip cancel button.
    terminal: bool = False
    # When the bot needs to inform the user about a failure or halt.
    error: str | None = None
    # Optional artefact location surfaced on terminal DONE so the bot can
    # send the built .pptx back to the user. M3 interim: local filesystem
    # path (worker + bot share the same machine). M5 will swap this for an
    # S3 key — the field name carries that intent.
    result_path: str | None = None
