"""Parse LLM JSON output into a Pydantic model with 1 retry on schema error.

Cloud.ru FM doesn't support `response_format: json_schema` (per
`<project>/memory/cloudru_fm_api.md`). The contract is plain prompt
+ Pydantic validation + 1 retry-with-feedback, empirically 4/4 schema_ok
on GLM-OFF and DeepSeek.
"""
from __future__ import annotations

import json
import re
from typing import Any, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from llm.client import LLMCall, LLMResult, call_role
from llm.roles import ROLES, Role


# Hard ceiling on auto-bumped max_tokens. Beyond this we accept the
# failure rather than burn more cost on a likely-broken role/prompt.
# 24000 = empirical worst case observed 2026-06-04 (Kimi vision visual
# verifier on 14-slide deck: completion_tokens=22502). Static defaults
# in roles.py are tuned to make truncation rare; this ceiling is the
# rare-case safety net, not the steady state.
_TRUNCATION_TOKENS_CEILING = 24000


def _is_truncated(result: LLMResult) -> bool:
    """True if the call ran out of token budget mid-output.

    Two signals, either is sufficient:
      • ``finish_reason == "length"`` — canonical OpenAI cap-hit marker.
      • Empty content — Kimi vision sometimes spends the entire budget on
        reasoning and emits zero content tokens. ``finish_reason`` is then
        also "length" but we keep the empty-content check as a belt-and-
        braces for providers that mis-report finish_reason.
    """
    return result.finish_reason == "length" or not result.content.strip()

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _strip_fences(text: str) -> str:
    """Some models still emit ```json fences despite instructions — peel them."""
    return _FENCE_RE.sub("", text).strip()


def _find_first_json_object(text: str) -> str | None:
    """Locate the first balanced { ... } substring. Returns None if not found."""
    text = _strip_fences(text)
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start:i + 1]
    return None


def parse_json_or_raise(content: str) -> dict[str, Any]:
    """Best-effort JSON extraction. Raises ValueError with a short reason."""
    blob = _find_first_json_object(content) or _strip_fences(content)
    if not blob:
        raise ValueError("no JSON object found in model output")
    try:
        return json.loads(blob)
    except json.JSONDecodeError as e:
        raise ValueError(f"JSONDecodeError: {e.msg} at pos {e.pos}") from e


def call_and_parse(
    role: Role,
    messages: list[dict[str, Any]],
    model_cls: type[T],
    *,
    images: list[Any] | None = None,
    max_retries: int = 1,
) -> tuple[T, LLMResult]:
    """Call the role, parse content as JSON, validate against ``model_cls``.

    On validation/parse error, append a feedback user message describing the
    issue and retry once (or ``max_retries`` times). If still failing, raises
    the last error so the caller can decide HALT/skip.

    Returns:
        (model_instance, raw LLMResult of the final attempt).
    """
    msgs = [dict(m) for m in messages]
    last_err: Exception | None = None
    last_result: LLMResult | None = None
    tokens_override: int | None = None
    # One-shot truncation auto-bump that does NOT consume a feedback-style
    # retry. Rationale: appending an empty/truncated assistant reply and a
    # "fix your JSON" user turn is useless when the cap is the real problem
    # — the next attempt will just truncate at the same point. So when we
    # detect truncation we double the budget and re-issue the SAME prompt.
    bumped = False
    attempt = 0
    while True:
        result = call_role(LLMCall(
            role=role,
            messages=msgs,
            images=list(images or []),
            max_tokens_override=tokens_override,
        ))
        last_result = result
        try:
            data = parse_json_or_raise(result.content)
            model = model_cls.model_validate(data)
            if attempt > 0 or bumped:
                logger.info(
                    "llm.parse.retry_ok",
                    role=role.value, attempt=attempt, bumped=bumped,
                    tokens=tokens_override or ROLES[role].max_tokens,
                )
            return model, result
        except (ValueError, ValidationError) as e:
            last_err = e
            logger.warning(
                "llm.parse.fail",
                role=role.value,
                attempt=attempt,
                bumped=bumped,
                finish_reason=result.finish_reason,
                content_len=len(result.content),
                error=str(e)[:300],
                content_head=result.content[:200],
            )
            # Truncation auto-bump — runs at most once per call_and_parse.
            if not bumped and _is_truncated(result):
                current = tokens_override or ROLES[role].max_tokens
                new_tokens = min(current * 2, _TRUNCATION_TOKENS_CEILING)
                if new_tokens > current:
                    bumped = True
                    tokens_override = new_tokens
                    logger.warning(
                        "llm.parse.bump_tokens",
                        role=role.value,
                        from_tokens=current,
                        to_tokens=new_tokens,
                        reason=("length" if result.finish_reason == "length" else "empty_content"),
                    )
                    continue  # same prompt, larger budget — does NOT consume attempt
                # Already at ceiling: fall through to feedback retry / break.
                logger.warning(
                    "llm.parse.bump_ceiling_hit",
                    role=role.value, tokens=current, ceiling=_TRUNCATION_TOKENS_CEILING,
                )

            if attempt >= max_retries:
                break
            attempt += 1
            # Feedback turn — append assistant's bad output + user correction.
            # Only useful when content is non-empty (schema error, not truncation).
            feedback = (
                "Предыдущий ответ невалиден по схеме. Ошибка: "
                f"{type(e).__name__}: {str(e)[:500]}.\n"
                "Верни ТОЛЬКО валидный JSON по схеме из system-сообщения. "
                "Без markdown-ограждений, без пояснений."
            )
            msgs.append({"role": "assistant", "content": result.content})
            msgs.append({"role": "user", "content": feedback})

    assert last_err is not None and last_result is not None
    raise last_err
