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

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from webapp import draft, draft_render, slide_types

_LOG = logging.getLogger(__name__)

# Порог сомнения: слабая уверенность топ-1 ИЛИ топ-1 и топ-2 почти неразличимы.
CONFIDENCE_FLOOR = 0.6
GAP_FLOOR = 0.15

# Потолок разделов. Ниже, чем у чёрного ящика (MAX_DECK_SLIDES=100): стеклянная
# сборка ручная — слайды идут по одному, и деку в сотню слайдов автор так не
# дособерёт, а старт скорил бы все сто разделов параллельными вызовами.
MAX_GLASS_SLIDES = 40

# Стеклянная сборка идёт мимо очереди раннера, то есть и мимо его лимита
# MAX_PER_USER: без своего потолка один автор мог держать сколько угодно
# недозаполненных аутлайнов, каждый со своими вызовами модели.
MAX_ACTIVE_PER_USER = 10

# Эти шаблоны ставит система (обложку — start_glass), в меню раздела им не место.
_EXCLUDED = {"cover", "cover-image", "contacts", "back-cover",
             "section-dots", "section-frame", "blank"}

_DEFAULT_QUESTION = ("Раздел одинаково хорошо ложится в несколько макетов — "
                     "выберите вариант или напишите, что важно показать.")

# Вопрос подбирается под ЧИСЛО вариантов: с одним чипом текст про «несколько
# макетов» — прямая ложь, и автор решал, что выбор просто не работает.
_ONE_CANDIDATE_QUESTION = (
    "ИИ не уверен, что этот макет подходит разделу — подтвердите его, "
    "выберите другой или напишите, что важно показать.")
_FALLBACK_QUESTION = (
    "ИИ не смог подобрать макет для этого раздела (сбой запроса к модели) — "
    "макет ниже подобран простым правилом по структуре текста. "
    "Выберите другой или напишите, что важно показать.")

# Косметические дубли: тот же макет в другом цвете (см. intent statement-green —
# «тот же макет, что statement, но зелёный фон»). Двумя чипами они читаются как
# «выбора нет, оба одинаковые», а их близкая уверенность вдобавок поднимала
# ЛОЖНЫЙ вопрос: gap<GAP_FLOOR срабатывал на разнице фона, а не смысла.
_TWINS = {"statement-green": "statement"}


