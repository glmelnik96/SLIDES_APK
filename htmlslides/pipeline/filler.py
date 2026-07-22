"""Роль 2 — Slide Filler: слоты по контракту / freeform-HTML; параллельно; autofix.

Шаблонный слайд: JSON строго по слот-контракту, нарушение ловит
library.validate_content -> 1 ретрай -> FillError.
Freeform: HTML-фрагмент только на классах deck.css (allowlist из линтера).
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel, Field

from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)

from ..brand import brand_rules
from ..library import TemplateLibrary
from ..models import DeckPlan, SlidePlan
from .client import KimiClient, LLMFormatError
from .linter import ALLOWED_CLASSES
from .planner import slot_brief

# Transient API failures that hit a SINGLE slide (after the OpenAI client already
# exhausted its own retries): rate limits, timeouts, dropped connections, 5xx.
# Under concurrent load these are common; they must NOT abort the whole deck.
_TRANSIENT_API_ERRORS = (
    RateLimitError, APITimeoutError, APIConnectionError, InternalServerError,
)


class FillError(RuntimeError):
    """Слайд не удалось заполнить валидно после ретрая."""


class SlideContent(BaseModel):
    content: dict = Field(default_factory=dict)


# Единый источник правды бренд-правил — brand_rules(); читаем один раз при импорте.
# ВНИМАНИЕ: блок встраивается в FREEFORM_SYSTEM, который позже идёт через
# .format(classes=...). В rules.md НЕ должно быть фигурных скобок {} —
# иначе .format() упадёт (регрессия закрыта test_brand.py).
_BRAND_BLOCK = f"\n\nБРЕНД-ПРАВИЛА:\n{brand_rules()}"

FILLER_SYSTEM = """\
Ты заполняешь слоты HTML-шаблона слайда Cloud.ru.
Дано: контракт слотов (JSON) и бриф контента слайда.
Верни ТОЛЬКО JSON вида {"content":{...}} — ключи строго по контракту.
Жёстко соблюдай max_chars / max_items; required-слоты обязательны;
лишних ключей не добавляй. Пиши по-русски, кратко и фактично, без выдумок.
Для карточных шаблонов (cards-6, three-col, grid-2x2): если пункт — это «Имя —
описание» (сервис/опция/тариф + пояснение), КЛАДИ короткое имя в "heading"
(≤24 симв., без тире), а пояснение в "text". НЕ склеивай имя и описание в одну
строку через тире.
Слот title/label слайда — это заголовок ЭТОГО слайда из начала его брифа (название
его раздела), а НЕ заголовок всей деки. «Заголовок деки» дан только для контекста —
НЕ копируй его в title/label и НЕ повторяй один заголовок на разных слайдах.
В числовых слотах-героях (value у stats-row / kpi / bar-chart) сохраняй значащие
суффиксы и единицы (ФЗ, %, +, млрд, ГБ, ×): «152-ФЗ», а НЕ «152». Если у пункта
нет единого числа-героя (напр. «Соответствие 152-ФЗ и 187-ФЗ»), поставь короткий
осмысленный токен С суффиксом (напр. «152-ФЗ»), а полную формулировку — в
label/caption. НЕ превращай такой пункт в голый числовой ряд вроде «152, 187».""" + _BRAND_BLOCK

FREEFORM_SYSTEM = """\
Ты верстаешь ОДИН слайд презентации Cloud.ru как HTML-фрагмент контент-зоны.
Логотип, колонтитул и номер слайда добавит система — НЕ рисуй их (никаких chrome-*).
Верни ТОЛЬКО код в блоке ```html ... ```.

Требования:
- Корень: <section class="slide" data-template="freeform"> ...контент... </section>.
- ПЕРВЫМ элементом — шапка-заголовок слайда (система поставит её сверху, как в
  остальных слайдах): <div class="content-head"><h3 class="content-head-title
  t-head-42">ЗАГОЛОВОК СЛАЙДА</h3></div> (опционально подзаголовок
  <p class="content-head-sub t-sub-36">…</p> внутри того же блока).
