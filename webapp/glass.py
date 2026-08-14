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
import re
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from webapp import draft, draft_render, slide_types
from webapp.paths import session_dir

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


class NoContext(Exception):
    """Перезаполнять нечем: у слайда нет ни брифа, ни собственного текста."""


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


def plan_section_candidates(client: Any, library, section, *,
                            text: str | None = None) -> SectionChoice:
    """Top-3 кандидата макета с уверенностью для одного раздела.

    ``text`` — готовый бриф, если он уже посчитан и дополнен (раздел, прочитанный
    с картинки исходного слайда). По умолчанию берём текст самого раздела.

    Любой сбой (сеть/формат/чужие id) → эвристика planner._fallback_template с
    confidence 0.0 — честное «место сомнения», а не тихий уверенный выбор."""
    from htmlslides.pipeline.planner import (_fallback_template,
                                             _section_to_text)
    text = _section_to_text(section) if text is None else text
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


# Раздел, от которого в тексте остались одни картинки. Стеклянная сборка идёт
# мимо vision-ветки чёрного ящика (там раздел дополняется рендером исходного
# слайда), и филлер на таком брифе не «собирал хуже» — он ДОСОЧИНЯЛ: на слайде
# появлялись тезисы и цифры, которых в документе нет вообще. Молчать об этом
# нельзя, поэтому такой раздел уходит в needs_input с прямым вопросом.
_IMAGE_MARK = re.compile(r"\[картинка:[^\]]*\]")
MIN_SECTION_CHARS = 80

_IMAGE_ONLY_QUESTION = (
    "В этом разделе исходника только изображения — текста, по которому можно "
    "собрать слайд, нет. Опишите, что на них показано: иначе ИИ соберёт слайд "
    "по одному заголовку и додумает остальное.")


def _image_only(section: Any, text: str) -> bool:
    """В разделе есть картинки, а текста — меньше одной фразы.

    Меряем по тексту БЕЗ пометок «[картинка: …]»: сами пометки к содержанию
    отношения не имеют, но длину брифа раздували, и раздел из шести картинок
    выглядел насыщенным."""
    from htmlslides.parsers.base import ImageBlock
    if not any(isinstance(b, ImageBlock) for b in getattr(section, "blocks", [])):
        return False
    body = _IMAGE_MARK.sub(" ", text or "")
    return len(" ".join(body.split())) < MIN_SECTION_CHARS


_VISION_SYSTEM = """\
Ты читаешь ОДИН слайд чужой презентации по его картинке. Верни ТОЛЬКО JSON
{"text": "..."} — что на слайде написано и нарисовано, по-русски.

Правила:
- Сначала перепиши ВЕСЬ видимый текст: заголовок, пункты, подписи, числа с их
  единицами. Дословно, ничего не сокращая по смыслу.
- Потом одной-двумя фразами опиши, что показывает картинка: схема (что с чем
  связано), график (что по осям, куда идёт), таблица (какие колонки), логотипы,
  фото.
- НЕ добавляй выводов, оценок и того, чего на слайде нет. Не видно — не пиши.
- Пусто или неразборчиво — верни "".
"""

# Заметка в брифе: автор в панели контекста должен видеть, что этот текст пришёл
# не из документа, а прочитан с картинки — и мог его поправить.
_IMAGE_READ_NOTE = "Прочитано с изображения исходного слайда:"


class _SlideRead(BaseModel):
    text: str = ""


def _read_one(client: Any, png: Path) -> str:
    from htmlslides.pipeline.client import image_part
    try:
        got = client.chat_json(
            [{"role": "system", "content": _VISION_SYSTEM},
             {"role": "user", "content": [
                 {"type": "text", "text": "Прочитай этот слайд."},
                 image_part(png)]}],
            _SlideRead, max_tokens=2048, retries=1)
        return " ".join((got.text or "").split())
    except Exception:  # noqa: BLE001 — не прочиталось, останется вопрос автору
        _LOG.warning("glass: vision read failed for %s", png, exc_info=True)
        return ""


