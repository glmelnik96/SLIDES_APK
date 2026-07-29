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


def test_index_has_three_entry_cards(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        html = c.get("/").text
        # three entry zones: upload (hero) + chat + template
        assert 'id="uploadFlow"' in html and 'id="create"' in html and 'id="drop"' in html
        assert 'id="openChat"' in html
        assert 'id="openManual"' in html


def test_index_has_prep_cards(monkeypatch, tmp_path):
    """Интро несёт карточку промпта для LLM (копирование + раскрываемый текст,
    запрет LLM-приписок) и ссылку на скилл В ОПИСАНИИ сверху («тут соберёте в
    сервисе, а со скиллом — сами»), выше зоны загрузки."""
    with _client(monkeypatch, tmp_path) as c:
        html = c.get("/").text
        assert 'id="copyPrompt"' in html and 'id="promptText"' in html
        assert "только сам markdown" in html  # пункт «без сообщений от нейронки»
        assert "10–20 разделов" in html
        # скилл — ссылка в верхнем описании, до точек входа
        assert 'id="skillDownload"' in html
        assert html.index('id="skillDownload"') < html.index('id="uploadFlow"')


def test_download_skill_requires_auth(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/downloads/cloud-ru-slides.skill").status_code == 401


def test_download_unknown_name_404(monkeypatch, tmp_path):
    """Только allowlist: произвольные имена (в т.ч. path traversal) не отдаются."""
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/downloads/other.bin", headers=H()).status_code == 404
        assert c.get("/downloads/..%2f.env", headers=H()).status_code == 404


def test_download_skill_served_from_downloads_dir(monkeypatch, tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    (dl / "cloud-ru-slides.skill").write_bytes(b"PK\x03\x04skill")
    monkeypatch.setattr(cfg.settings, "downloads_dir", dl)
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/downloads/cloud-ru-slides.skill", headers=H())
        assert r.status_code == 200
        assert r.content == b"PK\x03\x04skill"
        assert r.headers["content-type"].startswith("application/zip")
        assert "cloud-ru-slides.skill" in r.headers.get("content-disposition", "")


def test_download_skill_missing_file_404(monkeypatch, tmp_path):
    """Файл не разложен на диск (свежий деплой без scp) — честная 404, не 500."""
    monkeypatch.setattr(cfg.settings, "downloads_dir", tmp_path / "nope")
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/downloads/cloud-ru-slides.skill",
                     headers=H()).status_code == 404


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
        # Own loop: a prior async test can leave get_event_loop() with no current loop.
        loop = _aio.new_event_loop()
        try:
            n = loop.run_until_complete(_count())
        finally:
            loop.close()
        assert n == 1


def test_usage_event_logged_and_survives_retention(monkeypatch, tmp_path):
    """A finished build writes one anonymised usage row (right app/event/fields),
    and retention must NOT purge it (it accumulates long-term)."""
    import asyncio as _aio
    from datetime import datetime, timedelta, timezone
    _no_run(monkeypatch)
    with _client(monkeypatch, tmp_path) as c:
        # upsert a user via any authed call
        c.get("/api/history", headers=H("usagetest"))
        from sqlalchemy import select
        from webapp import retention, usage
        from webapp.db import models

        async def _run():
            sm = appmod.app.state.sessionmaker
            async with sm() as s:
                uid = (await s.execute(select(models.User.id).where(
                    models.User.gateway_user_id == "usagetest"))).scalar_one()
                await usage.log_render(
                    s, owner_user_id=uid, status="done", workflow="htmlnew",
                    started_at=datetime.now(timezone.utc) - timedelta(seconds=5),
                    result_path=None)
                await s.commit()
            async with sm() as s:
                rows = (await s.execute(select(models.UsageEvent))).scalars().all()
                # retention purge of old terminal jobs must leave usage_events alone
                await retention.purge_once(sm, ttl_hours=0)
                rows_after = (await s.execute(
                    select(models.UsageEvent))).scalars().all()
            return rows, rows_after

        # Own loop: a prior async test can leave get_event_loop() with no current loop.
        loop = _aio.new_event_loop()
        try:
            rows, rows_after = loop.run_until_complete(_run())
        finally:
            loop.close()
        assert len(rows) == 1
        ev = rows[0]
        assert ev.app == "slides" and ev.event == "render"
        assert ev.status == "done" and ev.workflow == "htmlnew"
        assert ev.gateway_user_id == "usagetest"
        assert ev.duration_ms is not None and ev.duration_ms >= 0
        assert len(rows_after) == 1   # survived retention


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
        # Own loop: a prior async test can leave get_event_loop() with no current loop.
        loop = _aio.new_event_loop()
        try:
            loop.run_until_complete(_fail())
        finally:
            loop.close()
        item = c.get("/api/history", headers=H()).json()[0]
        assert item["status"] == "failed"
        assert item["error"] == "boom 42"


def test_history_includes_usage_stats(monkeypatch, tmp_path):
    """История отдаёт расход прогона (токены/стоимость/время), чтобы фронт показал
    строку ⏱ … · ↑ … · ↓ … · ≈ … ₽. Гоним через терминальный хук — он проверяет
    и маппинг имён с провода в БД (prompt_tokens→in_tokens), и выдачу в JSON."""
    _no_run(monkeypatch)
    with _client(monkeypatch, tmp_path) as c:
        sid = c.post("/api/jobs", data={"mode": "htmlnew"},
                     files={"file": ("f.md", b"# hi", "text/markdown")},
                     headers=H()).json()["session_id"]
        import asyncio as _aio
        # Провод (ProgressEvent) несёт prompt_tokens/completion_tokens; хук маппит
        # их в in_tokens/out_tokens БД.
        data = {"session_id": sid, "stage": "done", "terminal": True,
                "result_path": None, "error": None,
                "prompt_tokens": 12_400, "completion_tokens": 8_900,
                "cost_rub": 11.96, "duration_ms": 167_000}
        # Own loop: a prior async test can leave get_event_loop() with no current loop.
        loop = _aio.new_event_loop()
        try:
            loop.run_until_complete(appmod.runner._terminal_hook(sid, data))
        finally:
            loop.close()
        item = c.get("/api/history", headers=H()).json()[0]
        assert item["status"] == "done"
        assert item["in_tokens"] == 12_400
        assert item["out_tokens"] == 8_900
        assert item["cost_rub"] == 11.96
        assert item["duration_ms"] == 167_000


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
    # Own loop: a prior async test can leave get_event_loop() with no current loop.
    loop = _aio.new_event_loop()
    try:
        loop.run_until_complete(_finish())
    finally:
        loop.close()


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


def test_drafts_list_scoped_and_deletable(monkeypatch, tmp_path):
    """Г§1 — черновик доступен со своего списка (GET /api/drafts), удаляется владельцем
    (DELETE), не виден и не удаляем чужим пользователем."""
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/api/drafts", headers=H("u1")).json() == []
        sid = c.post("/api/drafts", json={"mode": "manual"},
                     headers=H("u1")).json()["session_id"]
        items = c.get("/api/drafts", headers=H("u1")).json()
        assert len(items) == 1
        assert items[0]["id"] == sid and items[0]["mode"] == "manual"
        assert items[0]["created_at"]  # дата для «Без названия · дата»
        # scoped to owner — u2 sees nothing and cannot delete
        assert c.get("/api/drafts", headers=H("u2")).json() == []
        assert c.delete(f"/api/drafts/{sid}", headers=H("u2")).status_code == 404
        # owner deletes → gone; second delete → 404
        assert c.delete(f"/api/drafts/{sid}", headers=H("u1")).status_code == 200
        assert c.get("/api/drafts", headers=H("u1")).json() == []
        assert c.delete(f"/api/drafts/{sid}", headers=H("u1")).status_code == 404


def test_drafts_delete_rejects_non_draft(monkeypatch, tmp_path):
    """Г§1 — DELETE /api/drafts не должен трогать реальную (не-draft) сборку: только
    черновики; настоящий джоб чистится через историю."""
    _no_run(monkeypatch)
    with _client(monkeypatch, tmp_path) as c:
        sid = c.post("/api/jobs", data={"mode": "htmlnew"},
                     files={"file": ("a.md", b"# a", "text/markdown")},
                     headers=H("u1")).json()["session_id"]
        assert c.delete(f"/api/drafts/{sid}", headers=H("u1")).status_code == 404
        # a non-draft job never shows up in the drafts list either
        assert c.get("/api/drafts", headers=H("u1")).json() == []


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


def test_post_deck_rejects_non_utf8_and_empty(monkeypatch, tmp_path):
    """Malformed save bodies must be a clean 400, and must not corrupt the deck."""
    _no_run(monkeypatch)
    import webapp.deck_edit as de
    with _client(monkeypatch, tmp_path) as c:
        sid = c.post("/api/jobs", data={"mode": "htmlnew"},
                     files={"file": ("a.md", b"# a", "text/markdown")},
                     headers=H("u1")).json()["session_id"]
        de.save_deck(sid, '<section class="slide">A</section>')
        # 0xFF/0xFE are never valid UTF-8 → 400, not a 500 traceback
        assert c.post(f"/api/jobs/{sid}/deck", content=b"\xff\xfe broken",
                      headers=H("u1")).status_code == 400
        # blank body → 400 (save_deck's ValueError surfaced, not a 500)
        assert c.post(f"/api/jobs/{sid}/deck", content=b"   ",
                      headers=H("u1")).status_code == 400
        # the stored deck survived both bad saves untouched
        assert de.deck_path(sid).read_text("utf-8") == '<section class="slide">A</section>'


def test_chat_rewrite_preserves_concurrent_save(monkeypatch, tmp_path):
    """The LLM rewrite runs unlocked for seconds; if another tab saves the deck
    meanwhile, the chat result must be spliced into the FRESH deck — the stale
    snapshot must not clobber the concurrent edit of other slides."""
    _no_run(monkeypatch)
    import webapp.deck_edit as de
    import webapp.chat_edit as ce
    with _client(monkeypatch, tmp_path) as c:
        sid = c.post("/api/jobs", data={"mode": "htmlnew"},
                     files={"file": ("a.md", b"# a", "text/markdown")},
                     headers=H("u1")).json()["session_id"]
        de.save_deck(sid, '<section class="slide">one</section>'
                          '<section class="slide">two</section>')

        def fake_rewrite(html, idx, instr, client=None):
            # "another tab" saves an edit to slide 1 while the LLM is in flight
            de.save_deck(sid, html.replace("one", "ONE-EDITED"))
            return html.replace("two", "TWO-REWRITTEN")

        monkeypatch.setattr(ce, "rewrite_slide", fake_rewrite)
        r = c.post(f"/api/jobs/{sid}/chat",
                   json={"slide_index": 2, "instruction": "перепиши"},
                   headers=H("u1"))
        assert r.status_code == 200
        final = de.deck_path(sid).read_text("utf-8")
        assert "ONE-EDITED" in final and "TWO-REWRITTEN" in final
        assert ">one<" not in final and ">two<" not in final


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


def test_default_model_is_minimax_m3(monkeypatch):
    """KimiClient() without CLOUDRU_MODEL env must use MiniMax-M3 as default."""
    import htmlslides.pipeline.client as clientmod

    monkeypatch.delenv("CLOUDRU_MODEL", raising=False)

    class _Dummy:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    pass

    c = clientmod.KimiClient(rps=1000, transport=_Dummy())
    assert c.model == "MiniMaxAI/MiniMax-M3"


def test_cloudru_model_env_overrides_default(monkeypatch):
    """CLOUDRU_MODEL env must override the default model."""
    import htmlslides.pipeline.client as clientmod

    monkeypatch.setenv("CLOUDRU_MODEL", "moonshotai/Kimi-K2.6")

    class _Dummy:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    pass

    c = clientmod.KimiClient(rps=1000, transport=_Dummy())
    assert c.model == "moonshotai/Kimi-K2.6"


def test_create_job_forwards_exact_transfer(monkeypatch, tmp_path):
    started = {}
    monkeypatch.setattr(appmod.runner, "start",
                        lambda inp, **kw: started.update(exact=inp.exact_transfer)
                        or asyncio.Queue())
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/api/jobs",
                   data={"mode": "htmlnew", "exact_transfer": "true"},
                   files={"file": ("x.md", b"# hi", "text/markdown")}, headers=H())
        assert r.status_code == 200
        assert started["exact"] is True


def test_create_job_exact_defaults_false(monkeypatch, tmp_path):
    started = {}
    monkeypatch.setattr(appmod.runner, "start",
                        lambda inp, **kw: started.update(exact=inp.exact_transfer)
                        or asyncio.Queue())
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/api/jobs", data={"mode": "htmlnew"},
                   files={"file": ("x.md", b"# hi", "text/markdown")}, headers=H())
        assert r.status_code == 200
        assert started["exact"] is False