- После шапки — основной контент. Система поместит его в рабочую зону (1800×720).
  НЕ задавай абсолютных координат/позиций, НЕ используй класс .content и НЕ делай
  ещё один крупный заголовок в теле; верстай ОБЫЧНЫМ ПОТОКОМ сверху вниз (блоки/
  колонки). Контент должен умещаться в зону — не перегружай, лучше меньше и крупнее.
- Используй ТОЛЬКО классы: {classes}.
- Запрещено: атрибут style, теги <i>/<em>/<u>/<script>, тени, градиенты,
  скругления, курсив, неизвестные классы. Не более одного accent-block.
- Бренд: колоночная вёрстка (БЕЗ строк во всю ширину), иерархия текстов,
  точечные акценты, много воздуха; паттерны (.pattern-lines/.pattern-dots/
  .pattern-waterfall) — только как фон-акцент, не под плотным текстом.

Типографика: .t-hero-156 (огромный тезис), .t-head-42 (шапка-заголовок),
.t-head-36 (подзаголовки/единицы), .t-body-30 (наборный текст), .t-sub-36
(подзаголовок к hero), .t-number-320 (число-герой). Поверхности: .card
(карточка на фоне), .accent-block (зелёная плашка, максимум одна).

Брендовые СКЕЛЕТЫ раскладки (выбери подходящий или скомбинируй):
1) Крупный тезис слева (.t-head-42 + .t-body-30) + .pattern-lines/.pattern-dots
   справа как акцент.
