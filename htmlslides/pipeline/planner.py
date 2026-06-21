"""Роль 1 — Deck Planner: InputDoc + каталог шаблонов -> DeckPlan.

Текстовый вход (основной путь) планируется ПО РАЗДЕЛАМ (map) параллельно, затем
структура деки собирается в коде (reduce: cover/contacts/разнообразие/accent).
Причина — монолитный вызов на весь документ систематически падал на крупных доках:
Kimi-K2.6 уходит в reasoning-runaway и возвращает пустой content (no JSON), а у
планировщика нет мягкой деградации, поэтому падал ВЕСЬ билд. Per-section вызовы
малы (компактное меню + один раздел), no-think, с ретраями и код-фолбэком на раздел
— пустой ответ роняет ОДИН слайд, а не деку (тот же паттерн отказоустойчивости, что
у filler).

Для pptx-входа (rebrand) сохранён монолитный vision-вызов: ему нужны PNG всех
исходных слайдов разом, разбить по разделам нельзя.
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Sequence

import structlog
from openai import (APIConnectionError, APITimeoutError, InternalServerError,
                    RateLimitError)
from pydantic import BaseModel, Field

from ..brand import brand_rules
from ..library import SlotSpec, TemplateLibrary
from ..models import DeckPlan, SlidePlan
from ..parsers.base import (Block, CodeBlock, ImageBlock, InputDoc, ListBlock,
                            Section, TableBlock, TextBlock)
from .client import KimiClient, LLMFormatError, image_part

logger = structlog.get_logger(__name__)

# Reasoning OFF for per-section planning. Малый вход + малый выход + no-think — самый
# надёжный профиль вызова (см. project_planner_scaling: whole-doc падал 5/5, а
# per-section с ретраями+фолбэком держит деку). Kimi чтит тумблер лишь частично,
# поэтому это НЕ гарантия — гарантию даёт изоляция+ретраи+фолбэк ниже.
_PLANNER_NO_THINK = {"thinking": {"type": "disabled"}}

# Потолок токенов на план одного раздела. Выход крошечный (~0.5KB), потолок служит
# только предохранителем от reasoning-runaway: чем он ниже, тем быстрее пустой ответ
# отвалится в фолбэк (а не будет минуту молотить скрытое рассуждение).
_SECTION_MAX_TOKENS = 1280

# Параллелизм map-шага. RPS держит гейт клиента (env HTMLSLIDES_RPS, дефолт 10),
# так что 8 воркеров безопасны и сокращают wall-clock на многосекционных доках.
_MAP_WORKERS = 8

# Транзиентные сбои API на ОДИН раздел (после собственных ретраев openai-клиента):
# лимит/таймаут/обрыв/5xx. Не должны ронять деку — деградируем раздел на эвристику.
_TRANSIENT_API_ERRORS = (
    RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)

# Семейство текстовых перечислений — взаимозаменяемы для правила разнообразия.
# Чарты/таймлайны/спец-шаблоны НЕ свапаем (у них профильный смысл).
_VARIETY_FAMILY = ("cards-6", "grid-2x2", "three-col", "two-col-cards")
_VARIETY_SWAP = {
    "cards-6": ("grid-2x2", "three-col", "two-col-cards"),
    "grid-2x2": ("cards-6", "three-col", "two-col-cards"),
    "three-col": ("cards-6", "grid-2x2", "two-col-cards"),
    "two-col-cards": ("three-col", "cards-6", "grid-2x2"),
}

# ============================ ПРОМПТЫ ============================

# Монолитный планировщик — ТОЛЬКО для vision-rebrand (pptx со скриншотами).
PLANNER_SYSTEM = """\
Ты — планировщик слайдов презентации в бренде Cloud.ru 2.0.
Дано: исходный документ и каталог шаблонов слайдов с контрактами слотов.
Разбей содержание на слайды и верни ТОЛЬКО JSON без пояснений, вида:
{{"title":"Название деки","slides":[
  {{"index":1,"type":"title","template_id":"cover","freeform":false,
   "content":{{"brief":"конкретные факты и формулировки исходника для этого слайда"}}}}
]}}

Правила:
- Первый слайд — cover, последний — contacts.
- template_id строго из каталога; type бери из каталога.
- ПОЛНОТА: КАЖДЫЙ смысловой раздел исходника — ОТДЕЛЬНЫЙ слайд; не объединяй и не
  выбрасывай разделы. Ориентир 6–18 слайдов.
