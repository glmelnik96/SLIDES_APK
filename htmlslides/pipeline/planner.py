"""Роль 1 — Deck Planner: InputDoc + каталог шаблонов -> DeckPlan.

Текстовый вход (основной путь) планируется ПО РАЗДЕЛАМ (map) параллельно, затем
структура деки собирается в коде (reduce: cover/разнообразие/accent).
Причина — монолитный вызов на весь документ систематически падал на крупных доках:
reasoning-heavy модели уходили в runaway и возвращали пустой content (no JSON), а у
планировщика не было мягкой деградации, поэтому падал ВЕСЬ билд. Per-section вызовы
малы (компактное меню + один раздел), no-think, с ретраями и код-фолбэком на раздел
— пустой ответ роняет ОДИН слайд, а не деку (тот же паттерн отказоустойчивости, что
у filler).

Pptx-вход (rebrand) идёт тем же map+reduce: скриншоты слайдов 1:1 с разделами,
поэтому каждый map-вызов получает СВОЙ скриншот. Монолитный vision-вызов остался
только фолбэком на случай рассинхрона «скриншоты ≠ разделы» (рендер отдал не всё).
Причина ухода от монолита — точность: 16K токенов на всю деку давали ~абзац
пересказа на слайд, замер 2026-07-29 показал удержание 31–66% фактов исходника.
"""
from __future__ import annotations

import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional, Sequence

import structlog
from pydantic import BaseModel, Field

from ..brand import brand_rules
from ..library import SlotSpec, TemplateLibrary
from ..models import DeckPlan, SlidePlan
from ..parsers.base import (Block, CodeBlock, ImageBlock, InputDoc, ListBlock,
                            Section, TableBlock, TextBlock)
from .client import (TRANSIENT_API_ERRORS, KimiClient, LLMFormatError,
                     ProviderUnavailable, degradation_is_total, image_part)

logger = structlog.get_logger(__name__)

# Reasoning OFF for per-section planning. Малый вход + малый выход + no-think — самый
# надёжный профиль вызова (см. project_planner_scaling: whole-doc падал 5/5, а
# per-section с ретраями+фолбэком держит деку). Не все модели чтят тумблер (MiniMax
# принимает молча, но reasoning и так короткий) — гарантию даёт изоляция+ретраи+фолбэк.
_PLANNER_NO_THINK = {"thinking": {"type": "disabled"}}

# Потолок токенов на план одного раздела. Выход крошечный (~0.5KB), потолок служит
# только предохранителем от reasoning-runaway: чем он ниже, тем быстрее пустой ответ
# отвалится в фолбэк (а не будет минуту молотить скрытое рассуждение).
_SECTION_MAX_TOKENS = 1280

# Для map-вызова СО скриншотом потолок выше: brief обязан вместить всё содержимое
# слайда (в т.ч. текст, которого нет в XML — вектор/SmartArt), обрезанный JSON
# отвалился бы в фолбэк и потерял визуальную часть.
_SECTION_VISION_MAX_TOKENS = 2560

# Параллелизм map-шага. RPS держит гейт клиента (env HTMLSLIDES_RPS, дефолт 10),
# так что 8 воркеров безопасны и сокращают wall-clock на многосекционных доках.
_MAP_WORKERS = 8

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
- Первый слайд — cover.
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
- template_id СТРОГО из меню; 1 слайд, если ВЕСЬ контент раздела помещается.
  Если в разделе И ключевые цифры, И список из 4+ пунктов (или две разные темы) —
  верни 2 слайда (data-шаблон + текстовый): выбрасывать пункты нельзя.
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

