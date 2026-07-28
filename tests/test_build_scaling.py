"""Масштабирование хвоста пайплайна (разбор прод-таймаутов 2026-07-28).

Три сборки подряд упали в watchdog (40 мин) на крупных документах: план на
десятки-сотни слайдов, а vision-QA и autofix шли строго последовательно — по
LLM-вызову на слайд. Покрываем лечение:
- vision-QA и autofix идут пулом (реально параллельно), семантика не изменилась;
- сбой одного слайда не роняет круг и не теряет остальные;
- план обрезается до потолка с честным предупреждением в прогресс;
- таймаут сборки объясняет причину, а не советует «повторите запуск»;
- уровни логов доезжают до journald префиксом <N>.

Плюс второй прод-сбой того же дня: 503 от шлюза Cloud.ru прилетел в autofix и
выбросил `openai.InternalServerError` наружу — 34 минуты сборки (и оплаченные
токены) сгорели, хотя дека была уже собрана. `fill_deck` и планировщик такие
сбои давно гасят пер-слайдово, хвост — нет. Покрываем симметрию.
"""
import logging
import os
import threading

import httpx
import pytest
from openai import InternalServerError

os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

import htmlslides.pipeline.screenshot as shotmod
import htmlslides.pipeline.vision_qa as visionmod
from htmlslides.models import DeckPlan, SlidePlan
from htmlslides.pipeline import build
from htmlslides.pipeline.client import LLMFormatError
from webapp.logging_setup import _PriorityFormatter


def _api_503() -> InternalServerError:
    """Ровно то, что прилетело с прод-шлюза 2026-07-28 18:37."""
    req = httpx.Request("POST", "http://x/chat/completions")
    return InternalServerError(
        "upstream connect error or disconnect/reset before headers",
        response=httpx.Response(503, request=req), body=None)


def _slide(index: int, *, freeform: bool = False) -> SlidePlan:
    return SlidePlan(index=index, type="content", template_id="freeform",
                     freeform=freeform, content={"brief": f"s{index}"})


def _plan(n: int, *, freeform: bool = False) -> DeckPlan:
    return DeckPlan(title="D",
                    slides=[_slide(i, freeform=freeform) for i in range(1, n + 1)])


class _Tracker:
    """Считает пиковую одновременность вызовов — так видно, что пул реально работает."""

    def __init__(self):
        self.lock = threading.Lock()
        self.now = 0
        self.peak = 0
        self.seen = []

    def enter(self, index):
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
            self.seen.append(index)

    def leave(self):
        with self.lock:
            self.now -= 1


# ── _cap_slides ──────────────────────────────────────────────────────────────
def test_cap_slides_truncates_and_warns():
    msgs = []
    out = build._cap_slides(_plan(10), max_slides=4, progress=msgs.append)
    assert len(out.slides) == 4
    assert [s.index for s in out.slides] == [1, 2, 3, 4]   # индексы перенумерованы
    # префикс limit:, а не warn: — иначе UI проглотит сообщение молча
    assert len(msgs) == 1 and msgs[0].startswith("limit:")
    assert "разбейте его на части" in msgs[0]


def test_cap_notice_reaches_ui_and_keeps_stage():
    """Контракт с фронтом: `limit:` не двигает стадию, но показывается дословно."""
    from pathlib import Path

    from worker.tasks.htmlnew import map_progress

    msgs = []
    build._cap_slides(_plan(10), max_slides=4, progress=msgs.append)
    assert map_progress(msgs[0]) is None      # стадия не сбивается на «warn»

    app_js = Path(__file__).resolve().parents[1] / "webapp" / "static" / "app.js"
    src = app_js.read_text(encoding="utf-8")
    # без этой ветки friendlyDetail вернёт "" и урезание деки пройдёт молча
    assert 'detail.startsWith("limit:")' in src


def test_cap_slides_noop_under_limit():
    msgs = []
    plan = _plan(3)
    assert build._cap_slides(plan, max_slides=60, progress=msgs.append) is plan
    assert msgs == []


def test_cap_slides_disabled_by_zero():
    plan = _plan(5)
    assert build._cap_slides(plan, max_slides=0, progress=lambda m: None) is plan


# ── _cap_sections: не планировать то, что потолок всё равно выбросит ─────────
def _doc(n: int):
    from htmlslides.parsers.base import InputDoc, Section
    return InputDoc(title="D", sections=[Section(heading=f"h{i}", level=2,
                                                 blocks=[]) for i in range(n)])


