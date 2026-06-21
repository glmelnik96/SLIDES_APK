"""Роль 1 — Deck Planner: InputDoc + каталог шаблонов -> DeckPlan (1 вызов Kimi).

Для pptx-входа (rebrand) дополнительно получает PNG исходных слайдов (vision)
и переносит их структуру в бренд-шаблоны.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

import structlog

from ..brand import brand_rules
from ..library import SlotSpec, TemplateLibrary
from ..models import DeckPlan
from ..parsers.base import (Block, CodeBlock, ImageBlock, InputDoc, ListBlock,
                            TableBlock, TextBlock)
from .client import KimiClient, LLMFormatError, image_part

logger = structlog.get_logger(__name__)

# Reasoning-off fallback for the planner. Kimi-K2.6 reasoning occasionally runs
# away and returns no/empty JSON (finish=length) → LLMFormatError, which aborts
# the WHOLE build (unlike the filler, a planner failure has no graceful blank
# fallback). On that failure we retry once with reasoning disabled: it can't burn
# the budget on hidden reasoning, so it emits JSON; a slightly simpler plan beats
# a total build failure. Kept as a FALLBACK (not the default) because Kimi only
# partially honours the toggle for text — it doesn't speed the planner up, and the
# reasoning-on plan has better structural quality. (Vision rebrand ignores the
# toggle and always reasons — harmless there.)
_PLANNER_NO_THINK = {"thinking": {"type": "disabled"}}

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
- ПОЛНОТА: КАЖДЫЙ смысловой раздел исходника (заголовок ## и его содержимое) —
  это ОТДЕЛЬНЫЙ слайд. НЕ объединяй два разных раздела в один слайд и НЕ
  выбрасывай разделы. Сколько содержательных разделов — столько контентных
  слайдов (плюс cover/contacts/разделители). Ориентир 6–18 слайдов; для богатого
  документа делай больше слайдов, а не плотнее контент.
- Один смысл — один слайд; цифры и факты не выдумывай.
- В content.brief переноси конкретику исходника (цифры, названия, тезисы) БЕЗ
  ПОТЕРИ СМЫСЛА. Сокращай формулировки, но не обрезай значащие части: «152-ФЗ
  и 187-ФЗ» оставь как есть (НЕ «152, 187»); единицы, суффиксы и названия (ФЗ, %,
  млрд, Tier III) сохраняй — голое число без них теряет смысл.
- Явный маркер раздела в исходнике («Раздел N», «Часть N», «Глава N», «Этап N») —
  это слайд-разделитель: используй section-dots или section-frame, в brief дай
  короткое название раздела (поясняющий текст вынеси на следующий слайд).
- blank — это АВАРИЙНАЯ заглушка, а НЕ контентный слайд: НЕ выбирай blank ни для
  одного смыслового раздела. Короткий вводный/итоговый раздел из 1–2 предложений-
  тезисов (без списка, чисел, сравнения) — это statement: ключевую мысль в brief,
  она станет заголовком-тезисом, пояснение — подзаголовком. Любой раздел обязан
  попасть на содержательный шаблон.
- ВЫБОР ШАБЛОНА ПО КОЛИЧЕСТВУ именованных пунктов (название + описание):
  ровно 2 → two-col-cards; ровно 3 → three-col ИЛИ cards-6; 4 → grid-2x2 или
  cards-6; 5–6 → cards-6. ШЕСТЬ пунктов — это cards-6 (сетка 3×2), НЕ three-col:
  three-col рассчитан только на ТРИ колонки, шесть пунктов в нём слипаются.
  Тарифы / планы / пакеты с ОПИСАНИЕМ фич у каждого — тем же правилом
  (2→two-col-cards, 3–6→cards-6). НО если варианты сравниваются по ОДНОМУ
  числу (цена, стоимость, объём) без описания фич — это bar-chart (визуальное
  сравнение величин нагляднее плоского текста: «18 / 54 / 120» одинаково велики
  в карточках, но разительно разные столбиками).
- Числовой ряд во времени → line-chart (тренд); сравнение величин → bar-chart;
  доли целого → donut-chart; набор разрозненных метрик → stats-row;
  состав по группам (несколько категорий, в каждой доли) → stacked-bar;
  набор процентов выполнения/готовности/покрытия → kpi-rings;
  последовательность этапов/шагов во времени → timeline;
  «было/стало» → before-after; сетка 2×2 равнозначных тезисов → grid-2x2.
  Если в исходнике есть такие данные — ОБЯЗАТЕЛЬНО используй профильный шаблон,
  не сворачивай в текстовый список.
- РАЗНООБРАЗИЕ ВЁРСТКИ (требование бренда): не ставь один и тот же шаблон чаще
  2 раз на деку и не подряд. Если контент позволяет — выбирай разные макеты
  соседних слайдов. Текстовые перечисления чередуй: cards-6, grid-2x2, three-col,
  two-col-cards — а не только cards-6. statement — НЕ БОЛЕЕ ОДНОГО на всю деку
  (это акцентный слайд-тезис; два statement подряд выглядят монотонно).
- ЗЕЛЁНЫЙ АКЦЕНТ-СЛАЙД: для ОДНОГО самого сильного statement за деку (ключевой
  тезис, манифест или призыв) добавь в его content поле "accent":"1" — он отрисуется
  брендовым зелёным слайдом (графит на зелёном). Не чаще одного раза на деку и
  только если в деке есть statement.
{freeform_rule}

БРЕНД-ПРАВИЛА:
{rules}"""

