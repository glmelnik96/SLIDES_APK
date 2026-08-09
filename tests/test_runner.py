import asyncio
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor

import webapp.runner as runner


class _Event:
    def __init__(self, terminal=False, stage="parsing", result_path=None):
        self.terminal = terminal
        self.stage = stage
        self.result_path = result_path
        self.session_id = "s1"

    def model_dump(self, mode="json"):
        return {"stage": self.stage, "terminal": self.terminal,
                "result_path": self.result_path}


async def test_run_forwards_events_and_captures_result(monkeypatch, tmp_path):
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())

    # Fake progress module the runner will patch.
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    out = tmp_path / "out.pptx"
    out.write_bytes(b"PK\x03\x04")  # a real file so the empty-result net passes

    def fake_run(inp):
        prog.publish(_Event(stage="parsing"))
        prog.publish(_Event(terminal=True, stage="done", result_path=str(out)))

    monkeypatch.setattr(runner, "_pipeline_run", fake_run)

    inp = types.SimpleNamespace(session_id="s1", mode="verstai")
    q = r.start(inp)
    assert q in r._queues["s1"]
    events = []
    while True:
        ev = await asyncio.wait_for(q.get(), timeout=2)
        events.append(ev)
        if ev["terminal"]:
            break
    assert events[0]["stage"] == "parsing"
    assert events[-1]["result_path"] == str(out)
    assert r.result_path("s1") == str(out)


async def test_empty_result_is_reported_as_failed(monkeypatch):
    """A 'done' with a missing/empty result file must not be a silent success."""
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    def fake_run(inp):
        # Engine claims success but the file was never written.
        prog.publish(_Event(terminal=True, stage="done",
                            result_path="/no/such/file.pptx"))

    monkeypatch.setattr(runner, "_pipeline_run", fake_run)
    inp = types.SimpleNamespace(session_id="s1", mode="verstai")
    q = r.start(inp)
    ev = await asyncio.wait_for(q.get(), timeout=2)
    assert ev["terminal"] is True
    assert ev["stage"] == "failed"
    assert "пуст" in ev["error"]
    assert r.result_path("s1") is None


async def test_worker_exception_emits_failed(monkeypatch, caplog):
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    def boom(inp):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(runner, "_pipeline_run", boom)
    inp = types.SimpleNamespace(session_id="s2", mode="verstai")
    with caplog.at_level("ERROR"):
        q = r.start(inp)
        ev = await asyncio.wait_for(q.get(), timeout=2)
    assert ev["terminal"] is True
    assert ev["stage"] == "failed"
    # Г§8 — the user sees a calm Russian line; the raw exception (with its class and
    # text) stays in the server log, not in the user-facing error field.
    assert ev["error"] == "Внутренняя ошибка сборки — попробуйте ещё раз"
    assert "kaboom" in caplog.text


async def test_worker_value_error_surfaces_readable_reason(monkeypatch, caplog):
    """Кураторский ValueError движка (напр. неподдерживаемый формат) доходит до
    пользователя дословно, а не подменяется общей «Внутренней ошибкой»."""
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)
    reason = "точный перенос на Этапе 1 поддерживает .pptx/.md/.txt, не .docx"

    def boom(inp):
        raise ValueError(reason)

    monkeypatch.setattr(runner, "_pipeline_run", boom)
    inp = types.SimpleNamespace(session_id="s2v", mode="htmlnew")
    with caplog.at_level("ERROR"):
        q = r.start(inp)
        ev = await asyncio.wait_for(q.get(), timeout=2)
    assert ev["stage"] == "failed"
    assert ev["error"] == reason           # понятная причина, не заглушка
    assert "build failed" in caplog.text   # полный трейс всё равно в server-логе


