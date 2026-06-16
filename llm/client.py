"""Thin synchronous wrapper around the OpenAI SDK pointed at Cloud.ru FM.

Provides:
 - one shared OpenAI client (lazy-initialised, env-driven)
 - a token-bucket RPS limiter backed by Redis (cluster-wide cap on the API key)
 - `call_role(...)` — dispatch by Role with retry + structured logging
 - `build_vision_content(...)` — helper for Kimi-K2.6 multimodal messages
"""
from __future__ import annotations

import base64
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import structlog
from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from bot.config import get_settings
from llm.roles import ROLES, Role, RoleSpec

logger = structlog.get_logger(__name__)

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        s = get_settings()
        _client = OpenAI(api_key=s.cloudru_api_key, base_url=s.cloudru_base_url)
    return _client


# ─── RPS limiter ─────────────────────────────────────────────────────────────

# Process-wide state for the min-interval gate.  All outbound chat requests
# flow through acquire_rps_slot() before hitting the Cloud.ru API.
_rps_lock: threading.Lock = threading.Lock()
_next_allowed: float = 0.0  # monotonic time at which the next slot opens


def acquire_rps_slot() -> None:
    """Reserve a rate-limit slot and sleep until it is allowed to start.

    Thread-safe: the slot timestamp is advanced while holding ``_rps_lock``
    so concurrent callers queue without racing; the actual sleep happens
    outside the lock so other threads can compute their own wake-up time
    in parallel.

    Rate configured via ``CLOUDRU_MAX_RPS`` env var (float, default 2.0).
    A value <= 0 disables the limiter entirely.
    """
    global _next_allowed
    rps = float(os.environ.get("CLOUDRU_MAX_RPS", "2.0"))
    if rps <= 0:
        return

    interval = 1.0 / rps
    with _rps_lock:
        now = time.monotonic()
        # If the queue is idle, start immediately; otherwise append after the
        # current tail.
        wake = max(now, _next_allowed)
        _next_allowed = wake + interval

    sleep_for = wake - time.monotonic()
    if sleep_for > 0:
        if sleep_for > 1.0:
            logger.debug("llm.rps_wait", wait_s=round(sleep_for, 3))
        time.sleep(sleep_for)


# ─── Vision helpers ──────────────────────────────────────────────────────────

VisionImage = str | bytes | Path
"""One of: data-URL / http(s) URL string, raw PNG/JPEG bytes, Path to a file."""


def _encode_image(image: VisionImage) -> str:
    """Return a `data:` URL or pass-through HTTP URL for an `image_url` block."""
    if isinstance(image, str):
        # Pass through if it's already a URL Cloud.ru accepts. Otherwise treat
        # as a filesystem path — state.artefacts roundtrips through JSON
        # (RedisSaver), so a Path stored upstream becomes a string here.
        if image.startswith(("data:", "http://", "https://", "file://")):
            return image
        image = Path(image)
    if isinstance(image, Path):
        data = image.read_bytes()
        # Sniff extension for the MIME hint; default to png.
        ext = image.suffix.lower().lstrip(".")
        mime = "image/jpeg" if ext in ("jpg", "jpeg") else "image/png"
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    if isinstance(image, (bytes, bytearray)):
        b64 = base64.b64encode(bytes(image)).decode("ascii")
        # No way to sniff without magic — assume PNG (the worker renders PNG).
        return f"data:image/png;base64,{b64}"
    raise TypeError(f"Unsupported image type: {type(image).__name__}")


def build_vision_content(
    text: str,
    images: Iterable[VisionImage] = (),
) -> list[dict[str, Any]]:
    """Compose an OpenAI vision `content` list: text block + N image_url blocks.

    Use as the ``content`` field of a user message when calling a vision role
    (BRIEF_PARSER, VISUAL_VERIFIER, PIXEL_JUDGE)::

        msg = {"role": "user", "content": build_vision_content(prompt, [png_bytes])}

    Empty ``images`` is valid — yields a plain text content list that vision
    models still accept. Brief Reader uses this to keep one code-path
    regardless of whether the input draft has rendered slides or not.
    """
    blocks: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for img in images:
        blocks.append({"type": "image_url", "image_url": {"url": _encode_image(img)}})
    return blocks


# ─── Call dispatch ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class LLMCall:
    role: Role
    messages: list[dict[str, Any]]
    # Override fields if the caller needs a one-off tweak.
    max_tokens_override: int | None = None
    extra_body_override: dict[str, Any] | None = None
    # Optional images appended to the LAST user message via build_vision_content.
    # Convenience for nodes that don't want to assemble content blocks manually.
    images: list[VisionImage] = field(default_factory=list)


@dataclass(frozen=True)
class LLMResult:
    role: Role
    model: str
    content: str
    reasoning: str
    prompt_tokens: int
    completion_tokens: int
    elapsed_s: float
    # Canonical OpenAI completion finish_reason — "stop", "length",
    # "content_filter", "tool_calls". Used by call_and_parse to detect
    # truncation and auto-bump max_tokens on the next retry. Default
    # "stop" keeps test cassettes that construct LLMResult by hand valid.
    finish_reason: str = "stop"


def _merge_extra_body(spec: RoleSpec, override: dict[str, Any] | None) -> dict[str, Any]:
    if not override:
        return dict(spec.extra_body)
    merged = dict(spec.extra_body)
    merged.update(override)
    return merged


