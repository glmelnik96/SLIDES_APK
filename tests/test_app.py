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


def test_shell_cache_busts_static_assets():
    """Served HTML shells must append ?v=<token> to local js/css so a shipped
    fix to editor.js/app.js reaches browsers instead of being cached forever."""
    import re
    for path in ("/", "/editor"):
        html = _client().get(path).text
        assets = re.findall(r'/static/[\w./-]+\.(?:js|css)(\?v=\d+)?', html)
        assert assets, f"no static assets found in {path}"
        assert all(q for q in assets), f"un-busted static asset in {path}: {assets}"


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


def test_get_deck_seeds_from_result(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    src = tmp_path / "report.html"
    src.write_text("<section class='slide'>hi</section>", encoding="utf-8")
    monkeypatch.setattr(appmod.runner, "result_path", lambda sid: str(src))
    c = _client()
    r = c.get("/api/jobs/abc/deck")
    assert r.status_code == 200
    assert "slide" in r.text


def test_chat_endpoint_rewrites_slide(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    import webapp.deck_edit as de
    import webapp.chat_edit as ce
    de.save_deck("cs1", '<section class="slide">A</section>')
    monkeypatch.setattr(appmod.runner, "result_path", lambda sid: None)
    monkeypatch.setattr(ce, "rewrite_slide",
                        lambda html, idx, instr, client=None:
                        '<section class="slide">EDITED</section>')
    c = _client()
    r = c.post("/api/jobs/cs1/chat", json={"slide_index": 1, "instruction": "короче"})
    assert r.status_code == 200
    assert de.deck_path("cs1").read_text("utf-8") == '<section class="slide">EDITED</section>'


def test_chat_endpoint_requires_instruction(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    c = _client()
    r = c.post("/api/jobs/cs1/chat", json={"slide_index": 1, "instruction": "   "})
    assert r.status_code == 400


def test_active_jobs_endpoint(monkeypatch):
    monkeypatch.setattr(appmod.runner, "active_jobs",
                        lambda: [{"session_id": "x", "mode": "htmlnew",
                                  "stage": "designing", "progress_pct": 45}])
    r = _client().get("/api/jobs/active")
    assert r.status_code == 200
    assert r.json()[0]["session_id"] == "x"


def test_status_unknown_404(monkeypatch):
    monkeypatch.setattr(appmod.runner, "status", lambda sid: None)
    r = _client().get("/api/jobs/nope/status")
    assert r.status_code == 404


def test_create_job_capacity_429(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    from webapp.runner import CapacityError

    def full(inp):
        raise CapacityError("максимум 5 сборок одновременно")

    monkeypatch.setattr(appmod.runner, "start", full)
    c = _client()
    r = c.post("/api/jobs", data={"mode": "htmlnew"},
               files={"file": ("x.md", b"# hi", "text/markdown")})
    assert r.status_code == 429
