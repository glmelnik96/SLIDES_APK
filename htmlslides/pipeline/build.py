"""Оркестратор пайплайна: parse -> plan -> fill -> assemble -> lint -> QA -> autofix."""
from __future__ import annotations

import json
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

from ..assembler import assemble
from ..library import TemplateLibrary
from ..models import DeckPlan, SlidePlan
from ..parsers import parse_file
from .client import TRANSIENT_API_ERRORS, KimiClient, LLMFormatError
from .filler import FillError, autofix_slide, fill_deck
from .linter import lint_html
from .planner import plan_deck

Progress = Callable[[str], None]

# Хвостовые фазы (vision-QA, autofix) — по одному LLM-вызову на слайд. Пока они
# шли последовательно, крупная дека выедала десятки минут и упиралась в watchdog
# сборки. Держим ту же ширину пула, что и plan/fill (RPS ограничивает клиент).
QA_WORKERS = 8

# Потолок слайдов в деке. Документ на 150+ разделов раскладывался в план на
# сотни слайдов: даже параллельно это не влезало в watchdog, и пользователь
# терял 40 минут ради «сборка превысила лимит времени». Лучше честно собрать
# первые MAX_DECK_SLIDES и сказать, что документ надо разбить на части.
#
# Значение упирается в бюджет сборки, а не в вёрстку: все фазы стоят по LLM-вызову
# на слайд, то есть время растёт по слайдам примерно линейно. 60 выбирались, когда
# они съедали 18,4 мин из 40; после `_cap_sections` те же 60 стоят 11,4 мин, и
# потолок стал резать документы, которые в бюджет уже влезали. Мерять при каждом
# изменении: ориентир — уложиться в мягкий дедлайн (0.75 × 2400 с = 30 мин), иначе
# дека доедет без визуальной вычитки.
MAX_DECK_SLIDES = 100


def build_deck(input_path: str | Path, out_path: str | Path, *,
               mode: str = "auto", vision: bool = True, freeform_ok: bool = False,
               theme: str = "dark", max_slides: int = MAX_DECK_SLIDES,
               max_autofix: int = 1, keep_artifacts: str | Path | None = None,
               client: Optional[KimiClient] = None,
               progress: Progress = lambda message: None,
               check_cancel: Callable[[], None] = lambda: None,
               wrapup: Callable[[], bool] = lambda: False) -> Path:
    """Собрать деку из md/docx/pptx. Возвращает путь к готовому .html.

    Исключения (контракт для вызывающего кода, например бота /htmlnew):
      ValueError — mode=rebrand для не-pptx; неподдерживаемый формат входа;
          freeform-слайд в плане при freeform_ok=False (из планировщика).
      LLMFormatError — планировщик не вернул валидный JSON после ретрая.
      FillError — заполнение слайда не удалось после ретрая (основная фаза
          fill; сбои autofix НЕ роняют билд — только warn в progress).
      AssembleError — неизвестная theme (а также нарушения слот-контрактов после autofix).
      RenderUnavailable / subprocess.CalledProcessError /
          subprocess.TimeoutExpired — рендер исходных слайдов; только при
          mode=rebrand (в mode=auto деградирует до текстового пайплайна).
      RuntimeError — нет CLOUDRU_API_KEY (из KimiClient, если client не задан).
      ProviderUnavailable — не отвечают ни основная модель, ни резервная (проба
          в начале сборки), либо из-за сбоев API деградировала большая часть
          разделов/слайдов: отдавать деку из заглушек как успех нельзя.
      openai.* (APIError и наследники) — сеть/авторизация при вызовах LLM.
    """
    src, out = Path(input_path), Path(out_path)
    if mode == "exact":
        return _build_exact(src, out, theme=theme, progress=progress)
    if mode == "rebrand" and src.suffix.lower() != ".pptx":
        raise ValueError("mode=rebrand доступен только для .pptx")
    client = client or KimiClient()
    # Проба ДО парса и рендера: если модель молчит, отказ должен стоить полминуты,
    # а не сорока минут пофазных таймаутов (инцидент 2026-08-04). Заодно это
    # единственное место, где клиент узнаёт, куда сообщать о подмене модели.
    client.preflight(progress)
    library = TemplateLibrary.load()
    artifacts = Path(keep_artifacts) if keep_artifacts else None
    if artifacts:
        artifacts.mkdir(parents=True, exist_ok=True)

    progress(f"parse: {src.name}")
    doc = parse_file(src)
    slide_images = _render_source_slides(src, mode, artifacts, progress)

    doc, slide_images, capped = _cap_sections(
        doc, slide_images, max_slides=max_slides, progress=progress)

    progress(f"plan: планирую деку ({client.model})")
    plan = plan_deck(client, doc, library,
                     slide_images=slide_images, freeform_ok=freeform_ok)
    plan = _normalize_indices(plan)
    # Пустой план = валидный JSON {"slides": []} от модели (бывает под нагрузкой /
    # лимитом Cloud.ru). Без этой проверки assemble отдаёт деку-пустышку, и билд
    # завершается «успехом» с пустым .html. Падаем явно, чтобы вызывающий код
    # эмитнул FAILED с понятной причиной, а не молчаливый пустой результат.
    if not plan.slides:
        raise LLMFormatError(
            "планировщик вернул пустой план (0 слайдов) — повторите запуск; "
            "возможно, превышен лимит запросов Cloud.ru")
    plan = _cap_slides(plan, max_slides=max_slides, progress=progress,
                       announce=not capped)
    _dump(artifacts, "deckplan.json", plan.model_dump_json(indent=2))

    progress(f"fill: заполняю {len(plan.slides)} слайдов (параллельно)")
    plan = fill_deck(client, library, plan, progress=progress,
                     check_cancel=check_cancel)
    _dump(artifacts, "deckfill.json", plan.model_dump_json(indent=2))

    return polish_plan(plan, out, client=client, library=library, vision=vision,
                       theme=theme, max_autofix=max_autofix, artifacts=artifacts,
                       progress=progress, check_cancel=check_cancel,
                       wrapup=wrapup)


