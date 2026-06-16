import os
import asyncio
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from fastapi.testclient import TestClient
import webapp.app as appmod


def _client():
    return TestClient(appmod.app)


def test_index_served():
    r = _client().get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_history_endpoints(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    c = _client()
    assert c.get("/api/history").json() == []
    import webapp.history as history
    history.add(id="a", mode="htmlnew", source_filename="x.md",
                result_path="p", kind="html")
    assert len(c.get("/api/history").json()) == 1
    assert c.post("/api/history/clear").status_code == 200
    assert c.get("/api/history").json() == []


def test_create_job_starts_runner(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    started = {}

    def fake_start(inp):
        started["mode"] = inp.mode.value
        started["session"] = inp.session_id
        return asyncio.Queue()

    monkeypatch.setattr(appmod.runner, "start", fake_start)
    c = _client()
    r = c.post("/api/jobs", data={"mode": "htmlnew"},
               files={"file": ("x.md", b"# hi", "text/markdown")})
    assert r.status_code == 200
    assert "session_id" in r.json()
    assert started["mode"] == "htmlnew"


def test_create_job_rejects_bad_type(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    c = _client()
    r = c.post("/api/jobs", data={"mode": "verstai"},
               files={"file": ("x.md", b"hi", "text/markdown")})
    assert r.status_code == 400
