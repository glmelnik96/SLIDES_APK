import asyncio
import threading
import time
import types
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
    assert r.queue("s1") is q
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


async def test_worker_exception_emits_failed(monkeypatch):
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    def boom(inp):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(runner, "_pipeline_run", boom)
    inp = types.SimpleNamespace(session_id="s2", mode="verstai")
    q = r.start(inp)
    ev = await asyncio.wait_for(q.get(), timeout=2)
    assert ev["terminal"] is True
    assert ev["stage"] == "failed"
    assert "kaboom" in ev["error"]


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
    await asyncio.sleep(0.05)               # let the hook coroutine run
    assert ("b", "cancelled") in hooked
    release.set()


async def test_cancel_running_job_aborts_at_next_event(monkeypatch):
    """A running job aborts cooperatively: the sink raises at the next emit."""
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    def run(inp):
        prog.publish(_Event(stage="parsing"))
        while "s1" not in r._cancel:      # wait for the stop request
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
    assert "лимит времени" in (ev.get("error") or "")
    assert "s1" not in r._active           # worker freed, no zombie
    # the watchdog timer is cleaned up in work()'s finally, which runs in the
    # worker thread AFTER the terminal event is queued — poll briefly for it.
    for _ in range(100):
        if "s1" not in r._timers:
            break
        await asyncio.sleep(0.02)
    assert "s1" not in r._timers           # watchdog timer cleaned up