_FREEFORM_ALLOWED = (
    '- FREEFORM (произвольная брендовая раскладка): для слайдов с НЕТИПОВОЙ '
    'структурой, которую шаблоны передают плохо — матрицы/таблицы ответственности, '
    'концептуальные схемы и диаграммы потоков, карты экосистемы, нестандартные '
    'композиции — ставь "freeform": true, "template_id": null (систему вёрстки '
    'выберет генератор). Не натягивай такой контент на ближайший шаблон, если он '
    'искажает смысл. Для типовых слайдов (тезис, перечисление, график, сравнение) '
    'используй шаблоны.')
_FREEFORM_FORBIDDEN = ('- freeform запрещён: каждый слайд обязан использовать '
                       'шаблон из каталога.')

_REBRAND_NOTE = ("Ниже — скриншоты слайдов исходной презентации (режим rebrand). "
                 "Перенеси её структуру, порядок и содержание слайдов в бренд-шаблоны.")


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
    """Компактный JSON каталога шаблонов для промпта планировщика."""
    items = [{"id": t.id, "type": t.type, "intent": t.intent,
              "slots": {n: slot_brief(s) for n, s in t.slots.items()}}
             for t in library.templates]
    return json.dumps(items, ensure_ascii=False)


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


def plan_deck(client: KimiClient, doc: InputDoc, library: TemplateLibrary, *,
              slide_images: Sequence[Path] = (), freeform_ok: bool = False,
              max_tokens: int = 16384) -> DeckPlan:
    """Один вызов планировщика. Бросает SlotValidationError на неизвестный template_id.

    max_tokens щедрый: Kimi-K2.6 — reasoning-модель, на большом документе тратит
    тысячи токенов на скрытое рассуждение ДО выдачи JSON. При 8192 ответ обрывался
    на стадии reasoning (finish=length, пустой content) → LLMFormatError. Потолок
    не цель: биллинг по факту, короткие планы укладываются и дешевле."""
    rule = _FREEFORM_ALLOWED if freeform_ok else _FREEFORM_FORBIDDEN
    system = PLANNER_SYSTEM.format(freeform_rule=rule, rules=brand_rules())
    text = (f"Каталог шаблонов:\n{library_brief(library)}\n\n"
            f"Исходный документ:\n{doc_to_text(doc)}")
    if slide_images:
        content: list[dict] = [{"type": "text", "text": text + "\n\n" + _REBRAND_NOTE}]
        content.extend(image_part(p) for p in slide_images)
        user: dict = {"role": "user", "content": content}
    else:
        user = {"role": "user", "content": text}
    messages = [{"role": "system", "content": system}, user]
    try:
        # reasoning ON — best structural quality
        plan = client.chat_json(messages, DeckPlan, max_tokens=max_tokens)
    except LLMFormatError as exc:
        # Reasoning ran away → no/empty JSON after retries. Retry once with
        # reasoning disabled so the build doesn't hard-fail. An empty plan also
        # lands here: DeckPlan requires ≥1 slide, so {"slides":[]} fails
        # validation and chat_json re-raises it as LLMFormatError.
        logger.warning("planner.retry_no_think", error=str(exc))
        plan = client.chat_json(messages, DeckPlan, max_tokens=max_tokens,
                                extra_body=_PLANNER_NO_THINK)
    seen_accent = False
    for slide in plan.slides:
        # Страховка blank-misuse: blank — только аварийная заглушка fill-стадии.
        # Если планировщик выбрал blank для контентного слайда, переводим на
        # statement (оба type=content, бриф сохраняется → филлер заполнит из него).
        if not slide.freeform and slide.template_id == "blank":
            spec = library.get("statement")
            slide.template_id = "statement"
            slide.type = spec.type
        if slide.freeform and not freeform_ok:
            raise ValueError(f"slide {slide.index}: freeform запрещён (freeform_ok=False)")
        if not slide.freeform:
            library.get(slide.template_id)      # неизвестный id -> SlotValidationError
        # Страховка: зелёный accent-слайд максимум один на деку (бренд) — снимаем
        # accent со второго и далее, даже если планировщик поставил его несколько раз.
        if isinstance(slide.content, dict) and slide.content.get("accent"):
            if seen_accent:
                slide.content = {k: v for k, v in slide.content.items() if k != "accent"}
            else:
                seen_accent = True
    return plan
