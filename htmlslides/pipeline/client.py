"""Kimi-K2.6 через Cloud.ru FM: RPS-гейт, plain-prompt JSON + Pydantic + 1 ретрай.

response_format на Cloud.ru не поддержан, поэтому структурированный вывод —
просьба вернуть JSON + extract_json + Pydantic-валидация + один ретрай.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional, Type, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

DEFAULT_BASE_URL = "https://foundation-models.api.cloud.ru/v1"
DEFAULT_MODEL = "moonshotai/Kimi-K2.6"

T = TypeVar("T", bound=BaseModel)

# Process-wide cap on CONCURRENT Cloud.ru requests, shared by every KimiClient
# instance (each build makes its own client). This is the single safety valve that
# lets us run several builds in parallel and raise filler/planner concurrency
# without ever exceeding what Cloud.ru tolerates. Measured 2026-06-21: light
# requests held to ~60 concurrent with 0 rejects (first 429 at 80), heavy reasoning
# 12+ with 0 rejects — so 18 is a deliberately conservative ceiling with headroom.
# Per-instance _RateGate still smooths requests/sec; this bounds simultaneity.
_MAX_INFLIGHT = max(1, int(os.environ.get("CLOUDRU_MAX_INFLIGHT", "18")))
_INFLIGHT = threading.BoundedSemaphore(_MAX_INFLIGHT)


class LLMFormatError(RuntimeError):
    """Модель не вернула валидный JSON после ретрая (или JSON не найден)."""


class _RateGate:
    """Не больше rps запросов в секунду, потокобезопасно (для параллельного filler)."""

    def __init__(self, rps: float) -> None:
        self._interval = 1.0 / rps
        self._lock = threading.Lock()
        self._next_at = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            self._next_at = max(now, self._next_at) + self._interval
        if wait > 0:
            time.sleep(wait)


def extract_json(text: str) -> str:
    """Достать JSON-объект из ответа модели: ```json-блок либо первый валидный {...}."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start = candidate.find("{")
    if start == -1:
        raise LLMFormatError("no JSON object in model reply")
    candidate = candidate[start:]
    try:
        _, end = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError as exc:
        raise LLMFormatError(f"invalid JSON object in model reply: {exc}") from exc
    return candidate[:end]


def image_part(png_path: str | Path) -> dict:
    """Vision-вход Cloud.ru FM: image_url с base64 data-URL."""
    b64 = base64.b64encode(Path(png_path).read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}


class KimiClient:
    """Обёртка openai-клиента: модель/гейт/JSON-валидация. transport= для тестов.

    rps по умолчанию берётся из env HTMLSLIDES_RPS; если env не задан — 10.
    Явный аргумент rps= всегда имеет приоритет над env.
    """

    def __init__(self, api_key: Optional[str] = None, *,
                 base_url: Optional[str] = None,
                 model: Optional[str] = None,
                 rps: Optional[float] = None,
                 timeout: float = 300.0,
                 max_retries: int = 5,
                 extra_body: Optional[dict] = None,
                 transport=None) -> None:
        self.model = model or os.environ.get("CLOUDRU_MODEL", DEFAULT_MODEL)
        # Per-request kwargs for every call (e.g. {"thinking": {"type": "disabled"}}
        # to switch Kimi-K2.6 out of multi-minute reasoning for simple edits).
        self._extra_body = extra_body
        if rps is None:
            _raw = os.environ.get("HTMLSLIDES_RPS", "10")
            try:
                rps = float(_raw)
            except ValueError:
                raise ValueError(
                    f"HTMLSLIDES_RPS={_raw!r} is not a valid float") from None
        if rps <= 0:
            raise ValueError(f"HTMLSLIDES_RPS must be > 0, got {rps!r}")
        self._gate = _RateGate(rps)
        if transport is not None:
            self._client = transport
            return
        key = api_key or os.environ.get("CLOUDRU_API_KEY", "")
        if not key:
            raise RuntimeError("CLOUDRU_API_KEY не задан (env или api_key=)")
        self._client = OpenAI(
            api_key=key,
            base_url=base_url or os.environ.get("CLOUDRU_BASE_URL", DEFAULT_BASE_URL),
            max_retries=max_retries,  # 429/5xx ретраит сам openai-клиент (экспонента)
            timeout=timeout)

    def chat(self, messages: list[dict], *, max_tokens: int = 4096,
             temperature: float = 0.3,
             extra_body: Optional[dict] = None) -> str:
        # Per-call extra_body overrides the instance default. Lets one client run
        # reasoning ON for hard calls (planner/vision-QA) yet OFF for cheap
        # text-only calls (filler/autofix) — Kimi-K2.6's reasoning otherwise adds
        # 1-4 min per call. None here = fall back to the instance default.
        body = extra_body if extra_body is not None else self._extra_body
        self._gate.acquire()
        # Bound process-wide simultaneous Cloud.ru calls (shared across all builds).
        # Acquire around ONLY the network call and always release (context manager),
        # so a slow/failing call can't leak a slot. Each task does one call then
        # frees the slot — no task holds a slot while awaiting another, so the
        # semaphore can't deadlock even with many parallel filler/planner threads.
        with _INFLIGHT:
            resp = self._client.chat.completions.create(
                model=self.model, messages=messages,
                max_tokens=max_tokens, temperature=temperature,
                extra_body=body or None)
        return resp.choices[0].message.content or ""

    def chat_json(self, messages: list[dict], model_cls: Type[T], *,
                  max_tokens: int = 4096, retries: int = 2,
                  extra_body: Optional[dict] = None) -> T:
        """plain-prompt JSON + Pydantic, до `retries` повторов при невалидном ответе.

        Kimi на Cloud.ru без response_format изредка отдаёт не-JSON (проза/пусто);
        два повтора с жёстким «верни ТОЛЬКО JSON» гасят такие транзиентные осечки.
        """
        convo = messages
        last_exc: Exception | None = None
        for _ in range(retries + 1):
            reply = self.chat(convo, max_tokens=max_tokens, extra_body=extra_body)
            try:
                return model_cls.model_validate_json(extract_json(reply))
            except (LLMFormatError, ValidationError) as exc:
                last_exc = exc
                convo = messages + [
                    {"role": "assistant", "content": reply},
                    {"role": "user", "content":
                        f"Ответ не прошёл валидацию: {exc}. "
                        "Верни ТОЛЬКО исправленный JSON-объект, без пояснений."}]
        raise LLMFormatError(f"invalid JSON after {retries} retries: {last_exc}")
