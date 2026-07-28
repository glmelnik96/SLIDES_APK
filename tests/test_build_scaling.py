"""Масштабирование хвоста пайплайна (разбор прод-таймаутов 2026-07-28).

Три сборки подряд упали в watchdog (40 мин) на крупных документах: план на
десятки-сотни слайдов, а vision-QA и autofix шли строго последовательно — по
LLM-вызову на слайд. Покрываем лечение:
- vision-QA и autofix идут пулом (реально параллельно), семантика не изменилась;
- сбой одного слайда не роняет круг и не теряет остальные;
- план обрезается до потолка с честным предупреждением в прогресс;
- таймаут сборки объясняет причину, а не советует «повторите запуск»;
- уровни логов доезжают до journald префиксом <N>.
"""
import logging
import os
import threading

os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

import htmlslides.pipeline.screenshot as shotmod
import htmlslides.pipeline.vision_qa as visionmod
from htmlslides.models import DeckPlan, SlidePlan
from htmlslides.pipeline import build
from htmlslides.pipeline.client import LLMFormatError
from webapp.logging_setup import _PriorityFormatter


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