def test_create_job_exact_docx_rejected_early(monkeypatch, tmp_path):
    """«Точный перенос» + .docx отбраковывается на входе понятной 400, а не
    падает глубоко в воркере голым ValueError (реальные прод-сбои 2026-07)."""
    calls = {"n": 0}
    monkeypatch.setattr(appmod.runner, "start",
                        lambda inp, **kw: calls.update(n=calls["n"] + 1)
                        or asyncio.Queue())
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/api/jobs",
                   data={"mode": "htmlnew", "exact_transfer": "true"},
                   files={"file": ("doc.docx", b"PK\x03\x04stub", "application/octet-stream")},
                   headers=H())
        assert r.status_code == 400
        assert "Точный перенос" in r.json()["detail"]
        assert ".docx" in r.json()["detail"]
        assert calls["n"] == 0  # сборка не поставлена в очередь


def test_create_job_exact_pptx_allowed(monkeypatch, tmp_path):
    """.pptx с «Точным переносом» — валидная связка, сборка стартует."""
    started = {}
    monkeypatch.setattr(appmod.runner, "start",
                        lambda inp, **kw: started.update(exact=inp.exact_transfer)
                        or asyncio.Queue())
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/api/jobs",
                   data={"mode": "htmlnew", "exact_transfer": "true"},
                   files={"file": ("d.pptx", b"PK\x03\x04stub",
                                   "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
                   headers=H())
        assert r.status_code == 200
        assert started["exact"] is True


def test_create_job_docx_allowed_without_exact(monkeypatch, tmp_path):
    """.docx в обычной (не exact) сборке по-прежнему принимается."""
    started = {}
    monkeypatch.setattr(appmod.runner, "start",
                        lambda inp, **kw: started.update(exact=inp.exact_transfer)
                        or asyncio.Queue())
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/api/jobs", data={"mode": "htmlnew"},
                   files={"file": ("doc.docx", b"PK\x03\x04stub", "application/octet-stream")},
                   headers=H())
        assert r.status_code == 200
        assert started["exact"] is False