_SECTION_REBRAND_NOTE = (
    "Приложен скриншот этого слайда исходной презентации (режим rebrand). Перенеси "
    "в brief ВСЁ его содержимое — заголовок, пункты, цифры, подписи — без потери. "
    "Текст раздела выше может быть неполным (вектор/схемы в XML не видны) — "
    "скриншот главнее.")


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
    короткий вход резко снижает шанс reasoning-runaway → пустого ответа."""
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
    if section.notes:
        # Заметки докладчика — контекст для дизайна (не видимый текст слайда).
        lines.append(f"Заметки докладчика: {section.notes}")
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


def _block_has_text(block: Block) -> bool:
    """Есть ли в блоке осмысленный контент (не пустой абзац/список)."""
    if isinstance(block, TextBlock):
        return bool(block.text.strip())
    if isinstance(block, ListBlock):
        return any(i.strip() for i in block.items)
    if isinstance(block, TableBlock):
        return any(c.strip() for row in block.rows for c in row)
    return isinstance(block, (ImageBlock, CodeBlock))  # картинка/код — это контент


def _is_part_title(section: Section) -> bool:
    """Раздел-«шапка части»: есть заголовок, но нет собственного контента.

    Это классический разделитель («Раздел 1», «Часть N», «Блок услуг») — раньше
    он молча превращался в титул/statement. Теперь распознаём его как дивайдер."""
    return bool(section.heading) and not any(
        _block_has_text(b) for b in section.blocks)


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


def _merge_brief(model_brief: str, section: Section) -> str:
    """Brief модели + ВСЕГДА дословный текст раздела.

    Раньше исходник попадал в brief только при ПУСТОМ ответе модели, и filler видел
    лишь сжатый пересказ — замер прод-кейсов 2026-07-29 показал удержание 31–66%
    фактов. Теперь пересказ модели задаёт подачу (что вынести, каким шаблоном),
    а конкретику filler берёт из дословного исходника."""
    model_brief = model_brief.strip()
    src = _section_to_text(section)
    if not model_brief:
        return src
    return (f"{model_brief}\n\n"
            f"Исходный текст раздела (факты переносить дословно):\n{src}")


def _plan_section(client: KimiClient, library: TemplateLibrary, menu: str,
                  section: Section, *, freeform_ok: bool,
                  image: Path | None = None) -> tuple[list[SlidePlan], bool]:
    """Map-шаг: один раздел -> 1-2 SlidePlan. Любой сбой -> эвристический фолбэк.

    Возвращает ещё и флаг «фолбэк случился ИЗ-ЗА сбоя API»: осечка формата стоит
    одного раздела и лечится эвристикой, а молчащий провайдер положит и следующую
    фазу — про него зовущий обязан узнать (см. `degradation_is_total`).

    image — скриншот исходного слайда (pptx-rebrand): прикладывается к вызову,
    чтобы brief вобрал и то, чего нет в XML-тексте (вектор, SmartArt, схемы)."""
    known = {t.id for t in library.templates}
    rule = _FREEFORM_ALLOWED if freeform_ok else _FREEFORM_FORBIDDEN
    system = SECTION_SYSTEM.format(menu=menu, freeform_rule=rule, rules=brand_rules())
    heading = section.heading or "Раздел"
    user = f"Раздел «{heading}».\n\nТекст раздела:\n{_section_to_text(section)}"
    if image is not None:
        content: str | list = [
            {"type": "text", "text": f"{user}\n\n{_SECTION_REBRAND_NOTE}"},
            image_part(image)]
        max_tokens = _SECTION_VISION_MAX_TOKENS
    else:
        content = user
        max_tokens = _SECTION_MAX_TOKENS
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": content}]
    try:
        # max_tokens мал нарочно: валидный план раздела — ~0.5KB, потолок не нужен.
        # При reasoning-runaway модель жжёт ВЕСЬ бюджет до пустого ответа — низкий
        # потолок делает такой провал в разы быстрее (к фолбэку).
        # retries=1: вторую медленную попытку не ждём — деградируем эвристикой.
        sp = client.chat_json(messages, _SectionPlan, max_tokens=max_tokens,
                              retries=1, extra_body=_PLANNER_NO_THINK)
    except (LLMFormatError, *TRANSIENT_API_ERRORS) as exc:
        logger.warning("planner.section_fallback", heading=heading[:40],
                       error=str(exc)[:80])
        return [_fallback_slide(section, library)], isinstance(exc, TRANSIENT_API_ERRORS)

    out: list[SlidePlan] = []
    for ss in sp.slides[:2]:                       # не больше 2 слайдов на раздел
        brief = _merge_brief(ss.brief, section)
        if ss.freeform and freeform_ok:
            out.append(SlidePlan(index=1, type="content", template_id=None,
                                 freeform=True, content={"brief": brief}))
            continue
        tid = ss.template_id if ss.template_id in known else None
        if tid is None or tid in ("cover", "contacts", "back-cover", "blank"):
            tid = _fallback_template(section, library)
        out.append(SlidePlan(index=1, type=library.get(tid).type, template_id=tid,
                             freeform=False, content={"brief": brief}))
    return (out or [_fallback_slide(section, library)]), False


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


# Два макета-разделителя чередуем для разнообразия (точки → рамка → точки …).
_SECTION_TEMPLATES = ("section-dots", "section-frame")


def _divider_label(heading: str, max_chars: int) -> str:
    """Заголовок раздела -> короткая метка дивайдера КАПСОМ в пределах лимита
    (режем по границе слова, чтобы не было «БЕЗОПАСН…»)."""
    label = " ".join((heading or "Раздел").split()).strip().upper()
    if max_chars and len(label) > max_chars:
        cut = label[:max_chars]
        if " " in cut:
            cut = cut[:cut.rfind(" ")]
        label = cut or label[:max_chars]
    return label


def _divider_slide(section: Section, library: TemplateLibrary,
                   number: int) -> SlidePlan:
    """Детерминированный слайд-разделитель: слоты заполнены сразу (label/number),
    без LLM-филлера — нумерация предсказуема (01, 02, …), а филлер его пропускает."""
    tid = _SECTION_TEMPLATES[(number - 1) % len(_SECTION_TEMPLATES)]
    spec = library.get(tid)
    label_spec = spec.slots.get("label")
    max_chars = label_spec.max_chars if label_spec else 0
    return SlidePlan(index=1, type=spec.type, template_id=tid, freeform=False,
                     content={"label": _divider_label(section.heading, max_chars),
                              "number": f"{number:02d}"})


def _divider_flags(sections: Sequence[Section]) -> list[bool]:
    """Для каждого раздела: открывает ли он новую ЧАСТЬ деки (нужен разделитель)?

    Распределение — по структуре контента, а не по фиксированному шагу:
    · раздел-«шапка части» (заголовок без тела) — всегда разделитель;
    · раздел верхнего уровня — разделитель, только если СЛЕДУЮЩИЙ раздел вложен
      глубже (то есть это реально родительская глава, а не рядовой слайд).
    Меньше двух разделителей на деку — не главим вовсе (одинокий дивайдер бессмыслен)."""
    n = len(sections)
    levels = [s.level for s in sections if s.level >= 1]
    top = min(levels) if levels else 0
    flags = [False] * n
    for i, section in enumerate(sections):
        if _is_part_title(section):
            flags[i] = True
        elif top and section.level == top:
            nxt = sections[i + 1].level if i + 1 < n else 0
            if nxt > top:
                flags[i] = True
    if sum(flags) < 2:
        return [False] * n
    return flags


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
                    freeform_ok: bool, workers: int = _MAP_WORKERS,
                    images: Sequence[Path] | None = None) -> DeckPlan:
    menu = library_menu(library)
    # images (pptx-rebrand) идут 1:1 с doc.sections — зипуем ДО фильтра пустых
    # разделов, иначе выпавший раздел сдвинул бы весь визуальный ряд.
    imgs: list[Path | None] = list(images) if images else [None] * len(doc.sections)
    pairs = [(s, im) for s, im in zip(doc.sections, imgs) if _has_content(s)]
    sections = [s for s, _ in pairs]
    # В rebrand дивайдеры не синтезируем: структуру задаёт исходная дека, а
    # слайд-«шапка» исходника — полноценный слайд со своим скриншотом.
    dividers = [False] * len(sections) if images else _divider_flags(sections)

    # MAP: LLM-ом планируем всё, кроме разделов-«шапок частей», ставших дивайдерами
    # (у них нет своего тела — LLM не зовём, «титул вместо дивайдера» уходит). Глава
    # верхнего уровня с вводным абзацем тоже получает дивайдер, но её контент
    # остаётся в body (не теряем текст). Подавленный дивайдер → раздел уходит в body.
    body_idx = [i for i, s in enumerate(sections)
                if not (dividers[i] and _is_part_title(s))]
    if body_idx:
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = {i: pool.submit(_plan_section, client, library, menu,
                                      sections[i], freeform_ok=freeform_ok,
                                      image=pairs[i][1])
                       for i in body_idx}
            results = {i: f.result() for i, f in futures.items()}
        finally:
            pool.shutdown()
        body_plans = {i: slides for i, (slides, _) in results.items()}
        # Если из-за молчащего провайдера эвристикой пришлось спасать большую часть
        # разделов — планировать дальше нечего: впереди фаза fill (ещё вызов на
        # слайд), и она молча отдаст деку из пустых заглушек под видом «готово».
        if degradation_is_total(sum(bad for _, bad in results.values()), len(results)):
            raise ProviderUnavailable(
                "сервис ИИ не отвечает: не удалось спланировать разделы")
    else:
        body_plans = {}

    # REDUCE: cover + [разделитель части?] + слайды разделов (в порядке документа);
    # контакты автоматически НЕ добавляем — вставляются вручную при необходимости.
    # Разнообразие и accent — в коде.
    slides: list[SlidePlan] = [_cover_slide(doc, library)]
    divider_no = 0
    for i, section in enumerate(sections):
        if dividers[i]:
            divider_no += 1
            slides.append(_divider_slide(section, library, divider_no))
        if i in body_plans:
            slides.extend(body_plans[i])
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

    Оба входа — map(по разделам)+reduce(в коде); в pptx-rebrand каждый map-вызов
    несёт скриншот своего слайда. Монолитный vision-вызов — только фолбэк при
    рассинхроне «скриншоты ≠ разделы» (рендер исходника отдал не все PNG).
    Бросает SlotValidationError на неизвестный template_id.
    """
    if slide_images and len(slide_images) == len(doc.sections):
        plan = _plan_deck_text(client, doc, library, freeform_ok=freeform_ok,
                               images=slide_images)
    elif slide_images:
        logger.warning("planner.vision_monolith_fallback",
                       images=len(slide_images), sections=len(doc.sections))
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