async def test_duplicate_start_same_session_rejected(monkeypatch):
    """Starting a session that is already active (double-click on rebuild) must
    be refused: a second start would overwrite _futures/_queues for the sid and
    race two builds over the same session files."""
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    release = threading.Event()
    monkeypatch.setattr(runner, "_pipeline_run", lambda inp: release.wait(timeout=5))

    inp = types.SimpleNamespace(session_id="s1", mode="verstai")
    q = r.start(inp)
    try:
        r.start(inp)
        assert False, "expected CapacityError for a duplicate start"
    except runner.CapacityError:
        pass
    # the original job is untouched: same queue, still active
    assert r._queues["s1"] == {q} and "s1" in r._active
    release.set()


async def test_active_jobs_carries_section_count(monkeypatch):
    """active_jobs() отдаёт section_count из меты старта — фронт рисует по нему
    оценку времени сборки, и она переживает перезагрузку страницы."""
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    release = threading.Event()
    monkeypatch.setattr(runner, "_pipeline_run", lambda inp: release.wait(timeout=5))

    inp = types.SimpleNamespace(session_id="s1", mode="htmlnew")
    r.start(inp, user_id=1, section_count=7)
    try:
        jobs = r.active_jobs(user_id=1)
        assert jobs and jobs[0]["section_count"] == 7
    finally:
        release.set()


async def test_every_listener_gets_every_event(monkeypatch, tmp_path):
    """Две вкладки на одной сборке обязаны видеть ОДИН и тот же поток событий.

    Была одна общая asyncio.Queue на сессию, а /events читал её через get() —
    то есть подписчики её РАЗБИРАЛИ. Вторая вкладка (частый сценарий: сборка
    идёт 8–16 минут, пользователь открывает деку ещё раз посмотреть прогресс)
    забирала половину событий, а если ей доставался терминальный — первая
    вкладка навсегда оставалась на «Улучшаю слайды…»: поток не закрывался,
    ошибки не было, ретраиться нечему."""
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)
    out = tmp_path / "out.html"
    out.write_text("<html></html>", encoding="utf-8")

    release = threading.Event()

    def fake_run(inp):
        release.wait(timeout=5)
        prog.publish(_Event(stage="rendering"))
        prog.publish(_Event(terminal=True, stage="done", result_path=str(out)))

    monkeypatch.setattr(runner, "_pipeline_run", fake_run)

    r.start(types.SimpleNamespace(session_id="s1", mode="htmlpolish"))
    tab_a = r.subscribe("s1")
    tab_b = r.subscribe("s1")           # вторая вкладка подключилась позже
    assert tab_a is not tab_b
    release.set()

    async def drain(q):
        seen = []
        while True:
            ev = await asyncio.wait_for(q.get(), timeout=2)
            seen.append(ev["stage"])
            if ev["terminal"]:
                return seen

    assert await drain(tab_a) == ["rendering", "done"]
    assert await drain(tab_b) == ["rendering", "done"]

    # После терминального подписки сброшены — очередь отключившейся вкладки не
    # копит события до ретеншена; опоздавший читает терминальный status().
    assert r._queues.get("s1") is None
    assert r.subscribe("s1") is None and r.status("s1")["terminal"] is True


async def test_cancel_queued_job_emits_cancelled(monkeypatch):
    """A job still waiting in the queue is cancelled instantly via its Future."""
    r = runner.JobRunner(build_workers=1)   # 1 worker so b genuinely queues behind a
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    release = threading.Event()
    monkeypatch.setattr(runner, "_pipeline_run", lambda inp: release.wait(timeout=5))

    a = types.SimpleNamespace(session_id="a", mode="verstai")
    b = types.SimpleNamespace(session_id="b", mode="verstai")
    r.start(a)          # occupies the single worker thread
    qb = r.start(b)     # queued behind a — never starts

    # the terminal hook must fire for a queued-cancel too, else the DB row would
    # stay "queued" forever (missing from history and the usage log)
    hooked = []

    async def _hook(sid, data):
        hooked.append((sid, data["stage"]))
    r.set_terminal_hook(_hook)

    assert r.cancel("b") is True
    ev = await asyncio.wait_for(qb.get(), timeout=2)
    assert ev["terminal"] is True and ev["stage"] == "cancelled"
    assert "b" not in r._active
    # queued-cancel never ran → нет started_at → длительность неизвестна (None)
    assert ev.get("duration_ms") is None
    await asyncio.sleep(0.05)               # let the hook coroutine run
    assert ("b", "cancelled") in hooked
    release.set()


