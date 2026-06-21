import asyncio
import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from fastapi.testclient import TestClient
import webapp.app as appmod
import webapp.config as cfg


def _client(monkeypatch, tmp_path, db="t.db"):
    """TestClient with an isolated temp DB + workdir, emulating one process."""
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(cfg.settings, "db_url",
                        f"sqlite+aiosqlite:///{tmp_path / db}")
    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cfg.settings, "dev_user_id", "")  # require header
    return TestClient(appmod.app)


def H(uid="u1"):
    return {"X-User-Id": uid}


def _no_run(monkeypatch):
    monkeypatch.setattr(appmod.runner, "start", lambda inp, **kw: asyncio.Queue())


def test_index_served(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]


def test_shell_cache_busts_static_assets(monkeypatch, tmp_path):
    import re
    with _client(monkeypatch, tmp_path) as c:
        for path in ("/", "/editor"):
            html = c.get(path).text
            assets = re.findall(r'/static/[\w./-]+\.(?:js|css)(\?v=\d+)?', html)
            assert assets and all(q for q in assets)


def test_shell_has_canon_nav_with_slides_active(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        html = c.get("/").text
        # all three sections link to their gateway roots
        assert 'href="/images"' in html
        assert 'href="/creatives"' in html
        # Слайды is the active section
        assert 'href="/slides"    class="topnav__link is-active"' in html
        assert ">Cloud.ru <span>Design</span>" in html


def test_shell_injects_gateway_email(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        html = c.get("/", headers={"X-User-Email": "u@cloud.ru"}).text
        assert "u@cloud.ru" in html
        assert "{{ email }}" not in html  # placeholder always resolved
        # no email header → placeholder still resolved (empty), never leaks
        assert "{{ email }}" not in c.get("/").text


def test_default_bind_is_loopback():
    """Contract §1: upstream must listen on loopback only, never 0.0.0.0."""
    from webapp.config import Settings
    assert Settings().host == "127.0.0.1"


def test_api_requires_gateway_header(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/api/history").status_code == 401
        assert c.get("/api/jobs/active").status_code == 401


def test_create_job_starts_runner_html_only(monkeypatch, tmp_path):
    _no_run(monkeypatch)
    started = {}
    monkeypatch.setattr(appmod.runner, "start",
                        lambda inp, **kw: started.update(mode=inp.mode.value,
                                                         user=kw.get("user_id"))
                        or asyncio.Queue())
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/api/jobs", data={"mode": "htmlnew"},
                   files={"file": ("x.md", b"# hi", "text/markdown")}, headers=H())
        assert r.status_code == 200
        assert r.json()["kind"] == "html"
        assert started["mode"] == "htmlnew"
        assert started["user"] is not None


def test_pptx_modes_rejected(monkeypatch, tmp_path):
    _no_run(monkeypatch)
    with _client(monkeypatch, tmp_path) as c:
        for mode in ("verstai", "design"):
            r = c.post("/api/jobs", data={"mode": mode},
                       files={"file": ("x.pptx", b"PK", "application/octet-stream")},
                       headers=H())
            assert r.status_code == 400


def test_history_scoped_to_user(monkeypatch, tmp_path):
    _no_run(monkeypatch)
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/api/history", headers=H("u1")).json() == []
        c.post("/api/jobs", data={"mode": "htmlnew"},
               files={"file": ("a.md", b"# a", "text/markdown")}, headers=H("u1"))
        # u1 sees their job; u2 sees nothing
        assert len(c.get("/api/history", headers=H("u1")).json()) == 1
        assert c.get("/api/history", headers=H("u2")).json() == []


def test_clear_history_spares_active_run(monkeypatch, tmp_path):
    """Clearing history must not delete an in-flight run's Job row (which would
    404 its /events, /deck and /cancel and orphan the result). Regression for the
    VM incident where 'Очистить' killed a running build."""
    _no_run(monkeypatch)
    with _client(monkeypatch, tmp_path) as c:
        # one finished job + one still-active (status stays 'queued' with _no_run)
        done = c.post("/api/jobs", data={"mode": "htmlnew"},
                      files={"file": ("done.md", b"# d", "text/markdown")},
                      headers=H("u1")).json()["session_id"]
        active = c.post("/api/jobs", data={"mode": "htmlnew"},
                        files={"file": ("run.md", b"# r", "text/markdown")},
                        headers=H("u1")).json()["session_id"]
        # mark the first terminal so clear has something to remove
        import asyncio as _aio
        import webapp.app as _app
        import webapp.jobs_repo as _jr

        async def _finish():
            async with _app.app.state.sessionmaker() as s:
                await _jr.mark_terminal(s, done, status="done",
                                        result_path=None, error=None)
                await s.commit()
        _aio.get_event_loop().run_until_complete(_finish())

        assert c.post("/api/history/clear", headers=H("u1")).status_code == 200
        # active run's Job row survives (finished one is removed)
        ids = {j["id"] for j in c.get("/api/history", headers=H("u1")).json()}
        assert active in ids
        assert done not in ids
        # and the active run's owner-only deck endpoint no longer 404s on ownership
        import webapp.deck_edit as de
        de.save_deck(active, '<section class="slide">A</section>')
        assert c.get(f"/api/jobs/{active}/deck", headers=H("u1")).status_code == 200


def test_ownership_isolation_on_deck(monkeypatch, tmp_path):
    _no_run(monkeypatch)
    import webapp.deck_edit as de
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/api/jobs", data={"mode": "htmlnew"},
                   files={"file": ("a.md", b"# a", "text/markdown")}, headers=H("u1"))
        sid = r.json()["session_id"]
        de.save_deck(sid, '<section class="slide">A</section>')
        # owner can read the deck; another user gets 404 (not 403 — no leak)
        assert c.get(f"/api/jobs/{sid}/deck", headers=H("u1")).status_code == 200
        assert c.get(f"/api/jobs/{sid}/deck", headers=H("u2")).status_code == 404


def test_chat_endpoint_owner_only(monkeypatch, tmp_path):
    _no_run(monkeypatch)
    import webapp.deck_edit as de
    import webapp.chat_edit as ce
    monkeypatch.setattr(ce, "rewrite_slide",
                        lambda html, idx, instr, client=None:
                        '<section class="slide">EDITED</section>')
    with _client(monkeypatch, tmp_path) as c:
        sid = c.post("/api/jobs", data={"mode": "htmlnew"},
                     files={"file": ("a.md", b"# a", "text/markdown")},
                     headers=H("u1")).json()["session_id"]
        de.save_deck(sid, '<section class="slide">A</section>')
        # non-owner blocked
        assert c.post(f"/api/jobs/{sid}/chat", json={"slide_index": 1, "instruction": "x"},
                      headers=H("u2")).status_code == 404
        # owner ok
        r = c.post(f"/api/jobs/{sid}/chat", json={"slide_index": 1, "instruction": "короче"},
                   headers=H("u1"))
        assert r.status_code == 200
        assert de.deck_path(sid).read_text("utf-8") == '<section class="slide">EDITED</section>'


def test_chat_requires_instruction(monkeypatch, tmp_path):
    _no_run(monkeypatch)
    import webapp.deck_edit as de
    with _client(monkeypatch, tmp_path) as c:
        sid = c.post("/api/jobs", data={"mode": "htmlnew"},
                     files={"file": ("a.md", b"# a", "text/markdown")},
                     headers=H("u1")).json()["session_id"]
        de.save_deck(sid, '<section class="slide">A</section>')
        r = c.post(f"/api/jobs/{sid}/chat", json={"slide_index": 1, "instruction": "  "},
                   headers=H("u1"))
        assert r.status_code == 400


def test_status_unknown_404(monkeypatch, tmp_path):
    _no_run(monkeypatch)
    with _client(monkeypatch, tmp_path) as c:
        # not even a Job row → ownership check 404s
        r = c.get("/api/jobs/nope/status", headers=H("u1"))
        assert r.status_code == 404


def test_create_job_capacity_429(monkeypatch, tmp_path):
    from webapp.runner import CapacityError

    def full(inp, **kw):
        raise CapacityError("максимум 5 сборок одновременно")

    monkeypatch.setattr(appmod.runner, "start", full)
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/api/jobs", data={"mode": "htmlnew"},
                   files={"file": ("x.md", b"# hi", "text/markdown")}, headers=H())
        assert r.status_code == 429


def test_runner_per_user_limit():
    """A single user can't exceed the per-user active cap (others unaffected)."""
    import asyncio as _aio
    from webapp.runner import JobRunner, CapacityError

    r = JobRunner(max_active=10, max_per_user=2)
    r.bind_loop(_aio.new_event_loop())
    r._install_sink = lambda: None  # skip real progress sink
    r._pool.submit = lambda fn: None  # don't actually run

    class _Inp:
        def __init__(self, sid):
            self.session_id, self.mode = sid, "htmlnew"

    r.start(_Inp("a"), user_id=1)
    r.start(_Inp("b"), user_id=1)
    try:
        r.start(_Inp("c"), user_id=1)
        assert False, "expected CapacityError"
    except CapacityError:
        pass
    # a different user is unaffected
    r.start(_Inp("d"), user_id=2)