def test_chat_accumulates_usage():
    """chat() должен собирать resp.usage в client.usage_total (для замера экономии)."""
    import htmlslides.pipeline.client as clientmod

    class _Usage:
        prompt_tokens = 100
        completion_tokens = 20
        class prompt_tokens_details:      # noqa: N801  (мок SDK-структуры)
            cached_tokens = 40

    class _Resp:
        class _C:
            class _M:
                content = "hi"
            message = _M()
        choices = [_C()]
        usage = _Usage()

    class _Transport:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    return _Resp()

    c = clientmod.KimiClient(rps=1000, transport=_Transport())
    c.chat([{"role": "user", "content": "x"}])
    c.chat([{"role": "user", "content": "y"}])
    assert c.usage_total == {
        "prompt_tokens": 200, "completion_tokens": 40,
        "cached_tokens": 80, "calls": 2}


def test_chat_without_usage_does_not_break():
    """Транспорт без .usage (старые моки) не должен падать; счётчики остаются нулевыми."""
    import htmlslides.pipeline.client as clientmod

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
                    return _Resp()

    c = clientmod.KimiClient(rps=1000, transport=_Transport())
    c.chat([{"role": "user", "content": "x"}])
    assert c.usage_total == {
        "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "calls": 1}


def _poll_export(c, sid, fmt, uid="u1", tries=50):
    """Drive the app's event loop until the async export leaves 'running'."""
    import time
    for _ in range(tries):
        st = c.get(f"/api/jobs/{sid}/export/{fmt}", headers=H(uid)).json()
        if st["state"] != "running":
            return st
        time.sleep(0.02)
    return st


def _job_with_deck(c, de, uid="u1"):
    sid = c.post("/api/jobs", data={"mode": "htmlnew"},
                 files={"file": ("a.md", b"# a", "text/markdown")},
                 headers=H(uid)).json()["session_id"]
    de.save_deck(sid, '<section class="slide">A</section>')
    return sid


def test_export_pptx_async_flow(monkeypatch, tmp_path):
    """POST starts the render, GET polls to ready, GET .../file returns the deck."""
    _no_run(monkeypatch)
    import webapp.deck_edit as de
    import webapp.render_pptx as rp

    def fake_export(deck, out):
        out.write_bytes(b"PPTX-BYTES")
        return out

    monkeypatch.setattr(rp, "export_pptx", fake_export)
    with _client(monkeypatch, tmp_path) as c:
        sid = _job_with_deck(c, de)
        r = c.post(f"/api/jobs/{sid}/export/pptx", headers=H("u1"))
        assert r.status_code == 200 and r.json()["state"] == "running"
        st = _poll_export(c, sid, "pptx")
        assert st["state"] == "ready", st
        f = c.get(f"/api/jobs/{sid}/export/pptx/file", headers=H("u1"))
        assert f.status_code == 200
        assert f.content == b"PPTX-BYTES"
        assert "presentationml" in f.headers["content-type"]


def test_export_png_async_flow(monkeypatch, tmp_path):
    _no_run(monkeypatch)
    import webapp.deck_edit as de
    import webapp.render_png as rpng

    def fake_zip(deck, out):
        out.write_bytes(b"PK-ZIP")
        return out

    monkeypatch.setattr(rpng, "export_zip", fake_zip)
    with _client(monkeypatch, tmp_path) as c:
        sid = _job_with_deck(c, de)
        assert c.post(f"/api/jobs/{sid}/export/png", headers=H("u1")).status_code == 200
        st = _poll_export(c, sid, "png")
        assert st["state"] == "ready", st
        f = c.get(f"/api/jobs/{sid}/export/png/file", headers=H("u1"))
        assert f.status_code == 200 and f.content == b"PK-ZIP"
        assert "zip" in f.headers["content-type"]


def test_export_unknown_format_404(monkeypatch, tmp_path):
    _no_run(monkeypatch)
    import webapp.deck_edit as de
    with _client(monkeypatch, tmp_path) as c:
        sid = _job_with_deck(c, de)
        assert c.post(f"/api/jobs/{sid}/export/gif", headers=H("u1")).status_code == 404
        assert c.get(f"/api/jobs/{sid}/export/gif", headers=H("u1")).status_code == 404


def test_export_owner_only(monkeypatch, tmp_path):
    _no_run(monkeypatch)
    import webapp.deck_edit as de
    with _client(monkeypatch, tmp_path) as c:
        sid = _job_with_deck(c, de)
        # a non-owner can neither start nor poll nor download (404, no leak)
        assert c.post(f"/api/jobs/{sid}/export/pptx", headers=H("u2")).status_code == 404
        assert c.get(f"/api/jobs/{sid}/export/pptx", headers=H("u2")).status_code == 404
        assert c.get(f"/api/jobs/{sid}/export/pptx/file", headers=H("u2")).status_code == 404


def test_export_download_before_ready_409(monkeypatch, tmp_path):
    _no_run(monkeypatch)
    import webapp.deck_edit as de
    with _client(monkeypatch, tmp_path) as c:
        sid = _job_with_deck(c, de)
        # never started → idle → download is a clean 409, not a 500/leak
        assert c.get(f"/api/jobs/{sid}/export/pptx/file", headers=H("u1")).status_code == 409
        assert c.get(f"/api/jobs/{sid}/export/pptx", headers=H("u1")).json()["state"] == "idle"


def test_export_error_state_surfaced(monkeypatch, tmp_path):
    """A renderer failure becomes state=error (not a hung 'running')."""
    _no_run(monkeypatch)
    import webapp.deck_edit as de
    import webapp.render_pptx as rp

    def boom(deck, out):
        raise RuntimeError("chromium missing")

    monkeypatch.setattr(rp, "export_pptx", boom)
    with _client(monkeypatch, tmp_path) as c:
        sid = _job_with_deck(c, de)
        c.post(f"/api/jobs/{sid}/export/pptx", headers=H("u1"))
        st = _poll_export(c, sid, "pptx")
        assert st["state"] == "error"
        assert "chromium" in st["error"]