def _build_exact(src: Path, out: Path, *, theme: str,
                 progress: Progress) -> Path:
    """Точный перенос: 1 слайд источника → 1 слайд, текст дословно, без ИИ.

    pptx читаем существующим parse_pptx (дословный текст + картинки, 1 секция на
    слайд); .md/.txt режем по меткам «Слайд N:». docx на Этапе 1 не поддержан.
    """
    from ..parsers import parse_file
    from ..parsers.base import InputDoc, read_text_smart
    from ..parsers.exact_text import split_exact_text
    from .exact_builder import build_exact_plan

    progress(f"parse: {src.name}")
    suffix = src.suffix.lower()
    if suffix == ".pptx":
        doc = parse_file(src)
    elif suffix in (".md", ".txt"):
        raw = read_text_smart(src)
        doc = InputDoc(title=src.stem,
                       sections=split_exact_text(raw, progress=progress))
    else:
        raise ValueError(
            "точный перенос на Этапе 1 поддерживает .pptx/.md/.txt, "
            f"не {suffix or 'без расширения'}")
    if not doc.sections:
        raise LLMFormatError(
            "источник пуст (0 слайдов) — точную деку не собираем")

    progress(f"plan: {len(doc.sections)} слайдов 1-в-1")
    plan, warnings = build_exact_plan(doc)
    for w in warnings:
        progress(f"warn: {w}")

    # Этап 2 (ИИ-вёрстка) отключён намеренно: вёрстка теперь детерминированная
    # (build_exact_plan → flow-раскладки), ноль ИИ-вызовов — нет таймаутов и
    # заглушек. exact_designer.py остаётся в репо, но из exact-пути не зовётся.

    # polish без vision/autofix: только assemble.
    return polish_plan(plan, out, theme=theme, vision=False, max_autofix=0,
                       progress=progress)