def test_cap_sections_truncates_before_planning_and_warns():
    """Планирование стоит LLM-вызова на РАЗДЕЛ и на замере 2026-07-28 съедало
    49% сборки. Разделы за потолком планировать бессмысленно — их слайды
    выбросит _cap_slides."""
    msgs = []
    doc, images, cut = build._cap_sections(_doc(150), [], max_slides=60,
                                           progress=msgs.append)
    assert len(doc.sections) == 60      # каждый раздел даёт ≥1 слайд → 60 хватает
    assert cut is True
    assert len(msgs) == 1 and msgs[0].startswith("limit:")


def test_cap_sections_keeps_images_aligned():
    """pptx-rebrand: скриншоты идут 1-в-1 с разделами. Обрезать разделы, не
    обрезав картинки, значит сдвинуть планировщику весь визуальный ряд."""
    doc, images, cut = build._cap_sections(
        _doc(10), [f"s{i}.png" for i in range(10)], max_slides=4,
        progress=lambda m: None)
    assert len(doc.sections) == 4 and images == ["s0.png", "s1.png", "s2.png",
                                                 "s3.png"]


def test_cap_sections_noop_under_limit():
    msgs = []
    doc = _doc(5)
    out, images, cut = build._cap_sections(doc, [], max_slides=60,
                                           progress=msgs.append)
    assert out is doc and cut is False and msgs == []


def test_cap_slides_stays_silent_when_sections_already_announced():
    """Одно урезание — одно сообщение. Разделы обрезаны → про потолок уже
    сказано, второе сообщение от _cap_slides было бы дублем."""
    msgs = []
    out = build._cap_slides(_plan(10), max_slides=4, progress=msgs.append,
                            announce=False)
    assert len(out.slides) == 4 and msgs == []


# ── vision-QA пулом ──────────────────────────────────────────────────────────
def _stub_qa_env(monkeypatch, tmp_path, review):
    """Подменить окружение vision-QA: без браузера и без сети."""
    monkeypatch.setattr(shotmod, "measure_overflow", lambda p: [])
    monkeypatch.setattr(
        shotmod, "screenshot_slides",
        lambda html, indices, base: {i: tmp_path / f"{i}.png" for i in indices})
    monkeypatch.setattr(visionmod, "review_slide", review)


def test_vision_qa_runs_in_parallel_and_collects_all(monkeypatch, tmp_path):
    tracker = _Tracker()

    def _review(client, shot, *, brief, theme):
        tracker.enter(shot)
        threading.Event().wait(0.03)     # держим воркер, чтобы пул перекрылся
        tracker.leave()
        return visionmod.QAVerdict(passed=False, fixes=["сократи"])

    _stub_qa_env(monkeypatch, tmp_path, _review)
    monkeypatch.setattr(build, "lint_html", lambda html: [])

    plan = _plan(8, freeform=True)
    notes = build._qa_notes(plan, "<html></html>", vision=True, client=object(),
                            artifacts=None, progress=lambda m: None, workers=8)

    assert sorted(notes) == [1, 2, 3, 4, 5, 6, 7, 8]
    assert all(notes[i] == ["сократи"] for i in notes)
    assert tracker.peak > 1              # шли параллельно, а не по очереди


def test_vision_qa_one_slide_failure_does_not_lose_others(monkeypatch, tmp_path):
    def _review(client, shot, *, brief, theme):
        if shot.stem == "2":
            raise LLMFormatError("модель сломалась")
        return visionmod.QAVerdict(passed=False, fixes=[f"fix-{shot.stem}"])

    _stub_qa_env(monkeypatch, tmp_path, _review)
    monkeypatch.setattr(build, "lint_html", lambda html: [])
    msgs = []

    notes = build._qa_notes(_plan(3, freeform=True), "<html></html>", vision=True,
                            client=object(), artifacts=None, progress=msgs.append,
                            workers=4)

    assert notes == {1: ["fix-1"], 3: ["fix-3"]}
    assert any("vision-qa слайда 2 не удался" in m for m in msgs)


def test_vision_qa_survives_transient_api_error(monkeypatch, tmp_path):
    """503 в vision-QA — это потеря ревью одного слайда, а не всей сборки."""
    def _review(client, shot, *, brief, theme):
        if shot.stem == "2":
            raise _api_503()
        return visionmod.QAVerdict(passed=False, fixes=[f"fix-{shot.stem}"])

    _stub_qa_env(monkeypatch, tmp_path, _review)
    monkeypatch.setattr(build, "lint_html", lambda html: [])
    msgs = []

    notes = build._qa_notes(_plan(3, freeform=True), "<html></html>", vision=True,
                            client=object(), artifacts=None, progress=msgs.append,
                            workers=4)

    assert notes == {1: ["fix-1"], 3: ["fix-3"]}
    assert any("сбой API (InternalServerError)" in m for m in msgs)


