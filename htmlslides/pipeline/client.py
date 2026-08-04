"""Cloud.ru FM client: RPS-гейт, plain-prompt JSON + Pydantic + 1 ретрай.

Модель по умолчанию — MiniMax-M3 (vision, content всегда заполнен, reasoning
короткий). Переопределение через env CLOUDRU_MODEL (полезно для A/B-сравнений
и отката на другую модель). response_format на Cloud.ru не поддержан, поэтому
структурированный вывод — просьба вернуть JSON + extract_json + Pydantic-
валидация + один ретрай.
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Type, TypeVar

from openai import (APIConnectionError, APITimeoutError, InternalServerError,
                    OpenAI, RateLimitError)
from pydantic import BaseModel, ValidationError

DEFAULT_BASE_URL = "https://foundation-models.api.cloud.ru/v1"
DEFAULT_MODEL = "MiniMaxAI/MiniMax-M3"

# Резерв на время недоступности основной модели. Kimi-K2.6 был дефолтом до 339298a
# и мультимодален — то есть заведомо тянет ВЕСЬ пайплайн, включая vision-роли.
# Пустая строка в env = резерва нет (сборка честно падает вместо подмены модели).
#
# 2026-08-04 MiniMax-M3 перестала отвечать на стороне Cloud.ru (таймаут на каждом
# вызове), Kimi в ту же минуту отвечала за 1,9 с. Фолбэка не было вовсе, и две
# сборки ползли по 17-18 минут до отмены пользователем.
FALLBACK_MODEL = os.environ.get("CLOUDRU_FALLBACK_MODEL", "moonshotai/Kimi-K2.6")

# Проба перед сборкой: узнать о мёртвой модели за секунды. Боевые таймаут и ретраи
# здесь недопустимы — они и есть то, что превращало сбой в 40-минутное ожидание.
PROBE_TIMEOUT = 30.0
# ≥256: MiniMax ВСЕГДА эмитит короткий reasoning перед content, и при меньшем
# бюджете возвращает пустую строку — проба объявила бы живую модель мёртвой.
PROBE_MAX_TOKENS = 256

# Сколько транзиентных сбоев ПОДРЯД считаем отказом модели, а не блипом. Ловит
# деградацию, начавшуюся уже после пробы. Одиночный сбой штатно стоит одного
# слайда (см. пофазную деградацию) — менять из-за него модель на всю деку хуже.
SWITCH_AFTER = 3

# Транзиентные сбои API на ОДИН вызов, уже после собственных ретраев openai-клиента
# (лимит/таймаут/обрыв/5xx). Шлюз Cloud.ru отдаёт их пачками на несколько минут, а
# ретраи SDK укладываются в ~15 с, поэтому «пережить блип» на уровне клиента нельзя —
# гасить их обязана каждая пофазная обёртка, деградируя ОДИН слайд/раздел.
#
# Живёт здесь, а не в filler/planner: 2026-07-28 503 из шлюза убил 34-минутную
# сборку целиком именно потому, что кортеж был скопирован в двух фазах, а третья
# (хвост build.py — vision-QA/autofix) про него не знала. Один источник правды —
# чтобы новая фаза не могла «забыть» его снова.
TRANSIENT_API_ERRORS = (
    RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)

# Худший случай ОДНОГО вызова = timeout × (max_retries + 1): ретраи openai-SDK
# спят миллисекунды, всё время съедают сами висящие попытки. Watchdog сборки
# кооперативный — прервать висящий HTTP-запрос он не может, поэтому этот худший
# случай обязан быть много меньше BUILD_TIMEOUT_SEC (2400 с).
#
# Было 300 × 6 = 30 мин на ОДИН вызов. 2026-07-28 шлюз Cloud.ru деградировал, и
# два запроса засели в таком цикле (по журналу — паузы ровно по ~4,5 мин между
# ретраями): сборка упёрлась в 40-минутный watchdog, хотя работы оставалось на
# минуты. Теперь 180 × 3 = 9 мин — вписывается в бюджет, а редкие транзиентные
# сбои гасятся пофазной деградацией (TRANSIENT_API_ERRORS), а не ретраями SDK.
DEFAULT_TIMEOUT = 180.0
DEFAULT_MAX_RETRIES = 2

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

# Потолок эскалации max_tokens при обрыве ответа по длине (finish_reason=length).
_TOKEN_CAP = 32768


class LLMFormatError(RuntimeError):
    """Модель не вернула валидный JSON после ретрая (или JSON не найден)."""


class ProviderUnavailable(RuntimeError):
    """Собирать нечем: ни основная, ни резервная модель не отвечают.

    Отдельный класс, а не openai-исключение: это не «сбой одного вызова», а вывод
    о состоянии провайдера, и вести он должен к честному отказу сборки, а не к
    пофазной деградации (иначе пользователь получает деку из пустых заглушек).
    """


def degradation_is_total(degraded: int, total: int) -> bool:
    """Деградировала ли БОЛЬШАЯ ЧАСТЬ работы из-за сбоев API?

    Один источник правды для планировщика и филлера: обе фазы обязаны отвечать на
    этот вопрос одинаково, иначе «дека из заглушек» пролезет через ту, которая
    забыла про порог. Строго больше половины — единичные блипы штатны и по
    задумке стоят одного слайда.
    """
    return total > 0 and degraded * 2 > total


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
    """Cloud.ru FM client: модель/гейт/JSON-валидация. transport= для тестов.

    Модель по умолчанию — MiniMax-M3 (env CLOUDRU_MODEL для переопределения).
    Имя класса KimiClient сохранено для совместимости импортов по всему репо.

    rps по умолчанию берётся из env HTMLSLIDES_RPS; если env не задан — 10.
    Явный аргумент rps= всегда имеет приоритет над env.
    """

    def __init__(self, api_key: Optional[str] = None, *,
                 base_url: Optional[str] = None,
                 model: Optional[str] = None,
                 fallback_model: Optional[str] = None,
                 rps: Optional[float] = None,
                 timeout: float = DEFAULT_TIMEOUT,
                 max_retries: int = DEFAULT_MAX_RETRIES,
                 extra_body: Optional[dict] = None,
                 transport=None) -> None:
        self.model = model or os.environ.get("CLOUDRU_MODEL", DEFAULT_MODEL)
        # Резерв не может совпадать с основной: иначе «переключение» было бы
        # пустышкой, молча удваивающей ожидание на мёртвом провайдере.
        fb = FALLBACK_MODEL if fallback_model is None else fallback_model
        self._fallback = fb if fb and fb != self.model else ""
        self._switched = False
        self._switch_lock = threading.Lock()
        self._streak = 0
        # Куда сообщать о подмене модели. build.py подставляет сюда progress —
        # решение, принятое за пользователя, он обязан увидеть, а не вычитать из
        # журнала. Вызывается ровно один раз (переключение липкое).
        self.on_notice: Callable[[str], None] = lambda message: None
        # Per-request kwargs for every call (e.g. {"thinking": {"type": "disabled"}}
        # to suppress reasoning for simple edits — harmless on models that ignore it).
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
        # usage копим суммарно и потокобезопасно: chat() зовут параллельно
        # (design_exact_deck/fill_deck). cached_tokens покажет, включился ли
        # кэш общего префикса промпта (главный рычаг экономии Этапа 2).
        self._usage_lock = threading.Lock()
        self.usage_total = {"prompt_tokens": 0, "completion_tokens": 0,
                            "cached_tokens": 0, "calls": 0}
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

    # ── доступность модели ───────────────────────────────────────────────────

    @property
    def fallback_model(self) -> str:
        """Имя резервной модели ("" — резерва нет). Публично, потому что о
        доступности резерва спрашивает не только сборка (см. webapp/model_health)."""
        return self._fallback

    def preflight(self, notice: Optional[Callable[[str], None]] = None) -> None:
        """Проверить основную модель до начала работы; при отказе — уйти на резерв.

        Стоит ~2 с и несколько токенов на здоровом провайдере. Смысл — сдвинуть
        обнаружение сбоя с «через 9 минут на каждом вызове» на «через полминуты
        один раз»: прод-инцидент 2026-08-04 стоил двух отменённых сборок именно
        потому, что о мёртвой модели никто не узнавал.

        Пробуем и резерв тоже: если молчат обе, честный отказ ДО планирования
        лучше, чем сорок минут работы, заканчивающихся декой из заглушек.
        """
        if notice is not None:
            self.on_notice = notice
        if self.probe(self.model):
            return
        dead = self.model
        if not self._switch(f"модель {dead} не отвечает"):
            raise ProviderUnavailable(
                f"сервис ИИ не отвечает (модель {dead}), резервной модели нет")
        if not self.probe(self.model):
            raise ProviderUnavailable(
                f"сервис ИИ не отвечает: молчат и основная модель ({dead}), "
                f"и резервная ({self.model})")

    def probe(self, model: str) -> bool:
        """Дешёвый вызов «жива ли модель». Любая ошибка API = не жива.

        Состояние клиента НЕ меняет (в отличие от preflight, который при отказе
        липко переезжает на резерв) — поэтому годится и для read-only опроса
        доступности, показываемого в UI."""
        import openai
        try:
            self._probe_client().chat.completions.create(
                model=model, messages=[{"role": "user", "content": "ping"}],
                max_tokens=PROBE_MAX_TOKENS, temperature=0)
        except openai.APIError:
            return False
        return True

    def _probe_client(self):
        """Клон клиента с коротким таймаутом и без ретраев (боевые 180 × 3 на
        пробе означали бы ровно то ожидание, которое проба и должна убрать)."""
        with_options = getattr(self._client, "with_options", None)
        if with_options is None:                       # тестовый транспорт
            return self._client
        return with_options(timeout=PROBE_TIMEOUT, max_retries=0)

    def _switch(self, reason: str) -> bool:
        """Липко перевести клиент на резервную модель. False = резерва нет.

        Липко и однократно: возврат назад заставлял бы клиент метаться между
        моделями на деградирующем шлюзе, платя полным таймаутом за каждую пробу.
        """
        with self._switch_lock:
            if self._switched or not self._fallback:
                return self._switched
            self.model, self._switched, self._streak = self._fallback, True, 0
        self.on_notice(
            f"notice: {reason} — собираю на резервной модели ({self.model}). "
            "Оформление может отличаться от обычного.")
        return True

    def _note_call(self, ok: bool) -> None:
        """Серия транзиентных сбоев подряд = отказ модели, а не блип."""
        if ok:
            self._streak = 0
            return
        with self._switch_lock:
            self._streak += 1
            fire = self._streak >= SWITCH_AFTER and not self._switched
        if fire:
            self._switch("основная модель ИИ перестала отвечать")

    @staticmethod
    def _usage_get(src, key: str, default=0):
        """Прочитать поле usage независимо от формы ответа. Cloud.ru через
        openai-SDK отдаёт typed-объект CompletionUsage (атрибуты) — его getattr
        читает верно. Но часть OpenAI-совместимых шлюзов и model_dump-пути отдают
        usage ПЛОСКИМ словарём, на котором getattr молча вернул бы 0. Читаем обе
        формы защитно: форма зависит от шлюза, полагаться на объект нельзя."""
        if src is None:
            return default
        val = src.get(key, default) if isinstance(src, dict) else getattr(src, key, default)
        return default if val is None else val

    def _record_usage(self, resp) -> None:
        """Сложить usage ответа в self.usage_total. Моки без usage игнорируем."""
        usage = getattr(resp, "usage", None)
        if usage is None and isinstance(resp, dict):
            usage = resp.get("usage")
        details = self._usage_get(usage, "prompt_tokens_details", None)
        cached = self._usage_get(details, "cached_tokens", 0)
        with self._usage_lock:
            if usage is not None:
                self.usage_total["prompt_tokens"] += self._usage_get(usage, "prompt_tokens", 0)
                self.usage_total["completion_tokens"] += self._usage_get(usage, "completion_tokens", 0)
                self.usage_total["cached_tokens"] += cached
            self.usage_total["calls"] += 1

    def chat(self, messages: list[dict], *, max_tokens: int = 4096,
             temperature: float = 0.3,
             extra_body: Optional[dict] = None) -> str:
        return self.chat_ex(messages, max_tokens=max_tokens,
                            temperature=temperature, extra_body=extra_body)[0]

    def chat_ex(self, messages: list[dict], *, max_tokens: int = 4096,
                temperature: float = 0.3,
                extra_body: Optional[dict] = None) -> tuple[str, Optional[str]]:
        """chat + finish_reason. Обрыв по длине (finish_reason="length") раньше был
        НЕВИДИМ: обрезанный JSON/HTML неотличим от полного, а ретраи с тем же
        бюджетом гарантированно повторяли обрыв. Теперь при "length" бюджет
        удваивается (до 2 эскалаций, потолок _TOKEN_CAP), а вызывающий код видит
        финальный finish_reason и может обработать остаточный обрыв явно."""
        # Per-call extra_body overrides the instance default. Lets one client run
        # reasoning ON for hard calls (planner/vision-QA) yet OFF for cheap
        # text-only calls (filler/autofix) — reasoning-heavy models otherwise add
        # 1-4 min per call. None here = fall back to the instance default.
        body = extra_body if extra_body is not None else self._extra_body
        content, finish = "", None
        for _ in range(3):  # первый вызов + до 2 эскалаций бюджета
            self._gate.acquire()
            # Bound process-wide simultaneous Cloud.ru calls (shared across all
            # builds). Acquire around ONLY the network call and always release
            # (context manager), so a slow/failing call can't leak a slot. Each
            # task does one call then frees the slot — no task holds a slot while
            # awaiting another, so the semaphore can't deadlock even with many
            # parallel filler/planner threads.
            try:
                with _INFLIGHT:
                    resp = self._client.chat.completions.create(
                        model=self.model, messages=messages,
                        max_tokens=max_tokens, temperature=temperature,
                        extra_body=body or None)
            except TRANSIENT_API_ERRORS:
                # Единственная точка, через которую проходят ВСЕ вызовы пайплайна:
                # здесь и только здесь видно, что сбоит не один слайд, а модель.
                # Сам сбой пробрасываем — гасить его по-прежнему обязана фаза.
                self._note_call(ok=False)
                raise
            self._note_call(ok=True)
            self._record_usage(resp)
            choice = resp.choices[0]
            content = choice.message.content or ""
            finish = getattr(choice, "finish_reason", None)
            if finish != "length" or max_tokens >= _TOKEN_CAP:
                break
            max_tokens = min(_TOKEN_CAP, max_tokens * 2)
        return content, finish

    def chat_json(self, messages: list[dict], model_cls: Type[T], *,
                  max_tokens: int = 4096, retries: int = 2,
                  extra_body: Optional[dict] = None) -> T:
        """plain-prompt JSON + Pydantic, до `retries` повторов при невалидном ответе.

        Cloud.ru FM без response_format изредка отдаёт не-JSON (проза/пусто);
        два повтора с жёстким «верни ТОЛЬКО JSON» гасят такие транзиентные осечки.
        """
        convo = messages
        last_exc: Exception | None = None
        for _ in range(retries + 1):
            reply, finish = self.chat_ex(convo, max_tokens=max_tokens,
                                         extra_body=extra_body)
            if finish == "length":
                # Обрезанный JSON бесполезно «чинить» диалогом с тем же бюджетом
                # (chat_ex уже эскалировал до потолка) — падаем с внятной причиной.
                raise LLMFormatError(
                    "ответ модели обрезан по лимиту токенов (finish_reason=length) "
                    "даже после эскалации бюджета — упростите запрос")
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