def _read_blind_sections(source: Path, session_id: str, pairs: list,
                         briefs: list[str], client: Any,
                         workers: int) -> dict[int, str]:
    """Дочитать «слепые» разделы pptx с картинок исходных слайдов.

    Стеклянная сборка идёт мимо vision-ветки чёрного ящика (там скриншот
    исходного слайда прикладывается к планировщику), и раздел, где весь смысл
    лежит в картинке, приезжал в филлер пустым — а тот не «собирал хуже», он
    ДОСОЧИНЯЛ тезисы и цифры. Рендерим ЛЕНИВО: только для pptx и только когда
    слепые разделы вообще есть — LibreOffice на деку стоит десятки секунд, и
    платить их за документ без картинок незачем. Отдаём {позиция: описание};
    что не прочиталось, останется вопросом автору."""
    from htmlslides.parsers.render import RenderUnavailable, render_pptx_pngs

    blind = [i for i, (_, s) in enumerate(pairs) if _image_only(s, briefs[i])]
    if not blind or source.suffix.lower() != ".pptx":
        return {}
    try:
        pngs = render_pptx_pngs(source, session_dir(session_id) / "source-slides")
    except (RenderUnavailable, OSError, ValueError) as exc:
        _LOG.warning("glass: source render unavailable (%s)", exc)
        return {}
    except Exception:  # noqa: BLE001 — споткнулись на файле, не роняем старт
        _LOG.warning("glass: source render failed", exc_info=True)
        return {}

    # Картинки идут 1:1 с ИСХОДНЫМИ разделами документа, поэтому в pairs хранится
    # исходный индекс: отфильтрованный пустой раздел иначе сдвинул бы весь ряд.
    jobs = [(i, pngs[pairs[i][0]]) for i in blind if pairs[i][0] < len(pngs)]
    if not jobs:
        return {}
    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        texts = list(pool.map(lambda j: _read_one(client, j[1]), jobs))
    finally:
        pool.shutdown()
    return {i: t for (i, _), t in zip(jobs, texts) if t}


def _doubtful(choice: SectionChoice) -> bool:
    cands = choice.candidates
    if not cands or cands[0].confidence < CONFIDENCE_FLOOR:
        return True
    if len(cands) >= 2 and cands[0].confidence - cands[1].confidence < GAP_FLOOR:
        return True
    return False


# Разделы скорятся ПАРАЛЛЕЛЬНО и независимо, поэтому однородный документ давал
# однородную деку: на реальном прогоне семь разделов подряд получили cards-6 —
# 45% деки одним макетом. Модель тут не ошибается (каждый раздел правда ложится
# в карточки), ошибается процесс: соседа она не видит. Разводим повторы уже
# ПОСЛЕ скоринга, вторым кандидатом самой модели.
REPEAT_RUN = 3          # столько одинаковых подряд — уже узор, а не выбор
# Годность запасного макета меряем ПО НЕМУ САМОМУ, а не по отрыву от первого:
# замер на живой модели (Партнёры, 28 разделов) дал разрыв топ-1/топ-2 в
# 0.35–0.47 практически везде — любой относительный порог либо не срабатывал
# никогда, либо срабатывал всегда. При этом сам запасной вариант шёл на 0.45–0.65
# («тоже подходит, просто хуже»), и такой макет раз в три слайда честнее, чем
# восемь одинаковых сеток подряд.
SWAP_FLOOR = 0.45


def _varied_templates(choices: list[SectionChoice]) -> list[str]:
    """Макет на раздел, с разведением подряд идущих повторов.

    Меняем ТОЛЬКО применяемый макет, не трогая уверенность и чипы: `_doubtful`
    считается по честному ответу модели, иначе разведение повторов начало бы
    плодить вопросы на ровном месте. Запасной берём не любой, а от SWAP_FLOOR:
    разнообразие не стоит макета, который разделу не подходит."""
    picked: list[str] = []
    run = 0
    for choice in choices:
        cands = choice.candidates
        if not cands:
            picked.append("")
            run = 0
            continue
        top = cands[0].template_id
        prev = picked[-1] if picked else None
        if top == prev and run >= REPEAT_RUN - 1:
            alt = next((c for c in cands[1:]
                        if c.template_id != prev and c.confidence >= SWAP_FLOOR),
                       None)
            if alt:
                top = alt.template_id
        run = run + 1 if top == prev else 1
        picked.append(top)
    return picked


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


def _deck_title(doc: Any, origin_name: str | None, sections: list) -> str:
    """Название деки: титул документа -> имя ЗАГРУЖЕННОГО файла -> первый раздел.

    Раньше здесь стоял `source.stem`, но source — это путь на диске сервиса, куда
    загрузка всегда ложится как `input.pptx`: у любой pptx без титульного текста
    обложка выходила с надписью «INPUT». Имя файла автора мы теперь получаем
    отдельным аргументом; подчёркивания в нём — почти всегда пробелы
    («Партнёры_Инфраструктура_для_ИИ»)."""
    title = " ".join((getattr(doc, "title", "") or "").split())
    if title:
        return title
    stem = Path(origin_name or "").stem.replace("_", " ")
    stem = " ".join(stem.split())
    if stem:
        return stem
    for section in sections:
        heading = " ".join((section.heading or "").split())
        if heading:
            return heading
    return "Презентация"


