"""/htmlnew — сборка self-contained HTML-деки пакетом htmlslides.

Не LangGraph-узел: build_deck вызывается целиком, без чекпоинтов (при обрыве
дешевле перезапустить — сборка ~4–10 мин). Вызывается из run_pipeline по
ветке Mode.HTMLNEW; исключения наружу — терминальный failed эмитит run_pipeline.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

import structlog

from schemas.session import SessionState, Stage
from webapp import pricing
from webapp.paths import session_dir
from worker import progress

logger = structlog.get_logger(__name__)

# Префиксы прогресс-сообщений build_deck → (стадия бота, грубый %).
# Источник истины по префиксам: htmlslides/pipeline/build.py (progress(...)).
_STAGE_MAP: list[tuple[str, Stage, int]] = [
    ("parse:", Stage.PARSING, 10),
    ("rebrand", Stage.PARSING, 15),       # "rebrand:" и "rebrand-скриншоты недоступны"
    ("plan:", Stage.CLASSIFYING, 25),
    ("fill:", Stage.DESIGNING, 45),
    ("assemble:", Stage.RENDERING, 65),
    ("lint:", Stage.VALIDATING, 75),
    ("vision-qa", Stage.VALIDATING, 80),  # "vision-qa:" и "vision-qa пропущен"
    ("autofix:", Stage.AUTOFIXING, 90),
    ("done:", Stage.FINALIZING, 95),
]


# Пер-слайдовый прогресс заполнения: "fill: слайд k/N" → плавный pct внутри
# стадии DESIGNING (фаза fill занимает 45→63, перед assemble на 65).
_FILL_SLIDE_RE = re.compile(r"^fill:\s*слайд\s+(\d+)\s*/\s*(\d+)")
_FILL_PCT_LO, _FILL_PCT_HI = 45, 63


def map_progress(message: str) -> tuple[Stage, int] | None:
    """Сообщение build_deck → (Stage, pct); None — стадию не менять (warn и пр.)."""
    m = _FILL_SLIDE_RE.match(message)
    if m:
        k, n = int(m.group(1)), max(1, int(m.group(2)))
        pct = _FILL_PCT_LO + round((_FILL_PCT_HI - _FILL_PCT_LO) * min(k, n) / n)
        return Stage.DESIGNING, pct
    for prefix, stage_, pct in _STAGE_MAP:
        if message.startswith(prefix):
            return stage_, pct
    return None


def pick_mode(input_path: Path) -> str:
    """pptx — rebrand (планировщик видит скриншоты исходника), остальное — auto."""
    return "rebrand" if input_path.suffix.lower() == ".pptx" else "auto"


def run_htmlnew(state: SessionState) -> dict[str, Any]:
    """Синхронная сборка. Возвращает summary того же вида, что run_pipeline."""
    from htmlslides.pipeline.build import build_deck  # лениво: пакет есть только в worker
    from htmlslides.pipeline.client import KimiClient

    session_id = state.session_id
    input_path = Path(state.input_s3_key)
    stem = Path(state.source_filename or input_path.name).stem or "deck"
    # Единый корень с webapp (paths.session_dir): раньше здесь был _session_workdir
    # из бот-эры с ДРУГИМ дефолтным корнем (…/slidesbot вместо …/slidesapp/sessions),
    # из-за чего результат жил вне session_dir и не попадал под 24ч-ретеншен.
    out = session_dir(session_id) / f"{stem}.html"
    log = logger.bind(session_id=session_id)

    current: tuple[Stage, int] = (Stage.PARSING, 5)
    phase_started = time.monotonic()

    def on_progress(message: str) -> None:
        nonlocal current, phase_started
        mapped = map_progress(message)
        if mapped is not None:
            if mapped[0] is not current[0]:
                # Длительность каждой фазы — в журнал. Без неё разбор прод-таймаута
                # сводится к подсчёту строк httpx вручную: 2026-07-28 именно так и
                # пришлось искать, какая фаза съела 40-минутный бюджет.
                log.info("htmlnew.phase", phase=current[0].value,
                         seconds=round(time.monotonic() - phase_started, 1))
                phase_started = time.monotonic()
            current = mapped
        progress.stage(session_id, current[0], current[1], detail=message)

    def check_cancel() -> None:
        """Чекпоинт «варианта A»: build_deck зовёт это в начале каждого слайда.
        Если пользователь нажал «Остановить», прерываем сборку до нового LLM-вызова
        — уже идущие вызовы (≤workers) дойдут до конца, новых не стартует."""
        if progress.is_cancelled(session_id):
            raise progress.Cancelled(session_id)

    def wrapup() -> bool:
        """Мягкий дедлайн раннера: бюджет почти исчерпан — пропустить косметику.
        Не отмена: дека всё равно доводится до файла. Упереться в watchdog хуже —
        там пользователь не получает ничего (прод-таймауты 2026-07-28)."""
        return progress.should_wrapup(session_id)

    mode_arg = "exact" if state.exact_transfer else pick_mode(input_path)
    # Движок сам владеет клиентом, чтобы после сборки прочитать накопленный
    # usage_total и посчитать рубли. В exact LLM нет — клиент не создаём (там
    # он не нужен и не должен требовать API-ключ).
    client = None if mode_arg == "exact" else KimiClient()
    progress.stage(session_id, Stage.PARSING, 5, detail="старт сборки HTML")
    log.info("htmlnew.start", input=str(input_path), mode=mode_arg)
    result = build_deck(
        input_path,
        out,
        mode=mode_arg,
        vision=True,
        freeform_ok=True,        # включён управляемый freeform (вариант B); в exact игнорируется
        client=client,
        progress=on_progress,
        check_cancel=check_cancel,
        wrapup=wrapup,
    )
    log.info("htmlnew.phase", phase=current[0].value,       # хвостовая фаза
             seconds=round(time.monotonic() - phase_started, 1))
    log.info("htmlnew.done", result=str(result))
    # Расход прогона: токены накопились в client.usage_total (тот же объект,
    # что мутировал build_deck). Считаем рубли по прайсу и кладём в done →
    # раннер/БД → строка статистики в «Истории». В exact клиента нет → None.
    prompt_tokens = completion_tokens = cost = None
    if client is not None:
        usage = client.usage_total
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        cost = pricing.cost_rub(prompt_tokens, completion_tokens)
        calls = usage.get("calls", 0)
        # 0 ₽ бывает по двум разным причинам — разводим их в логах, иначе оба
        # выглядят одинаково «молча»:
        if not calls:
            # Ни одного успешного вызова модели: дека собрана детерминированным
            # fallback, т.к. LLM недоступен (сеть/TLS/таймаут — вызовы падали и
            # planner/filler уходили в section_fallback). Это НЕ баг подсчёта:
            # считать было нечего. Главный симптом реальных нулей в «Истории».
            log.warning("htmlnew.no_llm_calls",
                        model=getattr(client, "model", None))
        elif not prompt_tokens and not completion_tokens:
            # Вызовы были, а токенов ноль — провайдер не прислал usage в ответе.
            log.warning("htmlnew.usage_empty", calls=calls,
                        model=getattr(client, "model", None))
    progress.done(session_id, detail="HTML-дека готова", result_path=str(result),
                  prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                  cost_rub=cost)
    return {"ok": True, "session_id": session_id, "stage": Stage.DONE.value}
