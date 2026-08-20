"""«Стеклянная сборка» — прозрачный конвейер черновика на глазах пользователя.

Антитеза чёрному ящику. Старт (start_glass) — parse-only, БЕЗ единого вызова
модели: документ мгновенно режется на слайды-заготовки (status="unscored",
временный макет от эвристики планировщика), страница сразу уходит в редактор.
Дальше конвейер ленивый: score_next размечает ОДИН слайд (кандидаты макета,
уверенность, ленивый vision для слепых pptx-разделов), step_fill за вызов
делает ОДНО действие — заполняет готовый слайд, а если готовых нет, сам
размечает следующий. Там, где ИИ сомневается, он НЕ блокирует сборку: слайд
помечается needs_input с вопросом и чипами-кандидатами, конвейер идёт дальше,
ответить можно в любой момент (POST /glass/answer). Каждый шаг возвращает
``action`` ("score"|"fill"|None) — клиент останавливает цикл на None; ``done``
строго «всё заполнено и вопросов нет».

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
import time
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


# Часы шага — подменяются в тестах, чтобы «медленный сбой» не ждать 5 минут.
_now = time.monotonic

# Быстрая осечка заполнения (< бюджета) получает один повтор: обрыв соединения
# или битый JSON часто разовые. Медленный сбой — это таймаут; повтор удвоил бы
# шаг до 2×280с и вылетел за клиентский GLASS_FETCH_MS=340с, поэтому не ретраим.
_RETRY_BUDGET_S = 90.0

# Один клиент на процесс, как у раннера: у KimiClient липкий автофолбэк на
# резервную модель (_switch по преflight/полосе сбоев), и клиент-на-вызов
# заново пробовал бы мёртвый примари на КАЖДОМ шаге — минус 280с на слайд.
_CLIENT: Any = None
_CLIENT_LOCK = threading.Lock()


def _kimi():
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            from htmlslides.pipeline.client import KimiClient
            c = KimiClient(timeout=280.0, max_retries=1,
                           extra_body={"thinking": {"type": "disabled"}})
            try:
                c.preflight()
            except Exception:  # noqa: BLE001 — шаги деградируют честно сами
                # Мёртвый преflight не повод остаться без клиента: сеть могла
                # моргнуть на старте, а каждый шаг и так честно превращает свой
                # сбой в заглушку с вопросом. Клиент кэшируем как есть.
                _LOG.warning("glass: preflight не прошёл", exc_info=True)
            _CLIENT = c
    return _CLIENT


def _menu(library) -> str:
    return "\n".join(f"- {t.id} ({t.type}): {t.intent}"
                     for t in library.templates if t.id not in _EXCLUDED)


def _score_text(client: Any, library, text: str) -> SectionChoice | None:
    """LLM-скоринг брифа: top-3 кандидата или None при сбое/пустом ответе.

    Фолбэк решает вызывающий: у start-пути есть Section для эвристики, у
    ленивого score-пути — временный макет, поставленный эвристикой на старте."""
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
    except Exception:  # noqa: BLE001 — фолбэк вызывающего честно помечает сомнение
        # Молча падать нельзя: снаружи сбой выглядел как «ИИ подумал и выбрал
        # один вариант», и отличить сломанный скоринг от честного сомнения было
        # невозможно ни автору, ни нам.
        _LOG.warning("glass: scoring failed, falling back to heuristic",
                     exc_info=True)
    return None


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
    got = _score_text(client, library, text)
    if got is not None:
        return got
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


def _image_only(text: str) -> bool:
    """В брифе есть пометки картинок, а текста — меньше одной фразы.

    Меряем по тексту БЕЗ пометок «[картинка: …]» (их эмитит planner._block_to_text
    для ImageBlock): сами пометки к содержанию отношения не имеют, но длину брифа
    раздували, и раздел из шести картинок выглядел насыщенным. Детекция по брифу,
    а не по Section: при ленивом скоринге исходного разбора уже нет."""
    if not _IMAGE_MARK.search(text or ""):
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


def _read_source_slide(session_id: str, src_index: int | None,
                       client: Any) -> str:
    """Дочитать «слепой» раздел pptx с картинки исходного слайда — ЛЕНИВО,
    при скоринге именно этого раздела.

    Стеклянная сборка идёт мимо vision-ветки чёрного ящика (там скриншот
    исходного слайда прикладывается к планировщику), и раздел, где весь смысл
    лежит в картинке, приезжал в филлер пустым — а тот не «собирал хуже», он
    ДОСОЧИНЯЛ тезисы и цифры. Рендерим только для pptx и только когда до
    слепого раздела дошёл скоринг — LibreOffice на деку стоит десятки секунд,
    и платить их на старте за все разделы разом означало «сайт лежит».
    Пусто = не прочиталось, останется вопросом автору."""
    from htmlslides.parsers.render import RenderUnavailable, render_pptx_pngs

    source = _source_file(session_id)
    if (source is None or source.suffix.lower() != ".pptx"
            or src_index is None):
        return ""
    out_dir = session_dir(session_id) / "source-slides"
    # Рендер один на сессию: первый слепой раздел платит за всех, остальные
    # берут готовые png с диска.
    pngs = sorted(out_dir.glob("*.png"))
    if not pngs:
        try:
            pngs = render_pptx_pngs(source, out_dir)
        except (RenderUnavailable, OSError, ValueError) as exc:
            _LOG.warning("glass: source render unavailable (%s)", exc)
            return ""
        except Exception:  # noqa: BLE001 — споткнулись на файле, не роняем шаг
            _LOG.warning("glass: source render failed", exc_info=True)
            return ""
    # Картинки идут 1:1 с ИСХОДНЫМИ разделами документа — на слайде хранится
    # исходный индекс (src_index): выпавший раздел иначе сдвинул бы весь ряд.
    if not 0 <= src_index < len(pngs):
        return ""
    return _read_one(client, pngs[src_index])


def _doubtful(choice: SectionChoice) -> bool:
    cands = choice.candidates
    if not cands or cands[0].confidence < CONFIDENCE_FLOOR:
        return True
    if len(cands) >= 2 and cands[0].confidence - cands[1].confidence < GAP_FLOOR:
        return True
    return False


# Однородный документ давал однородную деку: на реальном прогоне семь разделов
# подряд получили cards-6 — 45% деки одним макетом. Модель тут не ошибается
# (каждый раздел правда ложится в карточки), ошибается процесс: соседа она не
# видит. Разводим повторы уже ПОСЛЕ скоринга, вторым кандидатом самой модели;
# скоринг ленивый и последовательный, так что предыдущие выборы уже в плане.
REPEAT_RUN = 3          # столько одинаковых подряд — уже узор, а не выбор
# Годность запасного макета меряем ПО НЕМУ САМОМУ, а не по отрыву от первого:
# замер на живой модели (Партнёры, 28 разделов) дал разрыв топ-1/топ-2 в
# 0.35–0.47 практически везде — любой относительный порог либо не срабатывал
# никогда, либо срабатывал всегда. При этом сам запасной вариант шёл на 0.45–0.65
# («тоже подходит, просто хуже»), и такой макет раз в три слайда честнее, чем
# восемь одинаковых сеток подряд.
SWAP_FLOOR = 0.45


def _pick_varied(plan: draft.DraftPlan, index: int,
                 choice: SectionChoice) -> str:
    """Макет слайда с разведением подряд идущих повторов.

    Смотрим на уже выбранные макеты ПРЕДЫДУЩИХ слайдов плана (скоринг ленивый
    и последовательный — соседи уже размечены). Меняем ТОЛЬКО применяемый
    макет, не трогая уверенность и чипы: `_doubtful` считается по честному
    ответу модели, иначе разведение повторов начало бы плодить вопросы на
    ровном месте. Запасной берём не любой, а от SWAP_FLOOR: разнообразие не
    стоит макета, который разделу не подходит."""
    cands = choice.candidates
    if not cands:
        return ""
    top = cands[0].template_id
    run = 0
    for s in reversed(plan.slides[1:index - 1]):   # без обложки, до нас
        if s.template_id == top:
            run += 1
        else:
            break
    if run >= REPEAT_RUN - 1:
        alt = next((c for c in cands[1:]
                    if c.template_id != top and c.confidence >= SWAP_FLOOR),
                   None)
        if alt:
            return alt.template_id
    return top


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


def start_glass(session_id: str, source: Path, *,
                origin_name: str | None = None,
                offset: int = 0, title: str | None = None) -> draft.DraftPlan:
    """Документ → аутлайн-заготовка: обложка + слайд на раздел, БЕЗ модели.

    Старт мгновенный (parse-only): раньше здесь скорились все разделы в одном
    HTTP-запросе, и «сайт по факту лежал» до первого ответа. Теперь слайды
    уходят в status="unscored" с временным макетом от эвристики планировщика,
    а кандидаты/вопросы добирает ленивый score_next по ходу конвейера.

    ``offset`` — сколько подходящих разделов пропустить сверху: так вторая дека
    забирает хвост документа, не влезший в потолок (см. continue_glass).
    ``title`` — готовый титул для такой деки: имя файла на диске всегда
    `input.pptx`, и выводить его заново означало бы получить другую обложку."""
    from htmlslides.library import TemplateLibrary
    from htmlslides.parsers import parse_file
    from htmlslides.pipeline.planner import (_fallback_template, _has_content,
                                             _is_part_title, _section_to_text)

    library = TemplateLibrary.load()
    doc = parse_file(source)
    # Исходный номер раздела едет на слайде (src_index): по нему ленивый vision
    # берёт картинку исходного слайда — выпавший раздел иначе сдвинул бы ряд.
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

    slides = [draft.DraftSlide(template_id="cover",
                               content=_cover_content(title, library), filled=True)]
    for src_i, section in pairs:
        slides.append(draft.DraftSlide(
            template_id=_fallback_template(section, library),
            brief=_section_to_text(section),
            status="unscored",
            src_index=src_i,
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


def continue_glass(prev_session_id: str, session_id: str) -> draft.DraftPlan:
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
    out = start_glass(session_id, dest, offset=prev.rest_from, title=prev.title)
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
    """1-based индекс следующего слайда для заполнения.

    Пропускаем всё, что помечено статусом (unscored — ещё не размечен,
    needs_input — ждёт автора, failed — не крутим осечку по кругу за токены)
    и всё, что сейчас заполняет соседний шаг."""
    for i, s in enumerate(plan.slides, start=1):
        if s.brief and not s.filled and not s.status and i not in (busy or ()):
            return i
    return None


def _next_unscored(plan: draft.DraftPlan,
                   busy: set[int] | None = None) -> int | None:
    """1-based индекс следующего неразмеченного слайда (для score_next)."""
    for i, s in enumerate(plan.slides, start=1):
        if s.status == "unscored" and i not in (busy or ()):
            return i
    return None


def _done(plan: draft.DraftPlan, busy: set[int]) -> bool:
    """done — только когда ни работы, ни вопросов, ни занятых соседями слайдов.

    1-4 (аудит раунда 2, 2026-08-15): done считался по одному _next_index —
    True при открытом вопросе и при идущем параллельном шаге. Слайд с вопросом
    (needs_input) и неразмеченный (unscored) — тоже работа: у обоих brief без
    filled."""
    return (not busy
            and not any(s.brief and not s.filled for s in plan.slides))


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
        t0 = _now()
        try:
            sp = fill_slide(client, library, sp, deck_title=plan.title,
                            diagram_kind=kind)
        except Exception:  # noqa: BLE001 — быстрый сбой получает второй шанс
            # Быстрая осечка (обрыв, битый JSON) часто разовая — пробуем ещё
            # раз. Медленная — это таймаут: повтор удвоил бы шаг до 2×280с и
            # вылетел за клиентский предел ожидания, поэтому сразу заглушка.
            if _now() - t0 >= _RETRY_BUDGET_S:
                raise
            _LOG.warning("glass: fill слайда %s сорвался быстро — повтор",
                         index, exc_info=True)
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


def _result(plan: draft.DraftPlan, index: int | None, done: bool, *,
            action: str | None = None) -> dict:
    # action — что сделал этот вызов: "score" (разметил слайд), "fill"
    # (заполнил) или None (работы не нашлось). Клиент останавливает свой цикл
    # на None; open_questions/unscored питают индикацию панели.
    return {"done": done, "index": index, "action": action,
            "open_questions": _marked(plan, "needs_input"),
            "unscored": _marked(plan, "unscored"),
            "failed": _marked(plan, "failed"),
            "plan": plan.model_dump()}


def _score_one(session_id: str, plan: draft.DraftPlan, index: int,
               client: Any) -> draft.DraftPlan:
    """Разметить ОДИН слайд: кандидаты макета, сомнение → вопрос, слепой
    pptx-раздел → ленивый vision. Вызов модели идёт без замка; в свежий план
    вклеивается только свой слайд (splice-into-fresh, как _fill_one)."""
    from htmlslides.library import TemplateLibrary

    library = TemplateLibrary.load()
    s = plan.slides[index - 1]
    old_brief = s.brief
    brief = old_brief
    if _image_only(brief):
        text = _read_source_slide(session_id, s.src_index, client)
        if text:
            brief = f"{brief}\n\n{_IMAGE_READ_NOTE} {text}".strip()
    # Скорим по обогащённому брифу: раздел, прочитанный с картинки, для выбора
    # макета ничем не хуже раздела с текстом в XML.
    choice = _score_text(client, library, brief)
    if choice is None:
        # Сбой скоринга не маскируется под вопрос ИИ: честный текст про осечку.
        # Временный макет эвристики (start_glass) остаётся единственным чипом.
        choice = SectionChoice(
            candidates=[Candidate(template_id=s.template_id or "blank",
                                  confidence=0.0)],
            question=_FALLBACK_QUESTION)
    blind = _image_only(brief)               # после дочитки уже не слепой
    doubt = blind or _doubtful(choice)
    tid = _pick_varied(plan, index, choice)

    with _plan_lock(session_id):
        fresh = draft.load_plan(session_id)
        if not 1 <= index <= len(fresh.slides):
            return fresh                     # слайд удалили, пока мы работали
        slide = fresh.slides[index - 1]
        # Слайд уже не ждёт разметки (автор ответил/переделал) — не откатываем.
        if slide.status != "unscored" or slide.brief != old_brief:
            return fresh
        slide.brief = brief
        slide.template_id = tid or slide.template_id
        slide.status = "needs_input" if doubt else None
        slide.question = ((_IMAGE_ONLY_QUESTION if blind
                           else _question_for(choice)) if doubt else None)
        slide.candidates = [c.template_id for c in choice.candidates]
        draft.save_plan(session_id, fresh)
        draft_render.render_draft(session_id, fresh)
    return fresh


def score_next(session_id: str, *, client: Any | None = None) -> dict:
    """Разметить следующий неразмеченный слайд (клиентский «разведчик» зовёт
    его параллельно шагам заполнения). Нечего размечать → action=None."""
    lock = _plan_lock(session_id)
    with lock:
        plan = draft.load_plan(session_id)
        busy = _INFLIGHT.setdefault(session_id, set())
        index = _next_unscored(plan, busy)
        if index is None:
            return _result(plan, None, _done(plan, busy), action=None)
        busy.add(index)
    try:
        plan = _score_one(session_id, plan, index, client or _kimi())
    finally:
        with lock:
            _INFLIGHT.get(session_id, set()).discard(index)
    with lock:
        done = _done(plan, _INFLIGHT.get(session_id, set()))
    return _result(plan, index, done, action="score")


def step_fill(session_id: str, *, client: Any | None = None) -> dict:
    """Один шаг конвейера — ОДНО действие: заполнить готовый слайд, а если
    готовых нет — разметить следующий unscored. Вопрос (needs_input) сборку
    НЕ останавливает: слайд откладывается, ответ доводит его в любой момент.
    Возвращает action ("fill"|"score"|None) — клиент крутит цикл до None."""
    lock = _plan_lock(session_id)
    with lock:
        plan = draft.load_plan(session_id)
        busy = _INFLIGHT.setdefault(session_id, set())
        index = _next_index(plan, busy)
        action = "fill" if index is not None else None
        if index is None:
            index = _next_unscored(plan, busy)
            action = "score" if index is not None else None
        if index is None:
            # 1-4 (аудит раунда 2, 2026-08-15): пока сосед заполняет последний
            # слайд, «работы нет» ≠ «сборка завершена» — вторая вкладка видела
            # done и сворачивала процесс на середине заполнения.
            return _result(plan, None, _done(plan, busy), action=None)
        # Заявка на слайд: без неё второй шаг (вторая вкладка, F5 в середине
        # шага) брал тот же индекс и оплачивал вызов модели дважды.
        busy.add(index)
    try:
        if action == "fill":
            # Тип схемы, выбранный автором в ответе, доезжает до филлера данными.
            plan = _fill_one(session_id, plan, index, client or _kimi(),
                             kind=plan.slides[index - 1].diagram_kind or "")
        else:
            plan = _score_one(session_id, plan, index, client or _kimi())
    finally:
        with lock:
            _INFLIGHT.get(session_id, set()).discard(index)
    with lock:
        done = _done(plan, _INFLIGHT.get(session_id, set()))
    return _result(plan, index, done, action=action)


def answer(session_id: str, index: int, *, template_id: str | None = None,
           kind: str | None = None, message: str = "") -> dict:
    """Ответ на вопрос ИИ (в любой момент): чип-кандидат и/или уточнение текстом.

    Ответ МГНОВЕННЫЙ и модели не трогает: он лишь записывает выбор в план
    (макет, уточнение в brief, тип схемы) и снимает вопрос. Заполняет слайд
    обычный шаг конвейера (step_fill). Раньше ответ сам звал fill_slide и
    держал HTTP всю дорогу до модели: на деградировавшем провайдере это
    280-560с — клиент обрывал запрос на своём пределе ожидания, автор видел
    ложное «проверьте интернет», а поздний результат панель уже не подбирала
    (живой прогон 2026-08-20).

    Осечку последующего заполнения карточка переживает: _fill_one на сбое
    вернёт status="failed" с вопросом — отвечать снова будет на что.

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
            raise IndexError(
                f"слайд {index} вне диапазона (1..{len(plan.slides)})")
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
        # Вопрос гасим сразу: ответ уже ничего не заполняет, ронять здесь
        # нечему. Сбой на заполнении шага вернёт свежий вопрос через failed.
        slide.diagram_kind = kind or None
        slide.status = None
        slide.question = None
        slide.candidates = None
        slide.filled = False                      # шаг заполнит с учётом ответа
        draft.save_plan(session_id, plan)
        draft_render.render_draft(session_id, plan)
        done = _done(plan, _INFLIGHT.get(session_id, set()))
    return _result(plan, index, done, action=None)


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
            raise IndexError(
                f"слайд {index} вне диапазона (1..{len(plan.slides)})")
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
    return _result(plan, index, _done(plan, _INFLIGHT.get(session_id, set())))