def start_glass(session_id: str, source: Path, *, client: Any | None = None,
                workers: int = 4, origin_name: str | None = None,
                offset: int = 0, title: str | None = None) -> draft.DraftPlan:
    """Документ → прозрачный аутлайн: обложка + слайд на раздел с кандидатами.

    Слайды НЕ заполняются здесь (это делают шаги /glass/step) — старт быстрый:
    parse + параллельный скоринг кандидатов (лёгкий вызов на раздел).

    ``offset`` — сколько подходящих разделов пропустить сверху: так вторая дека
    забирает хвост документа, не влезший в потолок (см. continue_glass).
    ``title`` — готовый титул для такой деки: имя файла на диске всегда
    `input.pptx`, и выводить его заново означало бы получить другую обложку."""
    from htmlslides.library import TemplateLibrary
    from htmlslides.parsers import parse_file
    from htmlslides.pipeline.planner import (_has_content, _is_part_title,
                                             _section_to_text)

    client = client or _kimi()
    library = TemplateLibrary.load()
    doc = parse_file(source)
    # Исходный номер раздела едет рядом: по нему берётся картинка исходного
    # слайда (см. _read_blind_sections) — выпавший раздел иначе сдвинул бы ряд.
    pairs = [(i, s) for i, s in enumerate(doc.sections)
             if _has_content(s) and not _is_part_title(s)]
    offset = max(0, offset)
    pairs = pairs[offset:]
    dropped = max(0, len(pairs) - MAX_GLASS_SLIDES)
    pairs = pairs[:MAX_GLASS_SLIDES]
    if not pairs:
        raise ValueError("в документе не осталось разделов для сборки")
    sections = [s for _, s in pairs]
    title = " ".join((title or "").split()) or _deck_title(doc, origin_name,
                                                           sections)

    briefs = [_section_to_text(s) for s in sections]
    for pos, text in _read_blind_sections(source, session_id, pairs, briefs,
                                          client, workers).items():
        briefs[pos] = f"{briefs[pos]}\n\n{_IMAGE_READ_NOTE} {text}".strip()

    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        # Скорим по обогащённому брифу: раздел, прочитанный с картинки, для
        # выбора макета ничем не хуже раздела с текстом в XML.
        choices = list(pool.map(
            lambda p: plan_section_candidates(client, library, p[0], text=p[1]),
            list(zip(sections, briefs))))
    finally:
        pool.shutdown()

    slides = [draft.DraftSlide(template_id="cover",
                               content=_cover_content(title, library), filled=True)]
    for section, brief, choice, tid in zip(sections, briefs, choices,
                                           _varied_templates(choices)):
        blind = _image_only(section, brief)
        doubt = blind or _doubtful(choice)
        slides.append(draft.DraftSlide(
            template_id=tid or choice.candidates[0].template_id,
            brief=brief,
            status="needs_input" if doubt else None,
            question=(_IMAGE_ONLY_QUESTION if blind else _question_for(choice))
            if doubt else None,
            candidates=[c.template_id for c in choice.candidates],
        ))
    plan = draft.DraftPlan(
        title=title, slides=slides,
        # Хвост называем числом и оставляем адрес, с которого его продолжать:
        # кнопка в панели сборки заводит вторую деку ровно отсюда, так что
        # обрезка перестала быть безвозвратной.
        rest=dropped, rest_from=(offset + len(pairs)) if dropped else 0,
        notice=(f"Документ длиннее потолка: взяли разделы "
                f"{offset + 1}–{offset + len(pairs)}, ещё {dropped} не вошли."
                if dropped else ""))
    draft.save_plan(session_id, plan)
    draft_render.render_draft(session_id, plan)
    return plan


