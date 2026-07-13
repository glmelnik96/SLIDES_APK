"""Оркестратор пайплайна: parse -> plan -> fill -> assemble -> lint -> QA -> autofix."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from ..assembler import assemble
from ..library import TemplateLibrary
from ..models import DeckPlan
from ..parsers import parse_file
from .client import KimiClient, LLMFormatError
from .filler import FillError, autofix_slide, fill_deck
from .linter import lint_html
from .planner import plan_deck

Progress = Callable[[str], None]


def build_deck(input_path: str | Path, out_path: str | Path, *,
               mode: str = "auto", vision: bool = True, freeform_ok: bool = False,
               theme: str = "dark",
               max_autofix: int = 1, keep_artifacts: str | Path | None = None,
               client: Optional[KimiClient] = None,
               progress: Progress = lambda message: None) -> Path:
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
      openai.* (APIError и наследники) — сеть/авторизация при вызовах LLM.
    """
    src, out = Path(input_path), Path(out_path)
    if mode == "exact":
        return _build_exact(src, out, theme=theme, progress=progress)
    if mode == "rebrand" and src.suffix.lower() != ".pptx":
        raise ValueError("mode=rebrand доступен только для .pptx")
    client = client or KimiClient()
    library = TemplateLibrary.load()
    artifacts = Path(keep_artifacts) if keep_artifacts else None
    if artifacts:
        artifacts.mkdir(parents=True, exist_ok=True)

    progress(f"parse: {src.name}")
    doc = parse_file(src)
    slide_images = _render_source_slides(src, mode, artifacts, progress)

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
    _dump(artifacts, "deckplan.json", plan.model_dump_json(indent=2))

    progress(f"fill: заполняю {len(plan.slides)} слайдов (параллельно)")
    plan = fill_deck(client, library, plan, progress=progress)
    _dump(artifacts, "deckfill.json", plan.model_dump_json(indent=2))

    return polish_plan(plan, out, client=client, library=library, vision=vision,
                       theme=theme, max_autofix=max_autofix, artifacts=artifacts,
                       progress=progress)


def _build_exact(src: Path, out: Path, *, theme: str,
                 progress: Progress) -> Path:
    """Точный перенос: 1 слайд источника → 1 слайд, текст дословно, без ИИ.

    pptx читаем существующим parse_pptx (дословный текст + картинки, 1 секция на
    слайд); .md/.txt режем по меткам «Слайд N:». docx на Этапе 1 не поддержан.
    """
    from ..parsers import parse_file
    from ..parsers.base import InputDoc
    from ..parsers.exact_text import split_exact_text
    from .exact_builder import build_exact_plan

    progress(f"parse: {src.name}")
    suffix = src.suffix.lower()
    if suffix == ".pptx":
        doc = parse_file(src)
    elif suffix in (".md", ".txt"):
        raw = src.read_text(encoding="utf-8")
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

    # Этап 2: ИИ-вёрстка каждого слайда по протоколу меток. Нет ключа → пропускаем,
    # остаётся детерминированный html Этапа 1 (дека собирается всегда).
    from .exact_designer import design_exact_deck
    client = _exact_client_or_none(progress)
    if client is not None:
        progress("design: ИИ-вёрстка exact-слайдов")
        plan = design_exact_deck(client, doc, plan, progress=progress)

    # polish без vision/autofix: только assemble.
    return polish_plan(plan, out, theme=theme, vision=False, max_autofix=0,
                       progress=progress)


def _exact_client_or_none(progress: Progress) -> Optional[KimiClient]:
    """Клиент для ИИ-вёрстки exact-слайдов; нет ключа → None (собираем как Этап 1)."""
    try:
        return KimiClient()
    except RuntimeError as exc:
        progress(f"warn: нет ключа к ИИ ({exc}); точный перенос без дизайна (как Этап 1)")
        return None


def polish_plan(plan: DeckPlan, out_path: str | Path, *,
                client: Optional[KimiClient] = None,
                library: Optional[TemplateLibrary] = None,
                vision: bool = True, vision_all: bool = False,
                theme: str = "dark", max_autofix: int = 1,
                artifacts: Optional[Path] = None,
                progress: Progress = lambda message: None) -> Path:
    """Assemble + QA + autofix a fully-filled DeckPlan and write the deck.

    The post-fill tail of build_deck, exposed so an already-authored plan (e.g. a
    manual/chat-built draft) can be 'rebuilt through the engine' — the same lint +
    vision-QA + one autofix round — without re-planning or re-filling from a doc.

    vision_all=True forces vision-QA over EVERY slide (not just lint-flagged or
    freeform ones) — used by the manual/chat 'rebuild through the engine' button so
    it actually reviews each slide rather than skipping a lint-clean deck."""
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
    notes = _qa_notes(plan, html, vision=vision, vision_all=vision_all,
                      client=client, theme=theme,
                      artifacts=artifacts, progress=progress)

    if notes and max_autofix > 0:
        progress(f"autofix: исправляю слайды {sorted(notes)} (1 круг)")
        by_index = {s.index: s for s in plan.slides}
        for index, slide_notes in notes.items():
            slide = by_index.get(index)
            if slide is None:                         # индекс notes вне плана
                progress(f"warn: замечания к несуществующему слайду {index}: "
                         + "; ".join(slide_notes))
                continue
            try:
                by_index[index] = autofix_slide(client, library, slide,
                                                slide_notes, deck_title=plan.title)
            except (FillError, LLMFormatError) as exc:
                progress(f"warn: autofix слайда {index} не удался: {exc}")
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


def _qa_notes(plan: DeckPlan, html: str, *, vision: bool, client: KimiClient,
              theme: str = "dark", artifacts: Optional[Path],
              progress: Progress, vision_all: bool = False) -> dict[int, list[str]]:
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
            for index in targets:
                if index not in briefs or index not in shots:  # target вне плана
                    progress(f"warn: vision-qa пропущен для слайда {index}: "
                             "нет брифа или скриншота")
                    continue
                progress(f"vision-qa: слайд {index}")
                try:
                    verdict = review_slide(client, shots[index],
                                           brief=briefs[index], theme=theme)
                except LLMFormatError as exc:
                    progress(f"warn: vision-qa слайда {index} не удался: {exc}")
                    continue
                if not verdict.passed:
                    notes.setdefault(index, []).extend(verdict.fixes)
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
