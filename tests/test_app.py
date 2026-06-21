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


def test_concurrent_first_touch_same_user_no_500(monkeypatch, tmp_path):
    """A brand-new user firing several requests at once must not 500 on a racing
    user INSERT (regression: parallel workers exposed a UNIQUE-constraint race in
    upsert_user). All concurrent first-touch requests resolve to one user."""
    import threading
    _no_run(monkeypatch)
    with _client(monkeypatch, tmp_path) as c:
        results = []
        lock = threading.Lock()

        def hit():
            r = c.get("/api/history", headers=H("brandnew"))
            with lock:
                results.append(r.status_code)

        threads = [threading.Thread(target=hit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert all(s == 200 for s in results), results
        # exactly one user row created despite the concurrent first-touch
        import asyncio as _aio
        import webapp.auth as _auth

        async def _count():
            from sqlalchemy import func, select
            from webapp.db import models
            async with appmod.app.state.sessionmaker() as s:
                n = await s.execute(select(func.count()).select_from(models.User)
                                    .where(models.User.gateway_user_id == "brandnew"))
                return n.scalar_one()
        assert _aio.get_event_loop().run_until_complete(_count()) == 1


def test_empty_file_rejected(monkeypatch, tmp_path):
    """Пустой/пробельный текстовый вход реджектится на сабмите (не занимает слот
    очереди и не «успешно» собирается в пустую деку)."""
    _no_run(monkeypatch)
    with _client(monkeypatch, tmp_path) as c:
        for body in (b"", b"   \n\t  "):
            r = c.post("/api/jobs", data={"mode": "htmlnew"},
                       files={"file": ("e.md", body, "text/markdown")}, headers=H())
            assert r.status_code == 400, body
        # непустой проходит
        ok = c.post("/api/jobs", data={"mode": "htmlnew"},
                    files={"file": ("x.md", b"# hi", "text/markdown")}, headers=H())
        assert ok.status_code == 200


def test_history_includes_error_for_failed(monkeypatch, tmp_path):
    """История отдаёт причину провала, чтобы фронт показал её вместо «Открыть»."""
    _no_run(monkeypatch)
    with _client(monkeypatch, tmp_path) as c:
        sid = c.post("/api/jobs", data={"mode": "htmlnew"},
                     files={"file": ("f.md", b"# hi", "text/markdown")},
                     headers=H()).json()["session_id"]
        import asyncio as _aio
        import webapp.jobs_repo as _jr

        async def _fail():
            async with appmod.app.state.sessionmaker() as s:
                await _jr.mark_terminal(s, sid, status="failed",
                                        result_path=None, error="boom 42")
                await s.commit()
        _aio.get_event_loop().run_until_complete(_fail())
        item = c.get("/api/history", headers=H()).json()[0]
        assert item["status"] == "failed"
        assert item["error"] == "boom 42"


def test_pptx_modes_rejected(monkeypatch, tmp_path):
    _no_run(monkeypatch)
    with _client(monkeypatch, tmp_path) as c:
        for mode in ("verstai", "design"):
            r = c.post("/api/jobs", data={"mode": mode},
                       files={"file": ("x.pptx", b"PK", "application/octet-stream")},
                       headers=H())
            assert r.status_code == 400


def _mark_done(session_id):
    """Помечаем джоб терминальным (история показывает только завершённые)."""
    import asyncio as _aio
    import webapp.app as _app
    import webapp.jobs_repo as _jr

    async def _finish():
        async with _app.app.state.sessionmaker() as s:
            await _jr.mark_terminal(s, session_id, status="done",
                                    result_path=None, error=None)
            await s.commit()
    _aio.get_event_loop().run_until_complete(_finish())


def test_history_scoped_to_user(monkeypatch, tmp_path):
    _no_run(monkeypatch)
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/api/history", headers=H("u1")).json() == []
        sid = c.post("/api/jobs", data={"mode": "htmlnew"},
                     files={"file": ("a.md", b"# a", "text/markdown")},
                     headers=H("u1")).json()["session_id"]
        _mark_done(sid)                       # история = только завершённые
        # u1 sees their finished job; u2 sees nothing
        assert len(c.get("/api/history", headers=H("u1")).json()) == 1
        assert c.get("/api/history", headers=H("u2")).json() == []


def test_history_excludes_unfinished_jobs(monkeypatch, tmp_path):
    """Регрессия: ещё не собранная (queued/running) презентация НЕ должна попадать
    в «Историю сборок» — только в «Активные сборки»."""
    _no_run(monkeypatch)
    with _client(monkeypatch, tmp_path) as c:
        c.post("/api/jobs", data={"mode": "htmlnew"},
               files={"file": ("q.md", b"# q", "text/markdown")}, headers=H("u1"))
        # джоб остаётся queued (runner замокан) → история пуста
        assert c.get("/api/history", headers=H("u1")).json() == []


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
        _mark_done(done)

        assert c.post("/api/history/clear", headers=H("u1")).status_code == 200
        # finished job removed from history; active (queued) was never in history
        # anyway (history = terminal only), but its Job ROW must survive the clear.
        ids = {j["id"] for j in c.get("/api/history", headers=H("u1")).json()}
        assert done not in ids
        # active run's owner-only deck endpoint still works → its Job row survived
        # (404 would mean the row was deleted out from under the running build).
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


def test_runner_queues_beyond_parallelism_without_rejecting():
    """Jobs beyond the parallel-worker count must be ACCEPTED (queued), not 429 —
    they only fail once the system-wide max_active is reached."""
    import asyncio as _aio
    from webapp.runner import JobRunner, CapacityError

    # 2 parallel workers but room for 5 in the system → 5 accepted, 6th rejected.
    r = JobRunner(max_active=5, max_per_user=99, build_workers=2)
    r.bind_loop(_aio.new_event_loop())
    r._install_sink = lambda: None
    r._pool.submit = lambda fn: None  # don't actually run; just exercise admission

    class _Inp:
        def __init__(self, sid):
            self.session_id, self.mode = sid, "htmlnew"

    for sid in ("a", "b", "c", "d", "e"):   # all queue fine despite only 2 workers
        r.start(_Inp(sid), user_id=1)
    assert r.active_count() == 5
    try:
        r.start(_Inp("f"), user_id=1)
        assert False, "expected CapacityError at max_active"
    except CapacityError:
        pass


def test_inflight_semaphore_caps_concurrent_calls(monkeypatch):
    """The process-wide semaphore bounds simultaneous Cloud.ru calls regardless of
    how many builds/threads call chat() at once."""
    import threading
    import time
    import htmlslides.pipeline.client as clientmod

    monkeypatch.setattr(clientmod, "_INFLIGHT", threading.BoundedSemaphore(3))
    live = {"now": 0, "max": 0}
    lock = threading.Lock()

    class _Resp:
        class _C:
            class _M:
                content = "ok"
            message = _M()
        choices = [_C()]

    class _Transport:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    with lock:
                        live["now"] += 1
                        live["max"] = max(live["max"], live["now"])
                    time.sleep(0.05)
                    with lock:
                        live["now"] -= 1
                    return _Resp()

    c = clientmod.KimiClient(rps=1000, transport=_Transport())
    threads = [threading.Thread(target=lambda: c.chat([{"role": "user", "content": "x"}]))
               for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert live["max"] <= 3, f"semaphore breached: {live['max']}"