def continue_glass(prev_session_id: str, session_id: str, *,
                   client: Any | None = None,
                   workers: int = 4) -> draft.DraftPlan:
    """Вторая дека из хвоста документа, не влезшего в потолок первой.

    Потолок в 40 разделов поднимать некуда (сорок слайдов автор и так собирает
    по одному больше получаса), но и терять хвост молча нельзя: раньше notice
    просто советовал «соберите их отдельной декой», а как — автор придумывал
    сам, обычно резал исходный файл руками. Исходник уже лежит в сессии, номер
    первого невзятого раздела — в плане, титул наследуем от первой деки (на
    диске файл всегда `input.<ext>`, и вывод имени дал бы обложку «INPUT»)."""
    prev = draft.load_plan(prev_session_id)
    if not prev.rest_from:
        raise ValueError("в документе не осталось разделов для сборки")
    src = _source_file(prev_session_id)
    if src is None:
        raise FileNotFoundError("исходный документ этой сборки уже удалён")
    dest = session_dir(session_id) / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    out = start_glass(session_id, dest, client=client, workers=workers,
                      offset=prev.rest_from, title=prev.title)
    # Хвост забран — снимаем заявку с первой деки, иначе её панель предлагала бы
    # собрать то же самое ещё раз (предложение живёт в плане, а не во вкладке).
    with _plan_lock(prev_session_id):
        fresh = draft.load_plan(prev_session_id)
        fresh.rest = fresh.rest_from = 0
        draft.save_plan(prev_session_id, fresh)
    return out


def _source_file(session_id: str) -> Path | None:
    """Загруженный документ сессии (`input.<ext>`, кладёт /glass/start)."""
    for path in sorted(session_dir(session_id).glob("input.*")):
        if path.is_file():
            return path
    return None


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


def _blocking_index(plan: draft.DraftPlan) -> int | None:
    """Первый по порядку слайд, который ЖДЁТ ответа автора и стоит раньше всего,
    что ещё предстоит заполнить.

    Сборка на нём останавливается. Раньше степпер такие слайды просто обходил:
    автор читал вопрос, а лента слева в это время продолжала расти — «непонятный
    процесс, ИИ заполняет слайды, пока ты отвечаешь». Пауза делает порядок
    честным: дошли до сомнительного места — стоим, ответили — идём дальше.
    Осечки (failed) не блокируют: их чинят в редакторе, а не паузой."""
    for i, s in enumerate(plan.slides, start=1):
        if not s.brief or s.filled:
            continue
        if s.status == "needs_input":
            return i
        if s.status is None:
            return None            # раньше вопроса есть что заполнять
    return None


def _fill_one(session_id: str, plan: draft.DraftPlan, index: int,
              client: Any, *, kind: str = "",
              context: str | None = None) -> draft.DraftPlan:
    """Заполнить ОДИН слайд через тот же fill_slide, что и чёрный ящик.

    Осечка модели не роняет шаг: слайд деградирует на blank с темой в заголовке
    (ровно как в чёрном ящике) и помечается failed. Пустой content оставлять
    нельзя — draft_render дорисовывает пустые слоты образцами из каталога, и
    автор принял бы выдуманную схему за свою.

    ``context`` — чем заполнять, если это не brief слайда (перезаполнение слайда,
    добавленного руками: там брифа нет, а текст на слайде есть). В план не
    попадает: brief — это «фрагмент исходного документа», и подменять его текстом
    самого слайда значит соврать в панели контекста."""
    from htmlslides.library import TemplateLibrary
    from htmlslides.models import SlidePlan
    from htmlslides.pipeline.filler import _fallback_title, fill_slide

    library = TemplateLibrary.load()
    s = plan.slides[index - 1]
    tid = s.template_id or "blank"
    plan_brief = s.brief             # чем слайд помечен в плане — для сверки ниже
    brief = plan_brief if context is None else context
    sp = SlidePlan(index=index, type=library.get(tid).type, template_id=tid,
                   content={"brief": brief})
    failed = False
    try:
        sp = fill_slide(client, library, sp, deck_title=plan.title,
                        diagram_kind=kind)
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
        if fresh.slides[index - 1].brief != plan_brief:
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
        # Осечка меняет МАКЕТ слайда (выбранный → blank), и молчать об этом
        # нельзя: со стороны автора это выглядело как «выбрал один макет,
        # применился другой». Причину кладём в тот же question — панель покажет
        # её карточкой с чипами, и ответ (тот же /glass/answer) заполнит заново.
        slide.question = (
            f"Не удалось заполнить макет «{_tpl_name(library, tid)}» — слайд "
            "стоит заглушкой с темой в заголовке. Выберите макет ещё раз или "
            "уточните, что показать: попробую снова." if failed else None)
        draft.save_plan(session_id, fresh)
        draft_render.render_draft(session_id, fresh)
    return fresh


def _tpl_name(library: Any, tid: str) -> str:
    try:
        return library.get(tid).display_name or tid
    except Exception:  # noqa: BLE001 — имя макета не стоит падения шага
        return tid