async def test_runner_exposes_cancel_predicate_to_pipeline(monkeypatch):
    """Variant A fast stop: the runner hands the sync build loop a predicate (via
    progress.set_cancel_check) that reports whether a session was asked to stop, so
    the fill loop can bail before launching new LLM calls — instead of waiting for
    the next progress emit to raise. cancel() must flip the predicate to True."""
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    captured = {}
    prog = types.SimpleNamespace(
        publish=None,
        set_cancel_check=lambda fn: captured.__setitem__("cc", fn))
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    release = threading.Event()
    monkeypatch.setattr(runner, "_pipeline_run", lambda inp: release.wait(timeout=5))
    inp = types.SimpleNamespace(session_id="s1", mode="verstai")
    r.start(inp)
    cc = captured.get("cc")
    assert cc is not None                  # predicate installed at sink setup
    assert cc("s1") is False               # not cancelled yet
    r.cancel("s1")
    assert cc("s1") is True                # flagged → pipeline bails before new calls
    release.set()


async def test_cancel_running_job_aborts_at_next_event(monkeypatch):
    """A running job aborts cooperatively: the sink raises at the next emit."""
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    def run(inp):
        prog.publish(_Event(stage="parsing"))
        # Ждём стоп-сигнала, но с дедлайном: без него поток пула (non-daemon)
        # крутился бы вечно, если сигнал не придёт, — и вешал бы выход из
        # интерпретатора уже после подсчёта итогов сюиты.
        deadline = time.monotonic() + 5
        while "s1" not in r._cancel and time.monotonic() < deadline:
            time.sleep(0.005)
        prog.publish(_Event(stage="classifying"))  # sink raises JobCancelled here

    monkeypatch.setattr(runner, "_pipeline_run", run)
    inp = types.SimpleNamespace(session_id="s1", mode="verstai")
    q = r.start(inp)

    first = await asyncio.wait_for(q.get(), timeout=2)
    assert first["stage"] == "parsing"
    assert r.cancel("s1") is True
    ev = await asyncio.wait_for(q.get(), timeout=2)
    assert ev["terminal"] is True and ev["stage"] == "cancelled"


async def test_done_event_carries_duration_ms(monkeypatch, tmp_path):
    """Раннер проставляет duration_ms в терминальный done — замер от started_at
    (реальное время сборки), а не поле движка."""
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    out = tmp_path / "out.html"
    out.write_text("<html></html>", encoding="utf-8")

    def fake_run(inp):
        time.sleep(0.02)                     # накопить измеримую длительность
        prog.publish(_Event(terminal=True, stage="done", result_path=str(out)))

    monkeypatch.setattr(runner, "_pipeline_run", fake_run)
    inp = types.SimpleNamespace(session_id="s1", mode="htmlnew")
    q = r.start(inp)
    ev = await asyncio.wait_for(q.get(), timeout=2)
    assert ev["terminal"] is True and ev["stage"] == "done"
    assert isinstance(ev["duration_ms"], int)
    assert ev["duration_ms"] >= 10


async def test_failed_event_carries_duration_ms(monkeypatch, caplog):
    """Сбой тоже несёт длительность — для строки статистики любого исхода."""
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    monkeypatch.setattr(runner, "_pipeline_run",
                        lambda inp: (_ for _ in ()).throw(RuntimeError("kaboom")))
    inp = types.SimpleNamespace(session_id="s2", mode="htmlnew")
    with caplog.at_level("ERROR"):
        q = r.start(inp)
        ev = await asyncio.wait_for(q.get(), timeout=2)
    assert ev["stage"] == "failed"
    assert isinstance(ev["duration_ms"], int)
    assert ev["duration_ms"] >= 0


