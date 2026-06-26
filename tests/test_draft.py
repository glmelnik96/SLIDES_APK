import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from fastapi.testclient import TestClient
import webapp.app as appmod
import webapp.config as cfg
from webapp import draft, draft_render


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(cfg.settings, "db_url",
                        f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cfg.settings, "dev_user_id", "")
    return TestClient(appmod.app)


def H(uid="u1"):
    return {"X-User-Id": uid}


# ── draft.py slide operations ────────────────────────────────────────────────
def test_slide_ops_add_update_delete_reorder():
    p = draft.DraftPlan()
    p = draft.add_slide(p, draft.DraftSlide(template_id="cover"))
    p = draft.add_slide(p, draft.DraftSlide(template_id="cards-6"))
    assert [s.template_id for s in p.slides] == ["cover", "cards-6"]
    # insert at position 1
    p = draft.add_slide(p, draft.DraftSlide(template_id="statement"), at=1)
    assert [s.template_id for s in p.slides] == ["statement", "cover", "cards-6"]
    # update content
    p = draft.update_slide(p, 2, content={"title": "Привет"})
    assert p.slides[1].content == {"title": "Привет"}
    # reorder 1 -> 3
    p = draft.reorder(p, 1, 3)
    assert [s.template_id for s in p.slides] == ["cover", "cards-6", "statement"]
    # delete middle
    p = draft.delete_slide(p, 2)
    assert [s.template_id for s in p.slides] == ["cover", "statement"]


def test_slide_ops_out_of_range():
    p = draft.DraftPlan(slides=[draft.DraftSlide(template_id="cover")])
    for op in (lambda: draft.update_slide(p, 5, content={}),
               lambda: draft.delete_slide(p, 0),
               lambda: draft.reorder(p, 1, 9)):
        try:
            op()
            assert False, "expected IndexError"
        except IndexError:
            pass


def test_load_save_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    p = draft.DraftPlan(title="T", slides=[
        draft.DraftSlide(template_id="cover", content={"title": "X"})])
    draft.save_plan("sid1", p)
    back = draft.load_plan("sid1")
    assert back.title == "T" and back.slides[0].content == {"title": "X"}
    # missing file → empty plan
    assert draft.load_plan("nope").slides == []


