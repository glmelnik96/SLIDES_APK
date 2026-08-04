"""Перезапуск сборки: POST /api/jobs/{id}/retry.

Кнопка «Повторить» раньше жила целиком в браузере (скролл к дропзоне + повтор,
если файл ещё выбран в <input type=file>) и после перезагрузки страницы не делала
ничего. Теперь перезапуск идёт на сервере из сохранённого исходника сессии.
"""
import asyncio
import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from fastapi.testclient import TestClient
import webapp.app as appmod
import webapp.config as cfg
from webapp.paths import session_dir


def _client(monkeypatch, tmp_path, db="t.db"):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(cfg.settings, "db_url",
                        f"sqlite+aiosqlite:///{tmp_path / db}")
    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cfg.settings, "dev_user_id", "")
    return TestClient(appmod.app)


def H(uid="u1"):
    return {"X-User-Id": uid}


def _no_run(monkeypatch):
    """Раннер не запускаем: проверяем приём и постановку, а не сборку."""
    started = []

    def fake_start(inp, **kw):
        started.append(inp)
        return asyncio.Queue()

    monkeypatch.setattr(appmod.runner, "start", fake_start)
    return started


MD = b"# Title\n\n## Section\n\ntext\n"


def _upload(c, name="doc.md", exact=False):
    data = {"mode": "htmlnew"}
    if exact:
        data["exact_transfer"] = "true"
    r = c.post("/api/jobs", data=data, files={"file": (name, MD)}, headers=H())
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


async def _set_status(sid, status):
    """Пометить джобу терминальной, минуя раннер (он в тестах заглушён)."""
    from webapp import jobs_repo
    async with appmod.app.state.sessionmaker() as s:
        await jobs_repo.mark_terminal(s, sid, status=status, result_path=None,
                                      error="boom")
        await s.commit()


def _fail(sid, status="failed"):
    # Свой loop: после async-теста get_event_loop() может остаться без текущего.
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_set_status(sid, status))
    finally:
        loop.close()


def test_retry_restarts_from_the_stored_source(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        started = _no_run(monkeypatch)
        sid = _upload(c)
        _fail(sid)
        r = c.post(f"/api/jobs/{sid}/retry", headers=H())
        assert r.status_code == 200, r.text
        new_sid = r.json()["session_id"]
        assert new_sid != sid
        # Исходник скопирован в новую сессию — сборке есть с чем работать.
        copied = session_dir(new_sid) / "input.md"
        assert copied.is_file() and copied.read_bytes() == MD
        assert started and started[-1].session_id == new_sid
        assert started[-1].source_filename == "doc.md"


def test_retry_preserves_exact_transfer(monkeypatch, tmp_path):
    """Без сохранённого флага перезапуск «Точного переноса» молча собрал бы
    обычную ИИ-деку: пользователь просит повторить то же самое."""
    with _client(monkeypatch, tmp_path) as c:
        started = _no_run(monkeypatch)
        sid = _upload(c, exact=True)
        _fail(sid)
        r = c.post(f"/api/jobs/{sid}/retry", headers=H())
        assert r.status_code == 200, r.text
        assert started[-1].exact_transfer is True


def test_retry_without_source_is_410_with_an_explanation(monkeypatch, tmp_path):
    """Ретеншен съел исходник — честный тупик с объяснением лучше кнопки,
    которая молча ничего не делает."""
    with _client(monkeypatch, tmp_path) as c:
        _no_run(monkeypatch)
        sid = _upload(c)
        _fail(sid)
        (session_dir(sid) / "input.md").unlink()
        r = c.post(f"/api/jobs/{sid}/retry", headers=H())
        assert r.status_code == 410
        assert "Загрузите файл заново" in r.json()["detail"]


def test_retry_of_a_running_job_is_refused(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        _no_run(monkeypatch)
        sid = _upload(c)                       # статус остаётся queued
        assert c.post(f"/api/jobs/{sid}/retry", headers=H()).status_code == 409


def test_retry_of_a_done_job_is_refused(monkeypatch, tmp_path):
    """У готовой деки есть «Открыть»; «собрать ещё раз то же самое» — не эта фича."""
    with _client(monkeypatch, tmp_path) as c:
        _no_run(monkeypatch)
        sid = _upload(c)
        _fail(sid, status="done")
        assert c.post(f"/api/jobs/{sid}/retry", headers=H()).status_code == 409


def test_retry_of_someone_elses_session_is_404(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        _no_run(monkeypatch)
        sid = _upload(c)
        _fail(sid)
        r = c.post(f"/api/jobs/{sid}/retry", headers=H("u2"))
        assert r.status_code == 404


def test_retry_respects_queue_capacity(monkeypatch, tmp_path):
    """Перезапуск не должен быть дырой в лимитах очереди."""
    from webapp.runner import CapacityError
    with _client(monkeypatch, tmp_path) as c:
        _no_run(monkeypatch)
        sid = _upload(c)
        _fail(sid)

        def boom(inp, **kw):
            raise CapacityError("очередь занята")

        monkeypatch.setattr(appmod.runner, "start", boom)
        assert c.post(f"/api/jobs/{sid}/retry", headers=H()).status_code == 429