async def test_watchdog_force_fails_overrunning_build(monkeypatch):
    """A build past build_timeout_sec is force-failed (not left as a zombie holding
    the worker), and reported as a timeout — distinct from a user cancel."""
    r = runner.JobRunner(build_timeout_sec=0.3)
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    def run(inp):
        # keep emitting progress (checkpoints) past the deadline; the watchdog marks
        # the session and the sink raises JobCancelled at the next emit. Runs well
        # past the timeout so the test isn't sensitive to scheduler jitter.
        for _ in range(10000):
            prog.publish(_Event(stage="designing"))
            time.sleep(0.01)

    monkeypatch.setattr(runner, "_pipeline_run", run)
    inp = types.SimpleNamespace(session_id="s1", mode="verstai")
    q = r.start(inp)

    ev = None
    while True:
        e = await asyncio.wait_for(q.get(), timeout=10)
        if e.get("terminal"):
            ev = e
            break
    assert ev["stage"] == "failed"
    # Причина, а не «повторите запуск»: тот же файл упрётся в тот же лимит,
    # полезный совет — разбить документ (прод-таймауты 2026-07-28).
    assert "лимит времени" in (ev.get("error") or "")
    assert "разбейте документ на части" in ev["error"]
    assert "повторите запуск" not in ev["error"]
    assert "s1" not in r._active           # worker freed, no zombie
    # the watchdog timer is cleaned up in work()'s finally, which runs in the
    # worker thread AFTER the terminal event is queued — poll briefly for it.
    for _ in range(100):
        if "s1" not in r._timers:
            break
        await asyncio.sleep(0.02)
    assert "s1" not in r._timers           # watchdog timer cleaned up


# ── разрезы для статистики: код ошибки и флаг обрезки по потолку ──────────────
class _DetailEvent(_Event):
    """Событие с текстом прогресса (движок шлёт уведомления в detail)."""
    def __init__(self, detail="", **kw):
        super().__init__(**kw)
        self.detail = detail

    def model_dump(self, mode="json"):
        d = super().model_dump(mode)
        d["detail"] = self.detail
        return d


def test_truncation_marker_matches_engine_notice():
    """Контракт двух сторон одним тестом: раннер узнаёт обрезку по префиксу
    сообщения движка. Если текст в build.py поправят — упадёт здесь, а не молча
    перестанет считаться `truncated` в статистике."""
    from htmlslides.pipeline.build import _CAP_NOTICE
    assert _CAP_NOTICE.startswith(runner._TRUNCATION_MARKER)


async def test_truncated_flag_set_from_progress_notice(monkeypatch, tmp_path):
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)
    out = tmp_path / "d.html"
    out.write_text("<section></section>", encoding="utf-8")

    def fake_run(inp):
        prog.publish(_DetailEvent(
            detail=runner._TRUNCATION_MARKER + " — собираю первые 100 слайдов.",
            stage="planning"))
        prog.publish(_Event(terminal=True, stage="done", result_path=str(out)))

    monkeypatch.setattr(runner, "_pipeline_run", fake_run)
    q = r.start(types.SimpleNamespace(session_id="s1", mode="htmlnew"))
    while not (await asyncio.wait_for(q.get(), timeout=2)).get("terminal"):
        pass
    assert r.truncated("s1") is True


async def test_truncated_defaults_to_false(monkeypatch, tmp_path):
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)
    out = tmp_path / "d.html"
    out.write_text("<section></section>", encoding="utf-8")
    monkeypatch.setattr(runner, "_pipeline_run", lambda inp: prog.publish(
        _Event(terminal=True, stage="done", result_path=str(out))))
    q = r.start(types.SimpleNamespace(session_id="s1", mode="htmlnew"))
    while not (await asyncio.wait_for(q.get(), timeout=2)).get("terminal"):
        pass
    assert r.truncated("s1") is False