def polish_plan(plan: DeckPlan, out_path: str | Path, *,
                client: Optional[KimiClient] = None,
                library: Optional[TemplateLibrary] = None,
                vision: bool = True, vision_all: bool = False,
                theme: str = "dark", max_autofix: int = 1,
                workers: int = QA_WORKERS,
                artifacts: Optional[Path] = None,
                progress: Progress = lambda message: None,
                check_cancel: Callable[[], None] = lambda: None,
                wrapup: Callable[[], bool] = lambda: False) -> Path:
    """Assemble + QA + autofix a fully-filled DeckPlan and write the deck.

    The post-fill tail of build_deck, exposed so an already-authored plan (e.g. a
    manual/chat-built draft) can be 'rebuilt through the engine' — the same lint +
    vision-QA + one autofix round — without re-planning or re-filling from a doc.

    vision_all=True forces vision-QA over EVERY slide (not just lint-flagged or
    freeform ones) — used by the manual/chat 'rebuild through the engine' button so
    it actually reviews each slide rather than skipping a lint-clean deck.

    wrapup() — мягкий дедлайн раннера: «бюджет сборки почти исчерпан». Хвост
    (vision-QA + автоправки) стоит по LLM-вызову на слайд и при этом только
    косметика поверх уже заполненной деки. Упереться в watchdog значит отдать
    пользователю НИЧЕГО, поэтому по этому сигналу хвост пропускается, а дека
    всё равно доводится до файла."""
    out = Path(out_path)
    # Client is only needed for vision-QA and autofix; build it lazily so a pure
    # assemble (vision=False, max_autofix=0) needs no CLOUDRU_API_KEY.
    if client is None and (vision or max_autofix > 0):
        client = KimiClient()
    library = library or TemplateLibrary.load()
    plan = _normalize_indices(plan)

    progress("assemble: собираю HTML")
    html = assemble(plan, theme=theme)

    progress("lint: статические проверки")
    # Проверяем дважды, а не один раз на входе: сама vision-QA идёт минутами и
    # вполне может исчерпать бюджет уже после первой проверки.
    skip_vision = vision and wrapup()
    if skip_vision:
        progress("limit: времени на сборку почти не осталось — пропускаю "
                 "визуальную вычитку, отдаю деку как есть.")
    notes = _qa_notes(plan, html, vision=vision and not skip_vision,
                      vision_all=vision_all,
                      client=client, theme=theme, workers=workers,
                      artifacts=artifacts, progress=progress)

    if notes and max_autofix > 0 and wrapup():
        progress("limit: времени на сборку почти не осталось — пропускаю круг "
                 "автоправок, отдаю деку как есть.")
        max_autofix = 0

    if notes and max_autofix > 0:
        # Вариант A: не начинаем круг автоправок (это ещё LLM-вызовы на слайд),
        # если сборку уже попросили остановить.
        check_cancel()
        progress(f"autofix: исправляю слайды {sorted(notes)} (1 круг)")
        by_index = {s.index: s for s in plan.slides}
        pending = []
        for index, slide_notes in notes.items():
            if index not in by_index:                 # индекс notes вне плана
                progress(f"warn: замечания к несуществующему слайду {index}: "
                         + "; ".join(slide_notes))
                continue
            pending.append((index, slide_notes))

        def _fix(item: tuple[int, list[str]]) -> tuple[int, Optional[SlidePlan]]:
            """Автоправка одного слайда в воркере; None = оставить как было."""
            index, slide_notes = item
            try:
                return index, autofix_slide(client, library, by_index[index],
                                            slide_notes, deck_title=plan.title)
            except (FillError, LLMFormatError) as exc:
                progress(f"warn: autofix слайда {index} не удался: {exc}")
                return index, None
            except TRANSIENT_API_ERRORS as exc:
                # 503/лимит/таймаут шлюза на ОДНОМ слайде не должен обнулять уже
                # заполненную деку: к autofix мы приходим после plan+fill, то есть
                # после десятков минут работы и оплаченных токенов. Худшее, что
                # теряется, — одна косметическая правка (прод-сбой 2026-07-28 18:37,
                # где 503 выбросился наружу и убил сборку целиком).
                progress(f"warn: autofix слайда {index} — сбой API "
                         f"({type(exc).__name__}); оставляю слайд как есть")
                return index, None

        # Параллельно по той же причине, что и vision-QA: круг автоправок — это
        # ещё по LLM-вызову на каждый отмеченный слайд. by_index читаем из
        # воркеров, пишем только здесь.
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for index, fixed in pool.map(_fix, pending):
                if fixed is not None:
                    by_index[index] = fixed
        plan = plan.model_copy(
            update={"slides": [by_index[k] for k in sorted(by_index)]})
        html = assemble(plan, theme=theme)
        for issue in lint_html(html):
            progress(f"warn: slide {issue.slide_index} "
                     f"{issue.code} {issue.detail}".rstrip())
    else:
        for index, slide_notes in notes.items():
            progress(f"warn: slide {index}: " + "; ".join(slide_notes))

    out.write_text(html, encoding="utf-8")
    progress(f"done: {out}")
    return out


