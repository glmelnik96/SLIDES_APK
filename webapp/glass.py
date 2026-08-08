"""«Стеклянная сборка» — прозрачный конвейер черновика на глазах пользователя.

Антитеза чёрному ящику: документ раскладывается в аутлайн (слайд на раздел) с
top-3 кандидатами макета и уверенностью; слайды заполняются ПО ОДНОМУ
клиент-управляемыми шагами (POST /glass/step — лента растёт без SSE), а там,
где ИИ сомневается, он НЕ блокирует сборку — помечает слайд needs_input с
вопросом и чипами-кандидатами, степпер его пропускает, ответить можно в любой
момент (POST /glass/answer).

Раннер/planner.plan_deck не трогаем: скоринг кандидатов — свой лёгкий вызов на
раздел (меню шаблонов из planner.library_menu-логики), заполнение — тот же
filler.fill_slide, что у чёрного ящика и чат-агента. DeckPlan-as-truth:
состояние живёт в plan.json (draft.DraftSlide.status/question/candidates).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from webapp import draft, draft_render

# Порог сомнения: слабая уверенность топ-1 ИЛИ топ-1 и топ-2 почти неразличимы.
CONFIDENCE_FLOOR = 0.6
GAP_FLOOR = 0.15

# Эти шаблоны ставит система (обложку — start_glass), в меню раздела им не место.
_EXCLUDED = {"cover", "cover-image", "contacts", "back-cover",
             "section-dots", "section-frame", "blank"}

_DEFAULT_QUESTION = ("Не уверен, какой макет подойдёт лучше — выберите "
                     "вариант или уточните, что важно показать.")

_GLASS_SYSTEM = """\
Ты подбираешь макет слайда для ОДНОГО раздела презентации Cloud.ru.
Дано меню шаблонов (id — назначение) и текст раздела. Верни ТОЛЬКО JSON:
{{"candidates":[{{"template_id":"<id из меню>","confidence":0.9}}],
 "question":""}}

Правила:
- candidates — до 3 вариантов по убыванию уверенности; template_id СТРОГО из
  меню; confidence 0..1 — насколько макет подходит именно этому разделу.
- ДАННЫЕ ВАЖНЕЕ ТЕКСТА: числа/метрики/таблицы → профильный data-шаблон;
  процесс с ветвлениями, цикл, воронка, оргсхема → diagram; этапы с датами →
  timeline; иначе текстовый по числу пунктов.
- Если выбор неочевиден (раздел ложится в разные макеты одинаково хорошо или
  контента мало) — задай в question ОДИН короткий уточняющий вопрос по-русски
  к автору документа. Если выбор ясен, question = "".