- В content.brief переноси конкретику исходника (цифры, названия) БЕЗ ПОТЕРИ СМЫСЛА;
  сохраняй единицы и суффиксы (152-ФЗ, %, млрд, Tier III).
- Выбор шаблона по числу пунктов: 2→two-col-cards; 3→three-col/cards-6; 4→grid-2x2;
  5-6→cards-6. Числовые данные → bar/line/donut/stats-row/timeline/kpi-rings.
- РАЗНООБРАЗИЕ: один шаблон не чаще 2 раз и не подряд; statement максимум один.
{freeform_rule}

БРЕНД-ПРАВИЛА:
{rules}"""

# Per-section планировщик (основной путь). Компактное МЕНЮ вместо полного каталога.
SECTION_SYSTEM = """\
Ты планируешь ОДИН раздел презентации Cloud.ru 2.0 в 1 (редко 2) слайда.
Дано: меню шаблонов (id — назначение) и текст раздела.
Верни ТОЛЬКО JSON вида:
{{"slides":[{{"template_id":"<id из меню>","brief":"конкретные факты раздела"}}]}}

Правила:
- template_id СТРОГО из меню; обычно 1 слайд на раздел (2 — только если контента
  реально много и он распадается на две темы).
- ДАННЫЕ ВАЖНЕЕ ТЕКСТА: если в разделе есть числа, проценты, метрики, сравнения или
  таблица — ВЫБИРАЙ ПРОФИЛЬНЫЙ data-шаблон, НЕ сворачивай в текстовый список:
  · таблица (строки×столбцы) или «сервис + параметры» → service-table;
  · 2 ключевые цифры-героя (выручка, рост) → kpi;
  · 3-4 разрозненные метрики в ряд → stats-row;
  · сравнение 3-6 величин по одному числу → bar-chart;
  · доли целого (структура, %) → donut-chart;
  · одна метрика во времени → line-chart;
  · состав по категориям (части в каждой) → stacked-bar;
  · проценты готовности/покрытия → kpi-rings;
  · этапы/шаги во времени → timeline;
  · «было → стало» → before-after.
- Иначе по числу именованных пунктов: 2→two-col-cards; 3→three-col; 4→grid-2x2;
  5-6→cards-6. Короткий тезис из 1-2 предложений без списка/чисел → statement.
- В brief — конкретика раздела (цифры, названия, тезисы), сохраняй единицы и
  суффиксы (152-ФЗ, %, млрд, Tier III). Ничего не выдумывай.
- НЕ выбирай cover/contacts/back-cover/blank — их ставит система.
{freeform_rule}