def test_vision_qa_passed_slide_adds_no_notes(monkeypatch, tmp_path):
    _stub_qa_env(monkeypatch, tmp_path,
                 lambda c, s, *, brief, theme: visionmod.QAVerdict(passed=True,
                                                                   fixes=[]))
    monkeypatch.setattr(build, "lint_html", lambda html: [])
    notes = build._qa_notes(_plan(4, freeform=True), "<html></html>", vision=True,
                            client=object(), artifacts=None,
                            progress=lambda m: None)
    assert notes == {}


# ── autofix пулом ────────────────────────────────────────────────────────────
def _stub_polish(monkeypatch, notes):
    """assemble/lint/QA заглушены; возвращает лог брифов по каждому assemble."""
    monkeypatch.setattr(build, "lint_html", lambda html: [])
    monkeypatch.setattr(build, "_qa_notes", lambda *a, **k: dict(notes))
    assembled: list[list[str]] = []

    def _assemble(plan, theme="dark"):
        assembled.append([s.content["brief"] for s in plan.slides])
        return "<html/>"

    monkeypatch.setattr(build, "assemble", _assemble)
    return assembled


def test_autofix_runs_in_parallel_and_applies_all(monkeypatch, tmp_path):
    tracker = _Tracker()

    def _autofix(client, library, slide, notes, *, deck_title=""):
        tracker.enter(slide.index)
        threading.Event().wait(0.03)
        tracker.leave()
        return slide.model_copy(update={"content": {"brief": "fixed"}})

    assembled = _stub_polish(monkeypatch, {i: ["n"] for i in range(1, 9)})
    monkeypatch.setattr(build, "autofix_slide", _autofix)

    build.polish_plan(_plan(8), tmp_path / "out.html", client=object(),
                      library=object(), vision=False, max_autofix=1, workers=8)

    assert assembled[1] == ["fixed"] * 8  # второй assemble — уже по исправленному
    assert tracker.peak > 1


def test_autofix_failure_keeps_original_slide(monkeypatch, tmp_path):
    def _autofix(client, library, slide, notes, *, deck_title=""):
        if slide.index == 2:
            raise LLMFormatError("не смог")
        return slide.model_copy(update={"content": {"brief": "fixed"}})

    assembled = _stub_polish(monkeypatch, {1: ["n"], 2: ["n"], 3: ["n"]})
    monkeypatch.setattr(build, "autofix_slide", _autofix)
    msgs = []

    build.polish_plan(_plan(3), tmp_path / "out.html", client=object(),
                      library=object(), vision=False, max_autofix=1,
                      progress=msgs.append)

    assert assembled[1] == ["fixed", "s2", "fixed"]  # слайд 2 остался как был
    assert any("autofix слайда 2 не удался" in m for m in msgs)


def test_autofix_survives_transient_api_error(monkeypatch, tmp_path):
    """503 в autofix НЕ должен ронять уже собранную деку (прод-сбой 18:37)."""
    def _autofix(client, library, slide, notes, *, deck_title=""):
        if slide.index == 2:
            raise _api_503()
        return slide.model_copy(update={"content": {"brief": "fixed"}})

    assembled = _stub_polish(monkeypatch, {1: ["n"], 2: ["n"], 3: ["n"]})
    monkeypatch.setattr(build, "autofix_slide", _autofix)
    msgs = []

    out = build.polish_plan(_plan(3), tmp_path / "out.html", client=object(),
                            library=object(), vision=False, max_autofix=1,
                            progress=msgs.append)

    assert out.exists()                                # дека дописана, а не потеряна
    assert assembled[1] == ["fixed", "s2", "fixed"]    # слайд 2 остался как был
    assert any("сбой API (InternalServerError)" in m for m in msgs)


def test_autofix_skips_notes_for_missing_slide(monkeypatch, tmp_path):
    calls = []

    def _autofix(client, library, slide, notes, *, deck_title=""):
        calls.append(slide.index)
        return slide

    _stub_polish(monkeypatch, {1: ["n"], 99: ["призрак"]})
    monkeypatch.setattr(build, "autofix_slide", _autofix)
    msgs = []

    build.polish_plan(_plan(2), tmp_path / "out.html", client=object(),
                      library=object(), vision=False, max_autofix=1,
                      progress=msgs.append)

    assert calls == [1]
    assert any("несуществующему слайду 99" in m for m in msgs)


