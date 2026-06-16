import asyncio
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