async def test_failed_event_carries_error_code(monkeypatch):
    """Метрике нужен код класса, а не текст (текст может нести куски документа)."""
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    def boom(inp):
        raise ValueError("формат .doc не поддерживается")

    monkeypatch.setattr(runner, "_pipeline_run", boom)
    q = r.start(types.SimpleNamespace(session_id="s1", mode="htmlnew"))
    ev = await asyncio.wait_for(q.get(), timeout=2)
    assert ev["stage"] == "failed"
    assert ev["error_code"] == "input_invalid"


async def test_cancel_has_no_error_code(monkeypatch):
    """Отмена пользователем — не сбой, класса ошибки у неё нет."""
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    def stop(inp):
        raise runner.JobCancelled("s1")

    monkeypatch.setattr(runner, "_pipeline_run", stop)
    q = r.start(types.SimpleNamespace(session_id="s1", mode="htmlnew"))
    ev = await asyncio.wait_for(q.get(), timeout=2)
    assert ev["stage"] == "cancelled"
    assert ev.get("error_code") is None


async def test_watchdog_failure_is_coded_build_timeout(monkeypatch):
    r = runner.JobRunner(build_timeout_sec=0.3)
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    def run(inp):
        for _ in range(10000):
            prog.publish(_Event(stage="designing"))
            time.sleep(0.01)

    monkeypatch.setattr(runner, "_pipeline_run", run)
    q = r.start(types.SimpleNamespace(session_id="s1", mode="htmlnew"))
    while True:
        ev = await asyncio.wait_for(q.get(), timeout=10)
        if ev.get("terminal"):
            break
    assert ev["stage"] == "failed"
    assert ev["error_code"] == "build_timeout"


def test_section_count_accessor():
    r = runner.JobRunner()
    r._meta["s1"] = {"section_count": 42}
    assert r.section_count("s1") == 42
    assert r.section_count("nope") is None


# ── защита изоляции тестов (conftest.drain_pool) ──────────────────────────
# work() читает runner._pipeline_run ЛЕНИВО, уже внутри потока пула. Тест,
# закончившийся раньше своего воркера, оставляет поток, который стартует, когда
# monkeypatch уже подставил подделку СЛЕДУЮЩЕГО теста; события всех тестов идут
# с одним session_id "s1", поэтому чужой ивент попадал в чужую очередь и ронял
# ассерты. На 2-ядерной VM это давало 5 падений на 100 прогонов.

def test_drain_pool_joins_finished_worker(pool_drainer):
    """Отработавший воркер дренируется и не числится застрявшим."""
    pool = ThreadPoolExecutor(max_workers=1)
    done = threading.Event()
    pool.submit(done.set)
    assert pool_drainer(pool, timeout=5) == []
    assert done.is_set()


def test_drain_pool_reports_stuck_worker_instead_of_hanging(pool_drainer):
    """Зависший воркер ВОЗВРАЩАЕТСЯ списком, а не вешает дренаж навсегда.

    Ровно этот сценарий (non-daemon поток в бесконечном цикле) подвесил прогон
    на VM: pytest досчитал итог, а интерпретатор навсегда встал в _python_exit →
    join. Дренаж обязан упереться в таймаут и назвать виновника."""
    pool = ThreadPoolExecutor(max_workers=1)
    never = threading.Event()
    pool.submit(never.wait)
    try:
        started = time.monotonic()
        stuck = pool_drainer(pool, timeout=0.2)
        assert len(stuck) == 1
        assert time.monotonic() - started < 3     # уперлось в таймаут, не висит
    finally:
        never.set()                               # отпускаем поток