# ── мягкий дедлайн: отдать деку без косметики, но отдать ─────────────────────
def test_wrapup_skips_vision_and_autofix_but_still_writes_deck(monkeypatch,
                                                               tmp_path):
    """Бюджет исчерпан → хвост пропущен, файл всё равно есть.

    Прод 2026-07-28: деградировавший шлюз съел бюджет ретраями, сборка упёрлась в
    watchdog и пользователь после 42 минут не получил НИЧЕГО."""
    monkeypatch.setattr(build, "lint_html",
                        lambda html: [])  # без замечаний из линтера
    called = []
    monkeypatch.setattr(build, "_qa_notes",
                        lambda *a, **k: called.append(k["vision"]) or {1: ["n"]})
    monkeypatch.setattr(build, "autofix_slide",
                        lambda *a, **k: pytest.fail("autofix не должен вызываться"))
    monkeypatch.setattr(build, "assemble", lambda plan, theme="dark": "<html/>")
    msgs = []

    out = build.polish_plan(_plan(3), tmp_path / "out.html", client=object(),
                            library=object(), vision=True, max_autofix=1,
                            wrapup=lambda: True, progress=msgs.append)

    assert out.read_text(encoding="utf-8") == "<html/>"   # дека доведена до файла
    assert called == [False]                              # vision-QA не звалась
    limits = [m for m in msgs if m.startswith("limit:")]
    assert len(limits) == 2                               # и про вычитку, и про автоправки
    assert all("отдаю деку как есть" in m for m in limits)


def test_wrapup_false_keeps_full_tail(monkeypatch, tmp_path):
    """Обратная сторона контракта: без дедлайна хвост работает как раньше."""
    assembled = _stub_polish(monkeypatch, {1: ["n"]})
    monkeypatch.setattr(build, "autofix_slide",
                        lambda c, l, slide, n, *, deck_title="":
                        slide.model_copy(update={"content": {"brief": "fixed"}}))
    msgs = []

    build.polish_plan(_plan(2), tmp_path / "out.html", client=object(),
                      library=object(), vision=False, max_autofix=1,
                      progress=msgs.append)

    assert assembled[1] == ["fixed", "s2"]
    assert not [m for m in msgs if m.startswith("limit:")]


def test_soft_deadline_fires_before_hard_watchdog():
    """Мягкий дедлайн обязан быть строго раньше жёсткого — иначе он бесполезен."""
    from webapp.runner import SOFT_DEADLINE_FRAC
    assert 0 < SOFT_DEADLINE_FRAC < 1


def test_single_llm_call_cannot_outlive_the_build_budget():
    """Watchdog не может прервать висящий HTTP-запрос, поэтому худший случай
    одного вызова обязан быть много меньше бюджета сборки (прод 2026-07-28:
    300 с × 6 ретраев = 30 мин на один вызов при watchdog в 40 мин)."""
    from htmlslides.pipeline.client import DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT
    from webapp.runner import BUILD_TIMEOUT_SEC, SOFT_DEADLINE_FRAC

    worst_call = DEFAULT_TIMEOUT * (DEFAULT_MAX_RETRIES + 1)
    # Должен укладываться в остаток бюджета ПОСЛЕ мягкого дедлайна — иначе один
    # висящий вызов съест окно, в котором дека дописывается на диск.
    assert worst_call < BUILD_TIMEOUT_SEC * (1 - SOFT_DEADLINE_FRAC)


# сообщение о таймауте покрыто в test_runner.py::
# test_watchdog_force_fails_overrunning_build (проверяет живое событие, не текст)


# ── приоритеты логов для journald ────────────────────────────────────────────
def test_stdlib_formatter_prefixes_syslog_priority():
    fmt = _PriorityFormatter("%(message)s")
    rec = logging.LogRecord("x", logging.WARNING, "f", 1, "внимание", None, None)
    assert fmt.format(rec).startswith("<4>")
    rec = logging.LogRecord("x", logging.ERROR, "f", 1, "беда", None, None)
    assert fmt.format(rec).startswith("<3>")
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "ок", None, None)
    assert fmt.format(rec).startswith("<6>")


def test_structlog_renders_priority_prefix(capsys):
    import structlog
    from webapp.logging_setup import configure_service_logging

    # configure_service_logging трогает глобальные логгеры (и запоминает текущий
    # sys.stdout, подменённый capsys) — возвращаем всё назад, иначе соседние
    # тесты падают на «I/O operation on closed file».
    saved_structlog = structlog.get_config()
    saved_handlers = logging.getLogger().handlers[:]
    try:
        configure_service_logging()
        structlog.get_logger().warning("planner.section_fallback",
                                       section="Для кого")
        out = capsys.readouterr().out
    finally:
        structlog.configure(**saved_structlog)
        logging.getLogger().handlers = saved_handlers
    assert out.startswith("<4>")
    assert "planner.section_fallback" in out