def _apply_images(messages: list[dict[str, Any]], images: list[VisionImage]) -> list[dict[str, Any]]:
    """Inject ``images`` into the last user message as a vision content list.

    Idempotent: if the last user content is already a list (caller built it
    via build_vision_content), images are appended instead of rewrapping.
    """
    if not images:
        return messages
    out = [dict(m) for m in messages]
    # Find last user message.
    for i in range(len(out) - 1, -1, -1):
        if out[i].get("role") == "user":
            content = out[i].get("content")
            if isinstance(content, list):
                # Already vision-style: just append image blocks.
                out[i]["content"] = content + [
                    {"type": "image_url", "image_url": {"url": _encode_image(im)}}
                    for im in images
                ]
            else:
                # Wrap text + images.
                out[i]["content"] = build_vision_content(str(content or ""), images)
            return out
    # No user message — append one.
    out.append({"role": "user", "content": build_vision_content("", images)})
    return out


def _is_transient_api_error(exc: BaseException) -> bool:
    """Retry only genuinely transient Cloud.ru failures.

    5xx («Internal Server Error», 503 «no healthy upstream» storms), 408/409
    timeouts/conflicts, 429 rate limits and connection-level errors are worth
    waiting out; any other 4xx is a caller bug — retrying just burns time.
    """
    if isinstance(exc, (APIConnectionError, RateLimitError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in (408, 409, 429) or exc.status_code >= 500
    return False


def _log_retry(retry_state: Any) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "llm.call.retry",
        attempt=retry_state.attempt_number,
        wait_s=round(getattr(retry_state.next_action, "sleep", 0.0), 1),
        error=f"{type(exc).__name__}: {exc}" if exc else "?",
    )


# Sized to ride out a multi-minute 503 burst (observed live 2026-06-10):
# 6 attempts with exp backoff 2→45s ≈ up to ~1.5 min of pure waiting on top
# of the OpenAI client's own internal retries.
@retry(
    reraise=True,
    stop=stop_after_attempt(6),
    wait=wait_exponential(multiplier=2, min=2, max=45),
    retry=retry_if_exception(_is_transient_api_error),
    before_sleep=_log_retry,
)
def _do_call(*, model: str, messages: Iterable[dict[str, Any]], max_tokens: int,
             temperature: float, extra_body: dict[str, Any]) -> tuple[Any, float]:
    acquire_rps_slot()
    client = get_client()
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=model,
        messages=list(messages),
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body=extra_body or None,
    )
    return resp, time.perf_counter() - t0


def _has_vision_blocks(messages: list[dict[str, Any]]) -> bool:
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(b.get("type") == "image_url" for b in c):
            return True
    return False


def call_role(call: LLMCall) -> LLMResult:
    """Dispatch a chat completion under the given Role.

    Retries on transient Cloud.ru errors (with exponential backoff).
    Caller is responsible for parsing `content` and re-trying with
    feedback if the JSON is malformed (see `llm.output_parsers`).

    If the Role requires vision, validates that the final message list
    contains at least one image_url block (after `images` injection).
    Vision-required roles called without images raise ValueError early —
    silent text-only fallback would hide the bug.
    """
    spec = ROLES[call.role]
    max_tok = call.max_tokens_override or spec.max_tokens
    extra_body = _merge_extra_body(spec, call.extra_body_override)
    messages = _apply_images(call.messages, call.images)

    if spec.requires_vision and not _has_vision_blocks(messages):
        raise ValueError(
            f"Role {call.role.value} requires vision input but no image_url "
            "blocks were provided (neither in messages nor via call.images)."
        )

    logger.debug(
        "llm.call.start",
        role=call.role.value,
        model=spec.model,
        max_tokens=max_tok,
        msg_count=len(messages),
        vision=_has_vision_blocks(messages),
    )
    resp, elapsed = _do_call(
        model=spec.model,
        messages=messages,
        max_tokens=max_tok,
        temperature=spec.temperature,
        extra_body=extra_body,
    )
    choice = resp.choices[0]
    msg = choice.message
    content = (msg.content or "").strip()
    reasoning = (getattr(msg, "reasoning", None) or "").strip()
    finish_reason = getattr(choice, "finish_reason", "stop") or "stop"
    usage = resp.usage
    result = LLMResult(
        role=call.role,
        model=spec.model,
        content=content,
        reasoning=reasoning,
        prompt_tokens=getattr(usage, "prompt_tokens", 0),
        completion_tokens=getattr(usage, "completion_tokens", 0),
        elapsed_s=elapsed,
        finish_reason=finish_reason,
    )
    logger.info(
        "llm.call.done",
        role=call.role.value,
        model=spec.model,
        elapsed_s=round(elapsed, 3),
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        reasoning_len=len(reasoning),
        content_len=len(content),
        finish_reason=finish_reason,
    )
    return result


def ping(role: Role = Role.CLASSIFIER) -> LLMResult:
    """Cheap liveness probe — used in M1 acceptance to verify reachability."""
    return call_role(LLMCall(
        role=role,
        messages=[{"role": "user", "content": "Reply with exactly: pong"}],
        max_tokens_override=200,  # enough for reasoning models, harmless for others
    ))