# ── draft_render: never raises on partial/over-limit/empty content ───────────
def test_render_empty_draft_shows_placeholder_deck(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    path = draft_render.render_draft("sid", draft.DraftPlan())
    html = path.read_text("utf-8")
    assert "<section" in html and "Новая презентация" in html


def test_render_partial_required_slots_use_placeholder(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    # cover.title is required but empty → must still render (placeholder), no raise
    p = draft.DraftPlan(slides=[draft.DraftSlide(template_id="cover", content={})])
    html = draft_render.render_draft("sid", p).read_text("utf-8")
    assert html.count("<section") == 1


def test_render_clamps_overlong_text_and_lists(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    # cover.title max 20; give 200 chars + a too-long list elsewhere
    p = draft.DraftPlan(slides=[
        draft.DraftSlide(template_id="cover", content={"title": "Я" * 200}),
        draft.DraftSlide(template_id="cards-6",
                         content={"title": "T",
                                  "cards": [{"heading": "h", "text": "t"}] * 50}),
    ])
    # must not raise despite over-limit content
    html = draft_render.render_draft("sid", p).read_text("utf-8")
    assert html.count("<section") == 2


def test_render_freeform_slide(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    p = draft.DraftPlan(slides=[draft.DraftSlide(
        freeform=True, content={"html": "<div class=\"content-head\"></div><p>hi</p>"})])
    html = draft_render.render_draft("sid", p).read_text("utf-8")
    assert 'data-template="freeform"' in html


# ── endpoint: POST /api/drafts ───────────────────────────────────────────────
def test_create_draft_makes_owned_session_with_deck(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.post("/api/drafts", json={"mode": "manual"}, headers=H())
        assert r.status_code == 200
        body = r.json()
        sid = body["session_id"]
        assert body["kind"] == "draft" and body["mode"] == "manual"
        # the seeded deck is servable via the existing /deck endpoint, owner-only
        deck = c.get(f"/api/jobs/{sid}/deck", headers=H())
        assert deck.status_code == 200 and "<section" in deck.text
        # not the owner → 404 (ownership isolation holds for drafts too)
        assert c.get(f"/api/jobs/{sid}/deck", headers=H("other")).status_code == 404


def test_create_draft_rejects_bad_mode(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert c.post("/api/drafts", json={"mode": "bogus"},
                      headers=H()).status_code == 400


def test_draft_excluded_from_history(monkeypatch, tmp_path):
    """A draft (status='draft') is not a finished build → absent from history."""
    with _client(monkeypatch, tmp_path) as c:
        c.post("/api/drafts", json={"mode": "manual"}, headers=H())
        assert c.get("/api/history", headers=H()).json() == []


# ── templates catalog + slide CRUD endpoints ────────────────────────────────
def test_templates_catalog(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        cat = c.get("/api/templates", headers=H()).json()
        ids = {t["id"] for t in cat}
        assert "cover" in ids and "cards-6" in ids
        # dividers hidden
        assert "section-dots" not in ids
        cover = next(t for t in cat if t["id"] == "cover")
        assert cover["slots"]["title"]["required"] is True
        assert cover["slots"]["title"]["max_chars"] == 20


def _new_draft(c):
    return c.post("/api/drafts", json={"mode": "manual"},
                  headers=H()).json()["session_id"]


def test_slide_crud_endpoints(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        # add
        r = c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cover"},
                   headers=H())
        assert r.status_code == 200 and len(r.json()["slides"]) == 1
        c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cards-6"},
               headers=H())
        # update with valid content
        r = c.put(f"/api/drafts/{sid}/slides/1",
                  json={"content": {"title": "Привет", "subtitle": "мир"}}, headers=H())
        assert r.status_code == 200 and r.json()["errors"] == []
        # update with over-limit content → still 200 (soft), but errors reported
        r = c.put(f"/api/drafts/{sid}/slides/1",
                  json={"content": {"title": "Я" * 99}}, headers=H())
        assert r.status_code == 200
        assert any(e["code"] == "too_long" for e in r.json()["errors"])
        # the deck still renders despite the over-limit content
        assert "<section" in c.get(f"/api/jobs/{sid}/deck", headers=H()).text
        # move 1 -> 2
        r = c.post(f"/api/drafts/{sid}/slides/1/move", json={"to": 2}, headers=H())
        assert [s["template_id"] for s in r.json()["slides"]] == ["cards-6", "cover"]
        # delete
        r = c.delete(f"/api/drafts/{sid}/slides/1", headers=H())
        assert [s["template_id"] for s in r.json()["slides"]] == ["cover"]


def test_old_draft_is_purged_by_retention(monkeypatch, tmp_path):
    """Abandoned drafts don't accumulate forever — retention purges them on TTL."""
    import asyncio as _aio
    from sqlalchemy import select
    from webapp import retention
    from webapp.db import models
    with _client(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)

        async def _run():
            from datetime import datetime, timedelta, timezone
            sm = appmod.app.state.sessionmaker
            # a fresh draft (created < TTL ago) survives a 24h purge…
            kept = await retention.purge_once(sm, ttl_hours=24)
            async with sm() as s:
                alive = (await s.execute(select(models.Job).where(
                    models.Job.session_id == sid))).scalar_one()
                alive.created_at = datetime.now(timezone.utc) - timedelta(hours=48)
                await s.commit()
            # …but once it's older than the TTL, it's purged
            removed = await retention.purge_once(sm, ttl_hours=24)
            async with sm() as s:
                gone = (await s.execute(select(models.Job).where(
                    models.Job.session_id == sid))).scalar_one_or_none()
            return kept, removed, gone
        kept, removed, gone = _aio.get_event_loop().run_until_complete(_run())
        assert removed >= 1 and gone is None


def test_slide_endpoints_validation_and_ownership(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        # bad template_id
        assert c.post(f"/api/drafts/{sid}/slides", json={"template_id": "nope"},
                      headers=H()).status_code == 400
        # out-of-range update
        assert c.put(f"/api/drafts/{sid}/slides/9", json={"content": {}},
                     headers=H()).status_code == 404
        # cross-user access → 404 (ownership)
        c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cover"}, headers=H())
        assert c.get(f"/api/drafts/{sid}", headers=H("intruder")).status_code == 404
        assert c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cover"},
                      headers=H("intruder")).status_code == 404
