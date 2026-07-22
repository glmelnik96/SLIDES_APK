"""SQLAlchemy ORM models for App2 (users + jobs).

Mirrors App1's shape: a User is upserted lazily by the gateway-supplied id; a Job
row records each build so history survives restarts and access is scoped per user.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    gateway_user_id: Mapped[str] = mapped_column(
        String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow)

    jobs: Mapped[list["Job"]] = relationship(back_populates="user")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(8))  # "html" | "pptx"
    source_filename: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    # queued | running | done | failed | cancelled
    result_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Расход прогона (проставляется на терминальном событии; None для старых
    # строк и для исходов без LLM). Стоимость — в рублях по прайсу MiniMax.
    in_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    out_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_rub: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    user: Mapped["User"] = relationship(back_populates="jobs")


class UsageEvent(Base):
    """Append-only usage log (shared cross-app contract — see App1's UsageEvent).

    One row per finished build (done/failed/cancelled). Anonymised metric only —
    NO files, NO full prompt; ``meta`` holds a safe whitelist. Retention never
    touches this table, so usage history accumulates long-term. Cross-app rollups
    join on gateway_user_id + email."""
    __tablename__ = "usage_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True)
    app: Mapped[str] = mapped_column(String(16), index=True)          # "slides"
    gateway_user_id: Mapped[Optional[str]] = mapped_column(
        String(64), index=True, nullable=True)
    email: Mapped[str] = mapped_column(String(255), default="", index=True)
    event: Mapped[str] = mapped_column(String(32))                    # "render"
    workflow: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
