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


async def test_run_forwards_events_and_captures_result(monkeypatch):
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())

    # Fake progress module the runner will patch.
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    def fake_run(inp):
        prog.publish(_Event(stage="parsing"))
        prog.publish(_Event(terminal=True, stage="done", result_path="/tmp/out.pptx"))

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
    assert events[-1]["result_path"] == "/tmp/out.pptx"
    assert r.result_path("s1") == "/tmp/out.pptx"


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
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    release = threading.Event()
    monkeypatch.setattr(runner, "_pipeline_run", lambda inp: release.wait(timeout=5))

    a = types.SimpleNamespace(session_id="a", mode="verstai")
    b = types.SimpleNamespace(session_id="b", mode="verstai")
    r.start(a)          # occupies the single worker thread
    qb = r.start(b)     # queued behind a — never starts

    assert r.cancel("b") is True
    ev = await asyncio.wait_for(qb.get(), timeout=2)
    assert ev["terminal"] is True and ev["stage"] == "cancelled"
    assert "b" not in r._active
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