БРЕНД-ПРАВИЛА:
{rules}"""

_FREEFORM_ALLOWED = (
    '- FREEFORM: для НЕТИПОВОЙ структуры (матрицы ответственности, концептуальные '
    'схемы, карты экосистемы), которую шаблоны искажают — ставь "freeform": true и '
    '"template_id": null.')
_FREEFORM_FORBIDDEN = ('- freeform запрещён: каждый слайд обязан использовать '
                       'шаблон из меню/каталога.')

_REBRAND_NOTE = ("Ниже — скриншоты слайдов исходной презентации (режим rebrand). "
                 "Перенеси её структуру, порядок и содержание слайдов в бренд-шаблоны.")


# ============================ ОБЩЕЕ ============================

def slot_brief(spec: SlotSpec) -> dict:
    """Компактное описание слота для промпта (нулевые лимиты опускаем)."""
    brief: dict = {"kind": spec.kind}
    if spec.required:
        brief["required"] = True
    if spec.max_chars:
        brief["max_chars"] = spec.max_chars
    if spec.max_items:
        brief["max_items"] = spec.max_items
    if spec.item_max_chars:
        brief["item_max_chars"] = spec.item_max_chars
    if spec.item_slots:
        brief["item_slots"] = {n: slot_brief(s) for n, s in spec.item_slots.items()}
    return brief


def library_brief(library: TemplateLibrary) -> str:
    """Полный JSON каталога со слот-контрактами (для монолитного vision-вызова)."""
    items = [{"id": t.id, "type": t.type, "intent": t.intent,
              "slots": {n: slot_brief(s) for n, s in t.slots.items()}}
             for t in library.templates]
    return json.dumps(items, ensure_ascii=False)


def library_menu(library: TemplateLibrary) -> str:
    """Компактное меню «id (type): intent» — только для ВЫБОРА шаблона в map-шаге.

    Полный слот-контракт планировщику не нужен (его получит filler по шаблону);
    короткий вход резко снижает шанс reasoning-runaway → пустого ответа Kimi."""
    return "\n".join(f"- {t.id} ({t.type}): {t.intent}" for t in library.templates)


def doc_to_text(doc: InputDoc) -> str:
    """InputDoc -> markdown-подобный текст для промпта (бинарь картинок не тащим)."""
    lines: list[str] = []
    if doc.title:
        lines.append(f"# {doc.title}")
    for section in doc.sections:
        if section.heading:
            lines.append(f"{'#' * max(section.level, 1)} {section.heading}")
        for block in section.blocks:
            rendered = _block_to_text(block)
            if rendered:
                lines.append(rendered)
    return "\n\n".join(lines)


def _section_to_text(section: Section) -> str:
    lines: list[str] = []
    if section.heading:
        lines.append(section.heading)
    for block in section.blocks:
        rendered = _block_to_text(block)
        if rendered:
            lines.append(rendered)
    return "\n\n".join(lines)


def _block_to_text(block: Block) -> str:
    if isinstance(block, TextBlock):
        return block.text
    if isinstance(block, ListBlock):
        if block.ordered:
            return "\n".join(f"{i}. {item}" for i, item in enumerate(block.items, 1))
        return "\n".join(f"- {item}" for item in block.items)
    if isinstance(block, TableBlock):
        return "\n".join("| " + " | ".join(row) + " |" for row in block.rows)
    if isinstance(block, ImageBlock):
        return f"[картинка: {block.alt or block.src or 'без подписи'}]"
    if isinstance(block, CodeBlock):
        return f"```{block.lang}\n{block.text}\n```"
    return ""


# ===================== MAP: план по разделу =====================

class _SectionSlide(BaseModel):
    template_id: Optional[str] = None
    freeform: bool = False
    brief: str = ""


class _SectionPlan(BaseModel):
    slides: list[_SectionSlide] = Field(min_length=1)


def _has_content(section: Section) -> bool:
    return bool(section.heading or section.blocks)


def _list_item_count(section: Section) -> int:
    return sum(len(b.items) for b in section.blocks if isinstance(b, ListBlock))


# Число с единицей/суффиксом — сигнал «здесь данные, а не проза» (152-ФЗ, 99.9%,
# 18 млрд, Tier III, ×3, 500 ГБ). Голые годы/нумерация списка сюда не считаем.
_NUMERIC_TOKEN = re.compile(
    r"\d[\d.,]*\s*(?:%|млрд|млн|тыс|₽|руб|x|×|раз|ГБ|ТБ|TB|GB|Tier|ФЗ|сек|мс|ч|сут)",
    re.IGNORECASE)
_PERCENT = re.compile(r"\d[\d.,]*\s*%")


def _fallback_template(section: Section, library: TemplateLibrary) -> str:
    """Эвристика выбора шаблона, когда LLM не дал валидный план раздела.

    ВАЖНО: учитывает ДАННЫЕ раздела (таблицы/числа/проценты) — иначе чарты, диаграммы
    и таблицы пропадали бы при каждом фолбэке (а на деградированном Cloud.ru фолбэк —
    частый путь). Деградация одного раздела вместо падения всей деки."""
    known = {t.id for t in library.templates}
    text = _section_to_text(section)
    has_table = any(isinstance(b, TableBlock) for b in section.blocks)
    numeric = len(_NUMERIC_TOKEN.findall(text))
    percents = len(_PERCENT.findall(text))
    items = _list_item_count(section)

    # Порядок проб: профильный data-шаблон → текстовый по числу пунктов → запасные.
    candidates: list[str] = []
    if has_table:
        candidates.append("service-table")
    if percents >= 2:                      # доли/проценты — кольцевая диаграмма
        candidates.append("donut-chart")
    if numeric >= 3:                       # набор метрик — крупные числа в ряд
        candidates.append("stats-row")
    elif numeric == 2:                     # пара величин — сравнение барами
        candidates.append("bar-chart")
    by_items = {2: "two-col-cards", 3: "three-col", 4: "grid-2x2",
                5: "cards-6", 6: "cards-6"}
    candidates.append(by_items.get(min(items, 6)) if items >= 2 else "statement")
    candidates += ["statement", "blank"]
    for choice in candidates:
        if choice in known:
            return choice
    return next(iter(known))


def _plan_section(client: KimiClient, library: TemplateLibrary, menu: str,
                  section: Section, *, freeform_ok: bool) -> list[SlidePlan]:
    """Map-шаг: один раздел -> 1-2 SlidePlan. Любой сбой -> эвристический фолбэк."""
    known = {t.id for t in library.templates}
    rule = _FREEFORM_ALLOWED if freeform_ok else _FREEFORM_FORBIDDEN
    system = SECTION_SYSTEM.format(menu=menu, freeform_rule=rule, rules=brand_rules())
    heading = section.heading or "Раздел"
    user = f"Раздел «{heading}».\n\nТекст раздела:\n{_section_to_text(section)}"
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]
    try:
        # max_tokens мал нарочно: валидный план раздела — ~0.5KB, потолок не нужен.
        # Kimi частично игнорит no-think и при runaway жжёт ВЕСЬ бюджет до пустого
        # ответа — низкий потолок делает такой провал в разы быстрее (к фолбэку).
        # retries=1: вторую медленную попытку не ждём — деградируем эвристикой.
        sp = client.chat_json(messages, _SectionPlan, max_tokens=_SECTION_MAX_TOKENS,
                              retries=1, extra_body=_PLANNER_NO_THINK)
    except (LLMFormatError, *_TRANSIENT_API_ERRORS) as exc:
        logger.warning("planner.section_fallback", heading=heading[:40],
                       error=str(exc)[:80])
        return [_fallback_slide(section, library)]

    out: list[SlidePlan] = []
    for ss in sp.slides[:2]:                       # не больше 2 слайдов на раздел
        brief = ss.brief.strip() or _section_to_text(section)
        if ss.freeform and freeform_ok:
            out.append(SlidePlan(index=1, type="content", template_id=None,
                                 freeform=True, content={"brief": brief}))
            continue
        tid = ss.template_id if ss.template_id in known else None
        if tid is None or tid in ("cover", "contacts", "back-cover", "blank"):
            tid = _fallback_template(section, library)
        out.append(SlidePlan(index=1, type=library.get(tid).type, template_id=tid,
                             freeform=False, content={"brief": brief}))
    return out or [_fallback_slide(section, library)]


def _fallback_slide(section: Section, library: TemplateLibrary) -> SlidePlan:
    tid = _fallback_template(section, library)
    return SlidePlan(index=1, type=library.get(tid).type, template_id=tid,
                     freeform=False, content={"brief": _section_to_text(section)})


# ===================== REDUCE: сборка деки в коде =====================

def _cover_slide(doc: InputDoc, library: TemplateLibrary) -> SlidePlan:
    spec = library.get("cover")
    brief = doc.title or "Cloud.ru"
    return SlidePlan(index=1, type=spec.type, template_id="cover",
                     content={"brief": brief})


def _contacts_slide(library: TemplateLibrary) -> SlidePlan:
    spec = library.get("contacts")
    return SlidePlan(index=1, type=spec.type, template_id="contacts",
                     content={"brief": "Контакты Cloud.ru: cloud.ru"})


def _enforce_variety(slides: list[SlidePlan], library: TemplateLibrary) -> None:
    """Правило бренда: текстовый шаблон не чаще 2 раз и не подряд. Свап на
    совместимый из семейства (чарты/спец-шаблоны не трогаем). In-place."""
    known = {t.id for t in library.templates}
    counts: dict[str, int] = {}
    prev: Optional[str] = None
    for slide in slides:
        tid = slide.template_id
        if not tid or tid not in _VARIETY_FAMILY:
            prev = tid
            continue
        conflict = counts.get(tid, 0) >= 2 or tid == prev
        if conflict:
            for alt in _VARIETY_SWAP.get(tid, ()):
                if alt in known and counts.get(alt, 0) < 2 and alt != prev:
                    slide.template_id = alt
                    slide.type = library.get(alt).type
                    tid = alt
                    break
        counts[tid] = counts.get(tid, 0) + 1
        prev = tid


def _pick_accent(slides: list[SlidePlan]) -> None:
    """≤1 зелёный accent-слайд на деку: первый statement (бренд-приём). In-place."""
    for slide in slides:
        if slide.template_id == "statement" and isinstance(slide.content, dict):
            slide.content = {**slide.content, "accent": "1"}
            return


def _plan_deck_text(client: KimiClient, doc: InputDoc, library: TemplateLibrary, *,
                    freeform_ok: bool, workers: int = _MAP_WORKERS) -> DeckPlan:
    menu = library_menu(library)
    sections = [s for s in doc.sections if _has_content(s)]

    # MAP: разделы параллельно (RPS держит гейт клиента; фолбэк внутри _plan_section).
    if sections:
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = [pool.submit(_plan_section, client, library, menu, s,
                                   freeform_ok=freeform_ok) for s in sections]
            per_section = [f.result() for f in futures]
        finally:
            pool.shutdown()
    else:
        per_section = []

    # REDUCE: cover + слайды разделов + contacts; разнообразие и accent — в коде.
    slides: list[SlidePlan] = [_cover_slide(doc, library)]
    for group in per_section:
        slides.extend(group)
    slides.append(_contacts_slide(library))
    _enforce_variety(slides, library)
    _pick_accent(slides)
    for i, slide in enumerate(slides, start=1):
        slide.index = i
    return DeckPlan(title=doc.title or "Презентация", slides=slides)


# ===================== VISION (rebrand): монолит =====================

def _plan_deck_vision(client: KimiClient, doc: InputDoc, library: TemplateLibrary,
                      slide_images: Sequence[Path], *, freeform_ok: bool,
                      max_tokens: int) -> DeckPlan:
    rule = _FREEFORM_ALLOWED if freeform_ok else _FREEFORM_FORBIDDEN
    system = PLANNER_SYSTEM.format(freeform_rule=rule, rules=brand_rules())
    text = (f"Каталог шаблонов:\n{library_brief(library)}\n\n"
            f"Исходный документ:\n{doc_to_text(doc)}")
    content: list[dict] = [{"type": "text", "text": text + "\n\n" + _REBRAND_NOTE}]
    content.extend(image_part(p) for p in slide_images)
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": content}]
    try:
        return client.chat_json(messages, DeckPlan, max_tokens=max_tokens)
    except LLMFormatError as exc:
        logger.warning("planner.retry_no_think", error=str(exc))
        return client.chat_json(messages, DeckPlan, max_tokens=max_tokens,
                                extra_body=_PLANNER_NO_THINK)


# ===================== ВХОД =====================

def plan_deck(client: KimiClient, doc: InputDoc, library: TemplateLibrary, *,
              slide_images: Sequence[Path] = (), freeform_ok: bool = False,
              max_tokens: int = 16384) -> DeckPlan:
    """InputDoc + каталог -> DeckPlan.

    Текстовый вход — map(по разделам)+reduce(в коде); pptx-rebrand со скриншотами —
    монолитный vision-вызов. Бросает SlotValidationError на неизвестный template_id.
    """
    if slide_images:
        plan = _plan_deck_vision(client, doc, library, slide_images,
                                 freeform_ok=freeform_ok, max_tokens=max_tokens)
    else:
        plan = _plan_deck_text(client, doc, library, freeform_ok=freeform_ok)
    return _finalize(plan, library, freeform_ok=freeform_ok)


def _finalize(plan: DeckPlan, library: TemplateLibrary, *,
              freeform_ok: bool) -> DeckPlan:
    """Общие страховки для обоих путей: blank-misuse, freeform-контроль, валидация
    template_id, дедуп зелёного accent."""
    seen_accent = False
    for slide in plan.slides:
        if not slide.freeform and slide.template_id == "blank":
            spec = library.get("statement")
            slide.template_id = "statement"
            slide.type = spec.type
        if slide.freeform and not freeform_ok:
            raise ValueError(f"slide {slide.index}: freeform запрещён (freeform_ok=False)")
        if not slide.freeform:
            library.get(slide.template_id)      # неизвестный id -> SlotValidationError
        if isinstance(slide.content, dict) and slide.content.get("accent"):
            if seen_accent:
                slide.content = {k: v for k, v in slide.content.items() if k != "accent"}
            else:
                seen_accent = True
    return plan
