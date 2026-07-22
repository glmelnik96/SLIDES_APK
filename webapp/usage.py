"""Append-only usage log (see models.UsageEvent) — App2/slides side of the shared
cross-app contract docs/.../2026-06-22-usage-events-logging.md.

One row per finished build. NO files, NO full prompt — only an anonymised metric
(workflow/status/duration + a safe meta whitelist) for "who used it, how much".
Identity comes from the gateway headers, carried on the Job's owning User. This
table is excluded from retention so usage history accumulates long-term.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from webapp.config import settings
from webapp.db import models

APP_NAME = "slides"


def _duration_ms(started_at: datetime | None,
                 finished_at: datetime | None = None) -> int | None:
    if started_at is None:
        return None
    end = finished_at or datetime.now(timezone.utc)
    # SQLite hands back naive UTC — normalise so aware/naive subtraction can't
    # raise TypeError.
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    return max(0, int((end - started_at).total_seconds() * 1000))


def _slides_count(result_path: str | None) -> int | None:
    """Count slides in the finished deck (safe metric — no content stored).
    Only meaningful on a successful build with a real file on disk."""
    if not result_path:
        return None
    p = Path(result_path)
    if not p.is_file():
        return None
    try:
        from webapp import deck_edit
        return deck_edit.count_slides(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — metric is best-effort, never fatal
        return None


async def log_render(session: AsyncSession, *, owner_user_id: int | None,
                     status: str, workflow: str | None,
                     started_at: datetime | None,
                     result_path: str | None) -> None:
    """Stage a usage row in the caller's session (the caller commits). Identity is
    resolved from the owning User; meta carries only the slide count."""
    user = (await session.get(models.User, owner_user_id)
            if owner_user_id is not None else None)
    meta: dict[str, object] = {}
    n = _slides_count(result_path)
    if n is not None:
        meta["slides_count"] = n
    session.add(models.UsageEvent(
        app=APP_NAME,
        gateway_user_id=(user.gateway_user_id if user else None),
        email=(user.email if user else ""),
        event="render",
        workflow=workflow,
        status=status,
        duration_ms=_duration_ms(started_at),
        meta=meta,
    ))


# ── cross-app usage push to the gateway (variant B) ──────────────────────────
def _smoke_emails() -> set[str]:
    """Lower-cased set of tech/smoke emails excluded from reporting."""
    return {e.strip().lower()
            for e in settings.usage_smoke_emails.split(",") if e.strip()}


def build_ingest_payload(*, email: str | None, gateway_user_id: str | None,
                         status: str, workflow: str | None,
                         duration_ms: int | None,
                         result_path: str | None) -> dict:
    """Shape one usage record for POST /internal/usage per the shared contract.

    Only an anonymised metric — no files, no prompt. `event` is "generation"
    (the gateway's cross-app vocabulary), distinct from the local DB's "render".
    Optional fields are omitted when empty so the receiver applies its defaults.
    """
    meta: dict[str, object] = {}
    n = _slides_count(result_path)
    if n is not None:
        meta["slides_count"] = n
    payload: dict[str, object] = {
        "app": APP_NAME,
        "email": email or "",
        "event": "generation",
        "status": status,
        "meta": meta,
    }
    if workflow:
        payload["workflow"] = workflow[:32]   # contract caps the key at 32 chars
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if gateway_user_id is not None:
        payload["gateway_user_id"] = gateway_user_id
    return payload


async def report_to_gateway(sessionmaker: async_sessionmaker, *,
                            owner_user_id: int | None, status: str,
                            workflow: str | None, started_at: datetime | None,
                            result_path: str | None, client=None) -> bool:
    """Best-effort: push one finished-operation metric to the gateway.

    Returns True iff an event was actually sent. No-op (returns False) when the
    ingest token is unset (local dev / un-provisioned deploy) or the owner is a
    smoke/tech account. All transport errors are swallowed — analytics must never
    affect the build. `client` is an injectable httpx-like AsyncClient for tests.
    """
    token = settings.usage_ingest_token.strip()
    if not token:
        return False    # disabled — endpoint off / no secret provisioned
    try:
        async with sessionmaker() as s:
            user = (await s.get(models.User, owner_user_id)
                    if owner_user_id is not None else None)
        email = user.email if user else ""
        if email and email.strip().lower() in _smoke_emails():
            return False    # exclude tech/smoke traffic on our side
        payload = build_ingest_payload(
            email=email,
            gateway_user_id=(user.gateway_user_id if user else None),
            status=status, workflow=workflow,
            duration_ms=_duration_ms(started_at), result_path=result_path)
        headers = {"Content-Type": "application/json", "X-Ingest-Token": token}
        if client is not None:
            await client.post(settings.usage_ingest_url,
                              json=payload, headers=headers)
        else:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as c:
                await c.post(settings.usage_ingest_url,
                             json=payload, headers=headers)
        return True
    except Exception:  # noqa: BLE001 — never let a metric push break anything
        return False