def _marked(plan: draft.DraftPlan, status: str) -> list[int]:
    return [i for i, s in enumerate(plan.slides, start=1) if s.status == status]


def _result(plan: draft.DraftPlan, index: int | None, done: bool) -> dict:
    # blocked — слайд, на котором сборка встала в ожидании ответа автора.
    # Клиент по нему останавливает цикл шагов (пауза), а не крутит его вхолостую.
    return {"done": done, "index": index, "blocked": _blocking_index(plan),
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
        # Пауза сильнее шага: дошли до слайда с вопросом — дальше не идём, пока
        # автор не ответит. Иначе он читает вопрос, а сборка убегает вперёд.
        if _blocking_index(plan) is not None:
            return _result(plan, None, False)
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
           kind: str | None = None, message: str = "",
           client: Any | None = None) -> dict:
    """Ответ на вопрос ИИ (в любой момент): чип-кандидат и/или уточнение текстом.

    Слайд доводится сразу же: выбранный макет (или текущий топ-1) + уточнение,
    добавленное в brief, идут через обычный fill_slide.

    ``kind`` — тип схемы из мастера «Схема». Едет ДАННЫМИ до филлера диаграмм
    (жёсткое требование), а не строчкой в тексте уточнения: раньше выбор автора
    («воронка») был для модели необязательной подсказкой."""
    from htmlslides.diagrams import AVAILABLE_KINDS
    from htmlslides.library import TemplateLibrary

    kind = (kind or "").strip()
    if kind and kind not in AVAILABLE_KINDS:
        raise KeyError(f"unknown diagram kind: {kind}")

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
    plan = _fill_one(session_id, plan, index, client or _kimi(), kind=kind)
    return _result(plan, index, _next_index(plan) is None)


_TAGS = re.compile(r"<[^>]+>")


def _slide_context(slide: draft.DraftSlide) -> str:
    """Текст самого слайда — контекст для перезаполнения слайда без брифа.

    Слайд, добавленный руками, исходного фрагмента документа не имеет, и без
    этого «перезаполнить по документу» на нём просто не работало бы: автор
    выбрал бы макет и получил пустоту вместо своего текста."""
    out: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str):
            s = v.strip()
            if not s or s.startswith("data:"):
                return                       # картинка — не текст
            if s.startswith("<"):
                s = _TAGS.sub(" ", s)        # свободный слайд: разметка модели
            s = " ".join(s.split())
            if s:
                out.append(s)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)

    # fields старше content: у типизированного слайда (схема) content может быть
    # пустым, а поля — заполненными.
    walk(slide.fields if slide.fields else slide.content)
    return "\n".join(out)


def refill_slide(session_id: str, index: int, *, template_id: str | None = None,
                 kind: str | None = None, client: Any | None = None) -> dict:
    """Сменить макет готового слайда и заново заполнить его под новый макет.

    Механическая смена макета (в редакторе) переносит текст слот-в-слот: три
    пункта списка так и остаются тремя пунктами, даже если новый макет — про
    цифры или схему. Здесь макет меняет ИИ: он перечитывает исходный фрагмент
    документа и пишет содержимое заново — под то, что новый макет умеет показать.

    В отличие от ``answer`` статус слайда не проверяется: это обычное действие
    автора над готовым слайдом, а не ответ на вопрос сборки."""
    from htmlslides.diagrams import AVAILABLE_KINDS
    from htmlslides.library import TemplateLibrary

    kind = (kind or "").strip()
    if kind and kind not in AVAILABLE_KINDS:
        raise KeyError(f"unknown diagram kind: {kind}")

    with _plan_lock(session_id):
        plan = draft.load_plan(session_id)
        if not 1 <= index <= len(plan.slides):
            raise IndexError(f"slide {index} out of range (1..{len(plan.slides)})")
        slide = plan.slides[index - 1]
        context = slide.brief.strip() or _slide_context(slide)
        if not context:
            raise NoContext(f"slide {index} has no text to refill from")
        if template_id:
            TemplateLibrary.load().get(template_id)   # KeyError на чужом id
            slide.template_id = template_id
        # Свободный слайд возвращается под макет: иначе draft_render продолжил бы
        # рисовать старый html и результат заполнения был бы не виден.
        slide.freeform = False
        slide.filled = False
        draft.save_plan(session_id, plan)
    plan = _fill_one(session_id, plan, index, client or _kimi(), kind=kind,
                     context=context)
    return _result(plan, index, _next_index(plan) is None)