def _normalize_indices(plan: DeckPlan) -> DeckPlan:
    """Индексы LLM-планировщика (возможны дыры/дубли) -> позиции 1..N.

    После нормализации SlidePlan.index везде совпадает с 1-based позицией
    <section> в собранном HTML (линтер/замер bbox адресуют именно позиции).
    """
    ordered = sorted(plan.slides, key=lambda s: s.index)   # stable sort
    return plan.model_copy(update={"slides": [
        s.model_copy(update={"index": i})
        for i, s in enumerate(ordered, start=1)]})


# Префикс `limit:` (а не `warn:`) — намеренно: UI глушит внутренние warn, а про
# урезанную деку пользователь обязан узнать. Сообщения с этим префиксом уходят в
# живой лог сборки дословно, поэтому пишем их на языке пользователя.
_CAP_NOTICE = ("limit: документ большой — собираю первые {n} слайдов. Чтобы "
               "перенести документ целиком, разбейте его на части и соберите "
               "по отдельности.")


def _cap_sections(doc, slide_images, *, max_slides: int,
                  progress: Progress):
    """Отбросить разделы, слайды которых всё равно срежет потолок.

    Планирование стоит LLM-вызова на РАЗДЕЛ, и на крупном документе это половина
    всей сборки (замер 2026-07-28: фаза plan — 544 с из 1102 с). Планировать
    полторы сотни разделов, чтобы затем выбросить всё после 60-го слайда, —
    чистая потеря и времени, и денег.

    Каждый раздел даёт 1-2 слайда (`_plan_section`, фолбэк тоже даёт один),
    поэтому первых max_slides разделов заведомо хватает, чтобы набрать
    max_slides слайдов. Возвращает (doc, slide_images, обрезали ли).

    slide_images режем тем же срезом: в pptx-rebrand скриншоты идут 1-в-1 с
    разделами, и обрезка одних лишь разделов сдвинула бы весь визуальный ряд.
    """
    if max_slides <= 0 or len(doc.sections) <= max_slides:
        return doc, slide_images, False
    progress(_CAP_NOTICE.format(n=max_slides))
    return (doc.model_copy(update={"sections": doc.sections[:max_slides]}),
            list(slide_images)[:max_slides], True)


def _cap_slides(plan: DeckPlan, *, max_slides: int, progress: Progress,
                announce: bool = True) -> DeckPlan:
    """Обрезать план до max_slides, честно сказав об этом в прогресс.

    Без потолка документ на сотню разделов раскладывался в план на сотни слайдов,
    сборка не влезала в watchdog и пользователь получал таймаут вместо результата —
    хуже, чем усечённая, но готовая дека с явным предупреждением.

    announce=False, когда про потолок уже сказал `_cap_sections`: одно урезание —
    одно сообщение, иначе пользователь видит дубль."""
    if max_slides <= 0 or len(plan.slides) <= max_slides:
        return plan
    if announce:
        progress(_CAP_NOTICE.format(n=max_slides))
    return _normalize_indices(
        plan.model_copy(update={"slides": plan.slides[:max_slides]}))