class NotReady(RuntimeError):
    """Точечное улучшение доступно только после полного завершения сборки."""


def _improve_notes(plan_one: Any, html: str, *, vision: bool,
                   client: Any, theme: str) -> list[str]:
    """Замечания QA по одному слайду (линт + замер + vision). Вынесено для
    подмены в тестах: полный QA требует Playwright."""
    from htmlslides.pipeline.build import _qa_notes
    notes = _qa_notes(plan_one, html, vision=vision, vision_all=True,
                      client=client, theme=theme, artifacts=None,
                      progress=lambda *_: None)
    return notes.get(1, [])


def improve_slide(session_id: str, index: int, *, vision: bool = True,
                  client: Any | None = None) -> dict:
    """«Улучшить этот слайд»: QA одного слайда и один автофикс — точечная
    замена общедековой кнопки rebuild (спека 2026-08-20). Доступно только
    после полного заполнения аутлайна: до того слайд может перезаполнить сам
    конвейер, и автофикс проиграл бы гонку."""
    from htmlslides.library import TemplateLibrary
    from htmlslides.models import DeckPlan, SlidePlan
    from htmlslides.pipeline.filler import autofix_slide
    from webapp import deck_edit

    with _plan_lock(session_id):
        plan = draft.load_plan(session_id)
        if any(s.brief and not s.filled for s in plan.slides):
            raise NotReady("сборка ещё идёт — улучшение доступно после "
                           "её завершения")
        if not 1 <= index <= len(plan.slides):
            raise IndexError(
                f"слайд {index} вне диапазона (1..{len(plan.slides)})")
        slide = plan.slides[index - 1]
        if slide.freeform:
            raise NoContext("свободный слайд правится в чате — "
                            "автофикс работает по макету")
        old_brief = slide.brief

    library = TemplateLibrary.load()
    deck_html = deck_edit.deck_path(session_id).read_text("utf-8")
    single = deck_edit.extract_slide(deck_html, index)
    if single is None:
        raise IndexError(f"слайда {index} нет в отрендеренной деке")
    tid = slide.template_id or "blank"
    content = dict(slide.fields or slide.content or {})
    sp = SlidePlan(index=1, type=library.get(tid).type, template_id=tid,
                   content=content)
    fixes = _improve_notes(DeckPlan(title=plan.title, slides=[sp]), single,
                           vision=vision, client=client or _kimi(),
                           theme=plan.theme or "dark")
    if not fixes:
        return {"improved": False, "notes": 0, "plan": plan.model_dump()}
    sp = autofix_slide(client or _kimi(), library, sp, fixes,
                       deck_title=plan.title)
    # Вызов модели шёл без замка — вклеиваем только свой слайд в свежий план
    # (splice-into-fresh, как _fill_one).
    with _plan_lock(session_id):
        fresh = draft.load_plan(session_id)
        if (not 1 <= index <= len(fresh.slides)
                or fresh.slides[index - 1].brief != old_brief):
            return {"improved": False, "notes": len(fixes),
                    "plan": fresh.model_dump()}
        fresh = draft.update_slide(fresh, index, content=sp.content,
                                   template_id=sp.template_id or tid)
        s = fresh.slides[index - 1]
        typed = slide_types.typed_from_content(sp.template_id or tid, sp.content)
        s.slide_type, s.fields = typed if typed else (None, None)
        s.filled = True
        draft.save_plan(session_id, fresh)
        draft_render.render_draft(session_id, fresh)
    return {"improved": True, "notes": len(fixes), "plan": fresh.model_dump()}
