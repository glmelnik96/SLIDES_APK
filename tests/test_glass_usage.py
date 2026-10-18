"""Стеклянная сборка попадает в статистику: одна строка usage_events на сборку.

С 2026-08-20 стеклянная сборка — единственный путь сборки с сайта, но событие в
usage_events писал только терминальный хук раннера, а glass идёт мимо раннера:
последняя строка статистики датирована 2026-08-23, хотя сборки шли. Блок «Слайды»
в админке платформы с тех пор видел только сборки через API.

Сборка считается один раз: когда дособрана (done или failed, если больше половины
слайдов — заглушки после осечек модели) либо, если автор её бросил, когда черновик
удаляет ретеншен (cancelled). Черновики конструктора — не сборки и не считаются.
"""
import os

os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import webapp.app as appmod
import webapp.config as cfg
from fastapi.testclient import TestClient
from webapp import draft, glass, retention
from webapp.db import models
from webapp.db.database import init_db
from webapp.paths import session_dir


def _client_app(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(cfg.settings, "db_url", f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cfg.settings, "dev_user_id", "")
    return TestClient(appmod.app)


H = {"X-User-Id": "u1", "X-User-Email": "a@b.c"}


def _fake_fill(client, library, sp, *, deck_title="", extra="", diagram_kind=""):
    return sp.model_copy(update={"content": {"title": f"Слайд {sp.index}"}})


def _boom(*_a, **_kw):
    raise RuntimeError("403 Requested model is not available for this project")


def _glass_outline(sid, *, source=True, slides=2):
    """Аутлайн стеклянной сборки: обложка + слайды с брифом, готовые к заполнению."""
    plan = draft.DraftPlan(title="Отчёт", slides=[
        draft.DraftSlide(template_id="cover", content={"title": "Отчёт"}, filled=True)] + [
        draft.DraftSlide(template_id="statement", brief=f"Раздел {i}: факты.", src_index=i)
        for i in range(slides)])
    draft.save_plan(sid, plan)
    if source:                                   # загрузка документа — признак стеклянной сборки
        (session_dir(sid) / "input.md").write_text("# Отчёт\n\nтекст", encoding="utf-8")


def _events(tmp_path):
    with sqlite3.connect(tmp_path / "t.db") as db:
        rows = db.execute("select app, event, workflow, status, duration_ms, "
                          "gateway_user_id, meta from usage_events").fetchall()
    return [dict(app=a, event=e, workflow=w, status=s, duration_ms=d, user=u,
                 meta=json.loads(m) if isinstance(m, str) else m)
            for a, e, w, s, d, u, m in rows]


def _run_to_done(c, sid):
    for _ in range(10):
        out = c.post(f"/api/drafts/{sid}/glass/step", headers=H).json()
        if out["done"]:
            return out
    raise AssertionError("сборка не дошла до done")


def test_finished_glass_build_is_counted_once(monkeypatch, tmp_path):
    import htmlslides.pipeline.filler as filler
    monkeypatch.setattr(filler, "fill_slide", _fake_fill)
    monkeypatch.setattr(glass, "_kimi", lambda: object())
    with _client_app(monkeypatch, tmp_path) as c:
        sid = c.post("/api/drafts", json={"mode": "manual"}, headers=H).json()["session_id"]
        _glass_outline(sid)

        _run_to_done(c, sid)
        c.post(f"/api/drafts/{sid}/glass/step", headers=H)   # повторный шаг после done

        events = _events(tmp_path)
    assert len(events) == 1
    ev = events[0]
    assert (ev["app"], ev["event"], ev["workflow"], ev["status"]) == (
        "slides", "render", "glass", "done")
    assert ev["user"] == "u1" and ev["duration_ms"] is not None
    # mode — «glass», а не «manual» строки jobs: иначе в админке сборки из
    # документа читались бы как ручной конструктор.
    assert ev["meta"]["mode"] == "glass"
    assert ev["meta"]["section_count"] == 2 and ev["meta"]["slides_count"] == 3
    assert "error_code" not in ev["meta"]


def test_glass_build_of_stubs_counts_as_failed(monkeypatch, tmp_path):
    """Осечка заполнения ставит заглушку и помечает слайд заполненным, поэтому дека
    из одних заглушек формально «дособрана». В статистике это провал, не успех."""
    import htmlslides.pipeline.filler as filler
    monkeypatch.setattr(filler, "fill_slide", _boom)
    monkeypatch.setattr(glass, "_kimi", lambda: object())
    with _client_app(monkeypatch, tmp_path) as c:
        sid = c.post("/api/drafts", json={"mode": "manual"}, headers=H).json()["session_id"]
        _glass_outline(sid)
        _run_to_done(c, sid)
        events = _events(tmp_path)
    assert [e["status"] for e in events] == ["failed"]
    assert events[0]["meta"]["error_code"] == "fill_failed"


def test_constructor_draft_is_not_a_build(monkeypatch, tmp_path):
    """Черновик конструктора (документ не загружали) — не сборка: не считаем."""
    import htmlslides.pipeline.filler as filler
    monkeypatch.setattr(filler, "fill_slide", _fake_fill)
    monkeypatch.setattr(glass, "_kimi", lambda: object())
    with _client_app(monkeypatch, tmp_path) as c:
        sid = c.post("/api/drafts", json={"mode": "manual"}, headers=H).json()["session_id"]
        _glass_outline(sid, source=False)
        _run_to_done(c, sid)
        assert _events(tmp_path) == []


# ── брошенная сборка: считается, когда черновик удаляет ретеншен ─────────────
async def _sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    return async_sessionmaker(engine, expire_on_commit=False)


async def _purge_with(monkeypatch, tmp_path, setups):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    Session = await _sm()
    old = datetime.now(timezone.utc) - timedelta(hours=48)
    async with Session() as s:
        u = models.User(gateway_user_id="u1", email="a@b.c")
        s.add(u)
        await s.flush()
        for sid in setups:
            s.add(models.Job(session_id=sid, user_id=u.id, mode="manual", kind="draft",
                             status="draft", created_at=old))
        await s.commit()
    for sid, setup in setups.items():
        setup(sid)
    await retention.purge_once(Session, ttl_hours=24)
    async with Session() as s:
        return (await s.execute(select(models.UsageEvent))).scalars().all()


async def test_abandoned_glass_build_is_counted_when_purged(monkeypatch, tmp_path):
    """Автор бросил сборку (вопросы ИИ без ответа, сбой модели) — до done она не
    дойдёт никогда. Без этого события брошенные сборки не видны вовсе."""
    events = await _purge_with(monkeypatch, tmp_path, {"g1": _glass_outline})
    assert [(e.workflow, e.status) for e in events] == [("glass", "cancelled")]
    assert events[0].meta["mode"] == "glass" and events[0].gateway_user_id == "u1"
    assert events[0].duration_ms is None          # момент ухода автора неизвестен


async def test_purge_does_not_count_twice_or_count_constructor(monkeypatch, tmp_path):
    def _counted(sid):
        _glass_outline(sid)
        plan = draft.load_plan(sid)
        plan.usage_logged = True
        draft.save_plan(sid, plan)

    events = await _purge_with(monkeypatch, tmp_path, {
        "done1": _counted, "manual1": lambda sid: _glass_outline(sid, source=False)})
    assert events == []