def _dedupe(cands: list["Candidate"]) -> list["Candidate"]:
    out: list[Candidate] = []
    seen: set[str] = set()
    for c in cands:            # порядок = по убыванию уверенности, оставляем первый
        key = _TWINS.get(c.template_id, c.template_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out

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
- statement/statement-green/quote — АКЦЕНТ, а не макет по умолчанию. Бери их,
  только когда в разделе ровно одна мысль и её нечем разложить. Есть
  перечисление, сравнение, причины и следствия, несколько аспектов или ролей —
  бери карточный/колоночный макет, даже если текст идёт сплошным абзацем.
- Если выбор неочевиден (раздел ложится в разные макеты одинаково хорошо или
  контента мало) — задай в question ОДИН короткий уточняющий вопрос по-русски
  к автору документа. Если выбор ясен, question = "".

МЕНЮ:
{menu}"""


class AnswerNotExpected(Exception):
    """Ответ пришёл слайду, который его не ждёт (устаревшая вкладка, повтор)."""


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
        cands = _dedupe(
            [c for c in choice.candidates if c.template_id in known])[:3]
        if cands:
            return SectionChoice(candidates=cands,
                                 question=choice.question.strip())
    except Exception:  # noqa: BLE001 — фолбэк ниже честно помечает сомнение
        # Молча падать нельзя: снаружи сбой выглядел как «ИИ подумал и выбрал
        # один вариант», и отличить сломанный скоринг от честного сомнения было
        # невозможно ни автору, ни нам.
        _LOG.warning("glass: scoring failed, falling back to heuristic",
                     exc_info=True)
    return SectionChoice(
        candidates=[Candidate(template_id=_fallback_template(section, library),
                              confidence=0.0)],
        question=_FALLBACK_QUESTION)


def _question_for(choice: SectionChoice) -> str:
    """Текст вопроса под конкретное сомнение.

    Свой вопрос модели важнее заготовки; заготовку выбираем по числу вариантов —
    «ложится в несколько макетов» при одном чипе читалось как поломка выбора."""
    if choice.question.strip():
        return choice.question.strip()
    return (_DEFAULT_QUESTION if len(choice.candidates) >= 2
            else _ONE_CANDIDATE_QUESTION)


def _doubtful(choice: SectionChoice) -> bool:
    cands = choice.candidates
    if not cands or cands[0].confidence < CONFIDENCE_FLOOR:
        return True
    if len(cands) >= 2 and cands[0].confidence - cands[1].confidence < GAP_FLOOR:
        return True
    return False


def unfinished_outlines(session_ids: Iterable[str]) -> int:
    """Сколько из этих черновиков ещё ждут дозаполнения (есть слайд с темой, но
    без содержимого). Считаем по плану, а не по флагу в базе: аутлайн — это
    состояние черновика, и оно живёт в plan.json."""
    n = 0
    for sid in session_ids:
        try:
            plan = draft.load_plan(sid)
        except Exception:  # noqa: BLE001 — битый/чужой план в счёт не берём
            continue
        if any(s.brief and not s.filled for s in plan.slides):
            n += 1
    return n


def _cover_content(title: str, library: Any) -> dict:
    """Название документа → слоты обложки.

    Слот заголовка узкий (у «Обложки» 20 симв. — он рисуется огромным капсом), а
    названия документов длинные, и раньше сюда клался весь заголовок целиком:
    рендер резал его ЖЁСТКО по символу («ПРОГРАММА МОДЕРНИЗАЦ»), а подзаголовок
    оставался пустым и подставлялся примером — первый слайд деки выходил
    обрубленным и с текстом-рыбой. Режем по границе слова, хвост уводим в
    подзаголовок: обложка читается как две строки и ничего не теряет."""
    slots = library.get("cover").slots
    cap = slots["title"].max_chars or 0
    sub_cap = slots["subtitle"].max_chars or 0
    text = " ".join((title or "").split())
    if not (cap and len(text) > cap):
        return {"title": text}
    cut = text[:cap]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    head = cut or text[:cap]
    tail = text[len(head):].strip()
    out = {"title": head}
    if tail:
        out["subtitle"] = tail[:sub_cap] if sub_cap else tail
    return out


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
    dropped = max(0, len(sections) - MAX_GLASS_SLIDES)
    sections = sections[:MAX_GLASS_SLIDES]

    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        choices = list(pool.map(
            lambda s: plan_section_candidates(client, library, s), sections))
    finally:
        pool.shutdown()

    slides = [draft.DraftSlide(template_id="cover",
                               content=_cover_content(title, library), filled=True)]
    for section, choice in zip(sections, choices):
        doubt = _doubtful(choice)
        slides.append(draft.DraftSlide(
            template_id=choice.candidates[0].template_id,
            brief=_section_to_text(section),
            status="needs_input" if doubt else None,
            question=_question_for(choice) if doubt else None,
            candidates=[c.template_id for c in choice.candidates],
        ))
    plan = draft.DraftPlan(title=title, slides=slides, notice=(
        f"Документ длиннее потолка: взяли первые {MAX_GLASS_SLIDES} разделов, "
        f"ещё {dropped} не вошли — соберите их отдельной декой." if dropped else ""))
    draft.save_plan(session_id, plan)
    draft_render.render_draft(session_id, plan)
    return plan


# Шаг и ответ — два писателя одного plan.json, и оба держат снимок плана всё
# время вызова LLM (десятки секунд). Замок закрывает только чтение-запись вокруг
# него, сам вызов идёт без замка (приём из app.post_chat). Заявки на слайд живут
# в памяти, а не в плане: процесс умер — заявка исчезла вместе с ним, и слайд не
# останется «вечно в работе».
_PLAN_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
_INFLIGHT: dict[str, set[int]] = {}


def _plan_lock(session_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _PLAN_LOCKS.get(session_id)
        if lock is None:
            lock = _PLAN_LOCKS[session_id] = threading.Lock()
    return lock


def _next_index(plan: draft.DraftPlan, busy: set[int] | None = None) -> int | None:
    """1-based индекс следующего слайда для шага.

    Пропускаем всё, что уже помечено (needs_input — ждёт автора, failed — не
    крутим осечку по кругу за токены) и всё, что сейчас заполняет соседний шаг."""
    for i, s in enumerate(plan.slides, start=1):
        if s.brief and not s.filled and not s.status and i not in (busy or ()):
            return i
    return None


def _fill_one(session_id: str, plan: draft.DraftPlan, index: int,
              client: Any) -> draft.DraftPlan:
    """Заполнить ОДИН слайд через тот же fill_slide, что и чёрный ящик.

    Осечка модели не роняет шаг: слайд деградирует на blank с темой в заголовке
    (ровно как в чёрном ящике) и помечается failed. Пустой content оставлять
    нельзя — draft_render дорисовывает пустые слоты образцами из каталога, и
    автор принял бы выдуманную схему за свою."""
    from htmlslides.library import TemplateLibrary
    from htmlslides.models import SlidePlan
    from htmlslides.pipeline.filler import _fallback_title, fill_slide

    library = TemplateLibrary.load()
    s = plan.slides[index - 1]
    tid = s.template_id or "blank"
    brief = s.brief
    sp = SlidePlan(index=index, type=library.get(tid).type, template_id=tid,
                   content={"brief": brief})
    failed = False
    try:
        sp = fill_slide(client, library, sp, deck_title=plan.title)
        content, out_tid = sp.content, tid
    except Exception:  # noqa: BLE001 — честная заглушка вместо выдумки
        failed = True
        content, out_tid = {"title": _fallback_title(brief)}, "blank"

    # Вызов выше шёл БЕЗ замка: за это время автор мог ответить на вопрос, а
    # соседний шаг — заполнить другие слайды. Вклеиваем ТОЛЬКО свой слайд в
    # свежий план, чтобы не откатить чужую работу нашим устаревшим снимком.
    with _plan_lock(session_id):
        fresh = draft.load_plan(session_id)
        if not 1 <= index <= len(fresh.slides):
            return fresh                      # слайд удалили, пока мы работали
        if fresh.slides[index - 1].brief != brief:
            return fresh                      # автор уточнил слайд — его ответ доведёт сам
        fresh = draft.update_slide(fresh, index, content=content,
                                   template_id=out_tid)
        slide = fresh.slides[index - 1]
        # Схему переводим в typed-слайд: иначе панель узлов и drag редактора её
        # не видят (они работают по slide_type+fields), и автор правил бы JSON.
        # Присваиваем ВСЕГДА, а не только при удачном разборе: typed-поля старше
        # template_id — draft_render рисует слайд из них и игнорирует макет. Ответ
        # «нет, это не схема, а сравнение 2×2» менял template_id, перезаполнял
        # содержимое — и на слайде оставалась прежняя схема. То же и на аварийном
        # blank: заглушка пряталась за диаграммой, которой в плане уже нет.
        typed = slide_types.typed_from_content(out_tid, content)
        slide.slide_type, slide.fields = typed if typed else (None, None)
        slide.filled = True
        slide.status = "failed" if failed else None
        slide.question = None
        draft.save_plan(session_id, fresh)
        draft_render.render_draft(session_id, fresh)
    return fresh


def _marked(plan: draft.DraftPlan, status: str) -> list[int]:
    return [i for i, s in enumerate(plan.slides, start=1) if s.status == status]


def _result(plan: draft.DraftPlan, index: int | None, done: bool) -> dict:
    return {"done": done, "index": index,
            "open_questions": _marked(plan, "needs_input"),
            "failed": _marked(plan, "failed"),
            "plan": plan.model_dump()}


def step_fill(session_id: str, *, client: Any | None = None) -> dict:
    """Один шаг сборки: заполнить следующий незаполненный слайд (кроме
    needs_input). Возвращает индекс шага и остаток — клиент крутит цикл."""
    lock = _plan_lock(session_id)
    with lock:
        plan = draft.load_plan(session_id)
        busy = _INFLIGHT.setdefault(session_id, set())
        index = _next_index(plan, busy)
        if index is None:
            return _result(plan, None, True)
        # Заявка на слайд: без неё второй шаг (вторая вкладка, F5 в середине
        # шага) брал тот же индекс и оплачивал вызов модели дважды.
        busy.add(index)
    try:
        plan = _fill_one(session_id, plan, index, client or _kimi())
    finally:
        with lock:
            _INFLIGHT.get(session_id, set()).discard(index)
    return _result(plan, index, _next_index(plan) is None)


def answer(session_id: str, index: int, *, template_id: str | None = None,
           message: str = "", client: Any | None = None) -> dict:
    """Ответ на вопрос ИИ (в любой момент): чип-кандидат и/или уточнение текстом.

    Слайд доводится сразу же: выбранный макет (или текущий топ-1) + уточнение,
    добавленное в brief, идут через обычный fill_slide."""
    from htmlslides.library import TemplateLibrary

    with _plan_lock(session_id):
        plan = draft.load_plan(session_id)
        if not 1 <= index <= len(plan.slides):
            raise IndexError(f"slide {index} out of range (1..{len(plan.slides)})")
        slide = plan.slides[index - 1]
        # Ответ адресован слайду, который его ждёт. Без проверки устаревшая
        # вкладка (индексы «плывут» при правке состава слайдов) перезаполняла
        # готовый слайд или обложку — та уходила в модель с пустым брифом, и
        # титул сочинялся с нуля.
        if slide.status not in ("needs_input", "failed"):
            raise AnswerNotExpected(
                f"slide {index} is not waiting for an answer")
        if template_id:
            TemplateLibrary.load().get(template_id)   # KeyError на чужом id
            slide.template_id = template_id
        if message.strip():
            slide.brief = (slide.brief + "\n\nУточнение автора: "
                           + message.strip()).strip()
        # Вопрос и кандидатов гасит только успешное заполнение (_fill_one):
        # раньше их стирали здесь, и сбой на следующей строке терял вопрос
        # навсегда — отвечать было уже не на что.
        slide.filled = False                      # перезаполняем с учётом ответа
        draft.save_plan(session_id, plan)
    plan = _fill_one(session_id, plan, index, client or _kimi())
    return _result(plan, index, _next_index(plan) is None)