2) Три колонки (.row > .col×3) с короткими блоками; в одной — .accent-block.
3) Число-герой (.t-number-320) + поясняющий .t-body-30 рядом.
Появление: оборачивай списки/группы в .m-stagger для каскада.""" + _BRAND_BLOCK

# Бюджет токенов на слайд. Запас под reasoning: при 4096 тяжёлое рассуждение
# модели съедало лимит до выдачи JSON (пустой ответ → деградация на blank).
# Сам контент слайда маленький, потолок не растит цену коротких ответов.
_FILL_MAX_TOKENS = 8192

# Заполнение слайда — это текст-в-JSON / текст-в-HTML по чёткому контракту, без
# картинок: глубокое рассуждение здесь не нужно, а на reasoning-heavy моделях
# добавляет 1-4 мин на вызов. Отключаем thinking для filler/autofix — главный
# рычаг скорости сборки. На MiniMax-M3 тумблер принимается, но reasoning и так
# короткий; при откате на другую модель через CLOUDRU_MODEL отключение полезно.
_FILL_NO_THINK = {"thinking": {"type": "disabled"}}

_FORBIDDEN_FRAGMENT = re.compile(
    r"\bstyle\s*=|<\s*(?:i|em|u|script)\b|box-shadow|text-shadow|gradient|border-radius",
    re.IGNORECASE)
_HTML_FENCE = re.compile(r"```(?:html)?\s*(.*?)```", re.DOTALL)
_SECTION = re.compile(r"<section\b.*</section>", re.DOTALL)


def fill_slide(client: KimiClient, library: TemplateLibrary, slide: SlidePlan, *,
               deck_title: str = "", extra: str = "") -> SlidePlan:
    """Заполнить один слайд. extra — доп. указания (исходный контент + замечания QA)."""
    try:
        if slide.freeform:
            return _fill_freeform(client, slide, deck_title=deck_title, extra=extra)
        return _fill_template(client, library, slide,
                              deck_title=deck_title, extra=extra)
    except LLMFormatError as exc:
        raise FillError(f"slide {slide.index}: {exc}") from exc


def fill_deck(client: KimiClient, library: TemplateLibrary, plan: DeckPlan, *,
              workers: int = 8, progress=lambda message: None,
              check_cancel=lambda: None) -> DeckPlan:
    """Параллельное заполнение всех слайдов (RPS держит гейт клиента).

    Мягкая деградация: FillError одного слайда НЕ валит всю деку — слайд
    откатывается на blank с темой в заголовке (warn в progress). Прочие сбои
    (сеть/авторизация) гасят бюджет — отменяют ещё не начатые слайды и пробрасываются.

    check_cancel — чекпоинт «вариант A»: вызывается в НАЧАЛЕ каждого слайда, до
    вызова LLM. Если пользователь нажал «Остановить», hook поднимает исключение, и
    ещё не начатый слайд не тратит токены. Уже запущенные вызовы (≤workers) дойдут
    до конца — прервать их поток Python не может, но новых не стартует."""
    aborted = threading.Event()
    total = len(plan.slides)
    done_lock = threading.Lock()
    done = 0

    def _tick() -> None:
        """Emit per-slide completion progress (thread-safe) so the UI shows live
        movement during the long parallel fill instead of one frozen message."""
        nonlocal done
        with done_lock:
            done += 1
            k = done
        progress(f"fill: слайд {k}/{total}")

    def one(slide: SlidePlan) -> SlidePlan:
        # Вариант A: остановка «кусается» ДО начала слайда — если сборку попросили
        # прекратить, ещё не запущенный слайд не зовёт LLM (нет лишних токенов).
        check_cancel()
        if aborted.is_set():  # барьер: после жёсткого сбоя соседа LLM не зовём
            return slide
        try:
            result = fill_slide(client, library, slide, deck_title=plan.title)
        except FillError as exc:
            progress(f"warn: слайд {slide.index} не заполнен ({exc}); фолбэк на blank")
            result = _fallback_slide(library, slide)
        except _TRANSIENT_API_ERRORS as exc:
            # Транзиентный сбой API на ОДНОМ слайде (лимит/таймаут/5xx после
            # собственных ретраев клиента) НЕ должен ронять всю деку — частая
            # ситуация под нагрузкой. Деградируем слайд на blank, как и FillError.
            progress(f"warn: слайд {slide.index} — сбой API ({type(exc).__name__}); "
                     "фолбэк на blank")
            result = _fallback_slide(library, slide)
        except Exception:
            aborted.set()
            raise
        _tick()
        return result

    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(one, slide) for slide in plan.slides]
        slides = [f.result() for f in futures]
    except Exception:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown()
    return plan.model_copy(update={"slides": slides})


def _fallback_slide(library: TemplateLibrary, slide: SlidePlan) -> SlidePlan:
    """Деградация проблемного слайда -> чистый blank с темой в заголовке.

    Лучше отдать одну простую брендовую заглушку, чем уронить всю деку из-за
    одной осечки модели. Заголовок blank требует ≤60 символов."""
    spec = library.get("blank")
    return slide.model_copy(update={
        "template_id": "blank", "type": spec.type, "freeform": False,
        "content": {"title": _fallback_title(_brief(slide))}})


def _fallback_title(brief: str, limit: int = 60) -> str:
    """Заголовок для blank-заглушки: первое предложение брифа, без обрезки
    посреди слова (иначе получалось уродливое «…за нескол»)."""
    text = " ".join(brief.split()).strip()              # схлопываем пробелы/переводы
    if not text:
        return "Слайд"
    first = re.split(r"(?<=[.!?…])\s", text, maxsplit=1)[0].strip(" .")
    if len(first) <= limit:
        return first
    cut = first[:limit]
    if " " in cut:                                       # режем по границе слова
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,.;:—-") + "…"


def autofix_slide(client: KimiClient, library: TemplateLibrary, slide: SlidePlan,
                  notes: list[str], *, deck_title: str = "") -> SlidePlan:
    """Один круг исправлений по замечаниям линтера/vision-QA (анти-зацикливание:
    оркестратор вызывает это не больше одного раза на слайд)."""
    listed = "\n".join(f"- {note}" for note in notes)
    extra = (f"Текущий контент слайда:\n"
             f"{json.dumps(slide.content, ensure_ascii=False)}\n\n"
             f"Замечания QA — исправь все:\n{listed}")
    return fill_slide(client, library, slide, deck_title=deck_title, extra=extra)


def _brief(slide: SlidePlan) -> str:
    return str(slide.content.get("brief", "")) or json.dumps(
        slide.content, ensure_ascii=False)


def _fill_template(client: KimiClient, library: TemplateLibrary, slide: SlidePlan, *,
                   deck_title: str, extra: str) -> SlidePlan:
    # Детерминированно заполненный слайд (напр. section-divider) несёт готовые
    # слоты вместо брифа — пропускаем без вызова LLM, если контракт уже выполнен.
    if not extra and "brief" not in slide.content and not library.validate_content(
            slide.template_id, slide.content):
        return slide
    template = library.get(slide.template_id)
    slots = json.dumps({n: slot_brief(s) for n, s in template.slots.items()},
                       ensure_ascii=False)
    user = (f"Контракт слотов шаблона «{template.id}»:\n{slots}\n\n"
            f"Заголовок деки: {deck_title}\n\nБриф слайда:\n{_brief(slide)}")
    if extra:
        user += "\n\n" + extra
    messages = [{"role": "system", "content": FILLER_SYSTEM},
                {"role": "user", "content": user}]
    filled = client.chat_json(messages, SlideContent, max_tokens=_FILL_MAX_TOKENS,
                              extra_body=_FILL_NO_THINK)
    errors = library.validate_content(slide.template_id, filled.content)
    if errors:
        details = "; ".join(f"{e.code}:{e.slot} {e.detail}".strip() for e in errors)
        retry = messages + [
            {"role": "assistant",
             "content": json.dumps({"content": filled.content}, ensure_ascii=False)},
            {"role": "user",
             "content": f"Контракт нарушен: {details}. Сократи/исправь и верни "
                        "ТОЛЬКО валидный JSON."}]
        filled = client.chat_json(retry, SlideContent, max_tokens=_FILL_MAX_TOKENS,
                                  extra_body=_FILL_NO_THINK)
        errors = library.validate_content(slide.template_id, filled.content)
        if errors:
            details = "; ".join(f"{e.code}:{e.slot}" for e in errors)
            raise FillError(f"slide {slide.index}: контракт не выполнен после "
                            f"ретрая: {details}")
    return slide.model_copy(update={"content": filled.content})


def _fill_freeform(client: KimiClient, slide: SlidePlan, *,
                   deck_title: str, extra: str) -> SlidePlan:
    system = FREEFORM_SYSTEM.format(classes=", ".join(sorted(ALLOWED_CLASSES)))
    user = f"Заголовок деки: {deck_title}\n\nБриф слайда:\n{_brief(slide)}"
    if extra:
        user += "\n\n" + extra
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    reply = client.chat(messages, max_tokens=_FILL_MAX_TOKENS,
                        extra_body=_FILL_NO_THINK)
    html = _extract_html(reply)
    problem = _freeform_problem(html)
    if problem:
        retry = messages + [
            {"role": "assistant", "content": reply},
            {"role": "user",
             "content": f"Во фрагменте проблема: {problem}"}]
        reply = client.chat(retry, max_tokens=_FILL_MAX_TOKENS,
                            extra_body=_FILL_NO_THINK)
        html = _extract_html(reply)
        problem = _freeform_problem(html)
        if problem:
            raise FillError(f"slide {slide.index}: freeform невалиден после "
                            f"ретрая: {problem}")
    return slide.model_copy(update={"content": {"html": html}})


def _freeform_problem(html: str) -> str | None:
    """Замечание к freeform-фрагменту или None, если фрагмент пригоден.

    Без проверки на завершённый <section> обрезанный/прозаический ответ модели
    (у _extract_html фолбэк — сырой reply) молча уезжал в деку как «HTML»."""
    if not _SECTION.search(html):
        return ("нет завершённого <section>…</section> (ответ обрезан или не "
                "HTML). Верни ТОЛЬКО ```html-блок с корнем "
                '<section class="slide" data-template="freeform">.')
    if _FORBIDDEN_FRAGMENT.search(html):
        return ("запрещённые приёмы (style= / i / em / u / script / тени / "
                "градиенты / скругления). Перепиши без них, верни ТОЛЬКО "
                "```html-блок.")
    return None


def _extract_html(reply: str) -> str:
    match = _HTML_FENCE.search(reply)
    if match:
        return match.group(1).strip()
    section = _SECTION.search(reply)
    return (section.group(0) if section else reply).strip()