МЕНЮ:
{menu}"""


class Candidate(BaseModel):
    template_id: str
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class SectionChoice(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list)
    question: str = ""


def _kimi():
    from htmlslides.pipeline.client import KimiClient
    return KimiClient(timeout=280.0, max_retries=1,
                      extra_body={"thinking": {"type": "disabled"}})


def _menu(library) -> str:
    return "\n".join(f"- {t.id} ({t.type}): {t.intent}"
                     for t in library.templates if t.id not in _EXCLUDED)


def plan_section_candidates(client: Any, library, section) -> SectionChoice:
    """Top-3 кандидата макета с уверенностью для одного раздела.

    Любой сбой (сеть/формат/чужие id) → эвристика planner._fallback_template с
    confidence 0.0 — честное «место сомнения», а не тихий уверенный выбор."""
    from htmlslides.pipeline.planner import (_fallback_template,
                                             _section_to_text)
    text = _section_to_text(section)
    known = {t.id for t in library.templates if t.id not in _EXCLUDED}
    try:
        choice = client.chat_json(
            [{"role": "system", "content": _GLASS_SYSTEM.format(menu=_menu(library))},
             {"role": "user", "content": f"Текст раздела:\n{text}"}],
            SectionChoice, max_tokens=2048,
            extra_body={"thinking": {"type": "disabled"}})
        cands = [c for c in choice.candidates if c.template_id in known][:3]
        if cands:
            return SectionChoice(candidates=cands,
                                 question=choice.question.strip())
    except Exception:  # noqa: BLE001 — фолбэк ниже честно помечает сомнение
        pass
    return SectionChoice(
        candidates=[Candidate(template_id=_fallback_template(section, library),
                              confidence=0.0)],
        question="")


def _doubtful(choice: SectionChoice) -> bool:
    cands = choice.candidates
    if not cands or cands[0].confidence < CONFIDENCE_FLOOR:
        return True
    if len(cands) >= 2 and cands[0].confidence - cands[1].confidence < GAP_FLOOR:
        return True
    return False


def start_glass(session_id: str, source: Path, *, client: Any | None = None,
                workers: int = 4) -> draft.DraftPlan:
    """Документ → прозрачный аутлайн: обложка + слайд на раздел с кандидатами.

    Слайды НЕ заполняются здесь (это делают шаги /glass/step) — старт быстрый:
    parse + параллельный скоринг кандидатов (лёгкий вызов на раздел)."""
    from htmlslides.library import TemplateLibrary
    from htmlslides.parsers import parse_file
    from htmlslides.pipeline.planner import (_has_content, _is_part_title,
                                             _section_to_text)

    client = client or _kimi()
    library = TemplateLibrary.load()
    doc = parse_file(source)
    title = (doc.title or source.stem or "Презентация").strip()
    sections = [s for s in doc.sections
                if _has_content(s) and not _is_part_title(s)]

    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        choices = list(pool.map(
            lambda s: plan_section_candidates(client, library, s), sections))
    finally:
        pool.shutdown()

    slides = [draft.DraftSlide(template_id="cover",
                               content={"title": title}, filled=True)]
    for section, choice in zip(sections, choices):
        doubt = _doubtful(choice)
        slides.append(draft.DraftSlide(
            template_id=choice.candidates[0].template_id,
            brief=_section_to_text(section),
            status="needs_input" if doubt else None,
            question=(choice.question or _DEFAULT_QUESTION) if doubt else None,
            candidates=[c.template_id for c in choice.candidates],
        ))
    plan = draft.DraftPlan(title=title, slides=slides)
    draft.save_plan(session_id, plan)
    draft_render.render_draft(session_id, plan)
    return plan


def _next_index(plan: draft.DraftPlan) -> int | None:
    """1-based индекс следующего слайда для шага; needs_input пропускаем."""
    for i, s in enumerate(plan.slides, start=1):
        if s.brief and not s.filled and s.status != "needs_input":
            return i
    return None


def _fill_one(session_id: str, plan: draft.DraftPlan, index: int,
              client: Any) -> draft.DraftPlan:
    """Заполнить ОДИН слайд через тот же fill_slide, что и чёрный ящик.

    Осечка модели не роняет шаг: слайд остаётся аутлайном с выбранным макетом
    (паттерн chat_agent.build_outline), сборка едет дальше."""
    from htmlslides.library import TemplateLibrary
    from htmlslides.models import SlidePlan
    from htmlslides.pipeline.filler import fill_slide

    library = TemplateLibrary.load()
    s = plan.slides[index - 1]
    tid = s.template_id or "blank"
    spec = library.get(tid)
    sp = SlidePlan(index=index, type=spec.type, template_id=tid,
                   content={"brief": s.brief})
    try:
        sp = fill_slide(client, library, sp, deck_title=plan.title)
        plan = draft.update_slide(plan, index, content=sp.content,
                                  template_id=tid)
    except Exception:  # noqa: BLE001 — макет сохранён, контент добьют позже
        plan = draft.update_slide(plan, index, template_id=tid)
    slide = plan.slides[index - 1]
    slide.filled = True
    slide.status = None
    slide.question = None
    draft.save_plan(session_id, plan)
    draft_render.render_draft(session_id, plan)
    return plan


def step_fill(session_id: str, *, client: Any | None = None) -> dict:
    """Один шаг сборки: заполнить следующий незаполненный слайд (кроме
    needs_input). Возвращает индекс шага и остаток — клиент крутит цикл."""
    plan = draft.load_plan(session_id)
    index = _next_index(plan)
    if index is None:
        open_questions = [i for i, s in enumerate(plan.slides, start=1)
                          if s.status == "needs_input"]
        return {"done": True, "index": None,
                "open_questions": open_questions,
                "plan": plan.model_dump()}
    plan = _fill_one(session_id, plan, index, client or _kimi())
    return {"done": _next_index(plan) is None, "index": index,
            "open_questions": [i for i, s in enumerate(plan.slides, start=1)
                               if s.status == "needs_input"],
            "plan": plan.model_dump()}


def answer(session_id: str, index: int, *, template_id: str | None = None,
           message: str = "", client: Any | None = None) -> dict:
    """Ответ на вопрос ИИ (в любой момент): чип-кандидат и/или уточнение текстом.

    Слайд доводится сразу же: выбранный макет (или текущий топ-1) + уточнение,
    добавленное в brief, идут через обычный fill_slide."""
    from htmlslides.library import TemplateLibrary

    plan = draft.load_plan(session_id)
    if not 1 <= index <= len(plan.slides):
        raise IndexError(f"slide {index} out of range (1..{len(plan.slides)})")
    slide = plan.slides[index - 1]
    if template_id:
        TemplateLibrary.load().get(template_id)   # KeyError на чужом id
        slide.template_id = template_id
    if message.strip():
        slide.brief = (slide.brief + "\n\nУточнение автора: "
                       + message.strip()).strip()
    slide.status = None
    slide.question = None
    slide.filled = False                          # перезаполняем с учётом ответа
    draft.save_plan(session_id, plan)
    plan = _fill_one(session_id, plan, index, client or _kimi())
    return {"index": index,
            "open_questions": [i for i, s in enumerate(plan.slides, start=1)
                               if s.status == "needs_input"],
            "plan": plan.model_dump()}