def _qa_notes(plan: DeckPlan, html: str, *, vision: bool, client: KimiClient,
              theme: str = "dark", artifacts: Optional[Path],
              progress: Progress, vision_all: bool = False,
              workers: int = QA_WORKERS) -> dict[int, list[str]]:
    """slide_index -> замечания (линтер + замер bbox + vision-QA).

    vision_all=True ревьюит КАЖДЫЙ слайд (а не только flagged/freeform)."""
    notes: dict[int, list[str]] = {}
    for issue in lint_html(html):
        notes.setdefault(issue.slide_index, []).append(
            f"{issue.code}: {issue.detail}".rstrip())
    from .linter import validate_freeform
    import re as _re
    _slides = _re.findall(
        r'<section[^>]*data-template="freeform"[^>]*>.*?</section>',
        html, _re.DOTALL)
    # сопоставляем по порядку freeform-секций их 1-based индексам в плане
    _ff_idx = [s.index for s in plan.slides if s.freeform]
    for pos, frag in enumerate(_slides):
        if pos < len(_ff_idx):
            for issue in validate_freeform(frag, _ff_idx[pos]):
                notes.setdefault(issue.slide_index, []).append(
                    f"{issue.code}: {issue.detail}".rstrip())
    if not vision:
        return notes
    freeform = {s.index for s in plan.slides if s.freeform}
    all_idx = {s.index for s in plan.slides} if vision_all else set()
    if not (notes or freeform or all_idx):
        return notes
    from .screenshot import QAUnavailable, measure_overflow, screenshot_slides
    from .vision_qa import review_slide
    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_html = Path(tmp) / "deck.html"
            tmp_html.write_text(html, encoding="utf-8")
            for issue in measure_overflow(tmp_html):
                notes.setdefault(issue.slide_index, []).append(
                    f"{issue.code}: {issue.detail}".rstrip())
            targets = sorted(set(notes) | freeform | all_idx)
            shots = screenshot_slides(tmp_html, targets, artifacts or Path(tmp))
            briefs = {s.index: json.dumps(s.content, ensure_ascii=False)[:2000]
                      for s in plan.slides}
            reviewable = []
            for index in targets:
                if index not in briefs or index not in shots:  # target вне плана
                    progress(f"warn: vision-qa пропущен для слайда {index}: "
                             "нет брифа или скриншота")
                    continue
                reviewable.append(index)

            def _review(index: int) -> tuple[int, list[str]]:
                """Ревью одного слайда в воркере: наружу — только данные."""
                try:
                    verdict = review_slide(client, shots[index],
                                           brief=briefs[index], theme=theme)
                except LLMFormatError as exc:
                    progress(f"warn: vision-qa слайда {index} не удался: {exc}")
                    return index, []
                except TRANSIENT_API_ERRORS as exc:
                    # Тот же контракт, что и в fill_deck: транзиентный сбой шлюза
                    # стоит ревью одного слайда, а не всей сборки.
                    progress(f"warn: vision-qa слайда {index} — сбой API "
                             f"({type(exc).__name__}); слайд без ревью")
                    return index, []
                return index, [] if verdict.passed else list(verdict.fixes)

            # Параллельно: последовательный обход стоил одного vision-вызова на
            # слайд подряд и на крупной деке съедал watchdog целиком. Результаты
            # разбираем в главном потоке (pool.map сохраняет порядок) — notes
            # мутирует один поток, лок не нужен.
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for index, fixes in pool.map(_review, reviewable):
                    progress(f"vision-qa: слайд {index}")
                    if fixes:
                        notes.setdefault(index, []).extend(fixes)
    except QAUnavailable as exc:
        progress(f"vision-qa пропущен: {exc}")
    return notes


def _render_source_slides(src: Path, mode: str, artifacts: Optional[Path],
                          progress: Progress) -> list[Path]:
    """Для pptx — PNG исходных слайдов (vision-планировщик, режим rebrand)."""
    if src.suffix.lower() != ".pptx":
        return []
    from ..parsers.render import RenderUnavailable, render_pptx_pngs
    base = artifacts or Path(tempfile.mkdtemp(prefix="htmlslides-"))
    try:
        images = render_pptx_pngs(src, base / "source-slides")
        progress(f"rebrand: {len(images)} скриншотов исходных слайдов")
        return images
    except RenderUnavailable as exc:
        # Нет LibreOffice/pypdfium — «зрение» на этой машине невозможно в принципе.
        # Деградируем на текст ВЕЗДЕ, включая rebrand: иначе любую pptx нельзя
        # собрать без установки LibreOffice (планировщик просто отработает по тексту).
        progress(f"rebrand-скриншоты недоступны ({exc}), работаю по тексту")
        return []
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        # LibreOffice есть, но споткнулся на этом файле (битый/завис). В явном
        # rebrand это проблема файла, а не машины — не прячем; в auto деградируем.
        if mode == "rebrand":
            raise
        progress(f"rebrand-скриншоты недоступны ({exc}), работаю по тексту")
        return []


def _dump(artifacts: Optional[Path], name: str, text: str) -> None:
    if artifacts:
        (artifacts / name).write_text(text, encoding="utf-8")
