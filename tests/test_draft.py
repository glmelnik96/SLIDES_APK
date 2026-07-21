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


def test_draftslide_typed_fields_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    p = draft.DraftPlan(slides=[draft.DraftSlide(
        brief="строение гриба", slide_type="bullets",
        fields={"heading": "Строение", "bullets": ["шляпка", "ножка"]})])
    draft.save_plan("typed", p)
    back = draft.load_plan("typed")
    assert back.slides[0].slide_type == "bullets"
    assert back.slides[0].fields["bullets"] == ["шляпка", "ножка"]
    # default: a plain slide has no type
    assert draft.DraftSlide().slide_type is None
    assert draft.DraftSlide().fields is None


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


def test_render_typed_bullets_slide_uses_cards6_no_llm(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    p = draft.DraftPlan(slides=[draft.DraftSlide(
        brief="строение", slide_type="bullets",
        fields={"heading": "Строение гриба",
                "bullets": ["шляпка", "ножка", "мицелий"]})])
    html = draft_render.render_draft("sidt", p).read_text("utf-8")
    assert html.count("<section") == 1
    assert 'data-template="cards-6"' in html
    assert "Строение гриба" in html and "мицелий" in html


def test_render_typed_invalid_fields_falls_back_to_raw(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    # slide_type set but fields invalid (no heading) → not the typed template;
    # falls through to the raw path (template_id or 'blank'), still renders.
    p = draft.DraftPlan(slides=[draft.DraftSlide(
        brief="x", slide_type="bullets", fields={"bullets": ["a"]})])
    html = draft_render.render_draft("sidt2", p).read_text("utf-8")
    assert html.count("<section") == 1
    assert 'data-template="cards-6"' not in html


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
        assert "cover" in ids and "grid-2x2" in ids
        # dividers hidden
        assert "section-dots" not in ids
        # cards-6 скрыт из пикера (дизайн-дубль заливочной grid-2x2), но остаётся в library
        assert "cards-6" not in ids
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
        c.post(f"/api/drafts/{sid}/slides", json={"template_id": "grid-2x2"},
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
        assert [s["template_id"] for s in r.json()["slides"]] == ["grid-2x2", "cover"]
        # delete
        r = c.delete(f"/api/drafts/{sid}/slides/1", headers=H())
        assert [s["template_id"] for s in r.json()["slides"]] == ["cover"]


def test_replace_whole_plan_endpoint(monkeypatch, tmp_path):
    """PUT /api/drafts/{sid} replaces the entire plan in one shot — the editor's
    undo/redo restores a previously-captured snapshot this way, and the derived
    deck re-renders from it."""
    with _client(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cover"}, headers=H())
        c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cards-6"}, headers=H())
        # replace the whole plan with a swapped-order, retitled snapshot
        snap = {"title": "Снимок",
                "slides": [{"template_id": "cards-6", "content": {}},
                           {"template_id": "cover", "content": {"title": "Привет"}}]}
        r = c.put(f"/api/drafts/{sid}", json=snap, headers=H())
        assert r.status_code == 200
        body = r.json()
        assert body["title"] == "Снимок"
        assert [s["template_id"] for s in body["slides"]] == ["cards-6", "cover"]
        # the derived deck re-rendered from the replacement (two slides)
        assert c.get(f"/api/jobs/{sid}/deck", headers=H()).text.count("<section") == 2
        # persisted: a fresh GET round-trips the same order
        assert [s["template_id"] for s in
                c.get(f"/api/drafts/{sid}", headers=H()).json()["slides"]] \
            == ["cards-6", "cover"]
        # invalid body (slides must be a list of slide objects) → 400, not 500
        assert c.put(f"/api/drafts/{sid}", json={"slides": "nope"},
                     headers=H()).status_code == 400
        # cross-user → 404 (ownership)
        assert c.put(f"/api/drafts/{sid}", json=snap,
                     headers=H("intruder")).status_code == 404


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
        # Own loop, not get_event_loop(): under a full-suite run a prior async
        # test leaves the policy's loop closed/None, so get_event_loop() raises.
        loop = _aio.new_event_loop()
        try:
            kept, removed, gone = loop.run_until_complete(_run())
        finally:
            loop.close()
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


# ── template visual preview ─────────────────────────────────────────────────
def test_template_preview_renders_sample_slide(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/api/templates/cards-6/preview", headers=H())
        assert r.status_code == 200
        assert r.text.count("<section") == 1 and "<style" in r.text
        # unknown template → 404
        assert c.get("/api/templates/nope/preview", headers=H()).status_code == 404


# ── typed structured slide content (fields endpoint + build guard) ──────────
def test_update_slide_fields_endpoint(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cover"}, headers=H())
        # set typed fields
        r = c.put(f"/api/drafts/{sid}/slides/1/fields",
                  json={"slide_type": "bullets",
                        "fields": {"heading": "Строение",
                                   "bullets": ["шляпка", "ножка"]}}, headers=H())
        assert r.status_code == 200
        slide = r.json()["plan"]["slides"][0]
        assert slide["slide_type"] == "bullets"
        assert slide["fields"]["bullets"] == ["шляпка", "ножка"]
        # the deck now renders that slide as cards-6 (deterministic, no LLM)
        assert 'data-template="cards-6"' in c.get(
            f"/api/jobs/{sid}/deck", headers=H()).text
        # invalid fields (no heading) → 400
        assert c.put(f"/api/drafts/{sid}/slides/1/fields",
                     json={"slide_type": "bullets", "fields": {"bullets": ["a"]}},
                     headers=H()).status_code == 400
        # unknown type → 400
        assert c.put(f"/api/drafts/{sid}/slides/1/fields",
                     json={"slide_type": "quote", "fields": {"heading": "h"}},
                     headers=H()).status_code == 400
        # out of range → 404
        assert c.put(f"/api/drafts/{sid}/slides/9/fields",
                     json={"slide_type": "title", "fields": {"heading": "h"}},
                     headers=H()).status_code == 404
        # cross-user → 404
        assert c.put(f"/api/drafts/{sid}/slides/1/fields",
                     json={"slide_type": "title", "fields": {"heading": "h"}},
                     headers=H("intruder")).status_code == 404


def test_build_guard_ignores_typed_only_deck(monkeypatch, tmp_path):
    # a deck whose only slide is typed has nothing to LLM-build → 400 guard.
    with _client(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cover"}, headers=H())
        c.put(f"/api/drafts/{sid}/slides/1/fields",
              json={"slide_type": "title", "fields": {"heading": "H"}}, headers=H())
        assert c.post(f"/api/drafts/{sid}/build", headers=H()).status_code == 400


# ── in-place (contenteditable) edit → freeform sync ─────────────────────────
def test_inline_html_edit_makes_slide_freeform(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cover"}, headers=H())
        section = ('<section class="slide" data-template="cover">'
                   '<h1>Отредактировано вручную</h1></section>')
        r = c.put(f"/api/drafts/{sid}/slides/1/html",
                  json={"html": section}, headers=H())
        assert r.status_code == 200
        plan = r.json()
        assert plan["slides"][0]["freeform"] is True
        assert "Отредактировано" in plan["slides"][0]["content"]["html"]
        # empty html → 400; out-of-range → 404
        assert c.put(f"/api/drafts/{sid}/slides/1/html", json={"html": "  "},
                     headers=H()).status_code == 400
        assert c.put(f"/api/drafts/{sid}/slides/9/html", json={"html": section},
                     headers=H()).status_code == 404


# ── rebuild draft through the engine (mode=htmlpolish) ──────────────────────
def test_rebuild_draft_dispatches_htmlpolish(monkeypatch, tmp_path):
    """The rebuild endpoint runs the same session through the engine via the job
    runner with mode=htmlpolish; we stub the engine to avoid network."""
    from webapp import pipeline_bridge
    captured = {}

    def fake_polish_runner():
        def run(state):
            captured["session_id"] = state.session_id
            captured["mode"] = state.mode
            # simulate the engine writing the polished deck + emitting done
            from webapp import deck_edit
            from worker import progress
            out = deck_edit.deck_path(state.session_id)
            out.write_text("<!DOCTYPE html><section class='slide'>ok</section>",
                           encoding="utf-8")
            progress.done(state.session_id, detail="done", result_path=str(out))
        return run

    monkeypatch.setitem(pipeline_bridge._ENGINE, "htmlpolish", fake_polish_runner)
    with _client(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        # empty draft can't be rebuilt
        assert c.post(f"/api/drafts/{sid}/rebuild", headers=H()).status_code == 400
        c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cover"}, headers=H())
        r = c.post(f"/api/drafts/{sid}/rebuild", headers=H())
        assert r.status_code == 200 and r.json()["kind"] == "htmlpolish"
        # cross-user can't rebuild
        assert c.post(f"/api/drafts/{sid}/rebuild", headers=H("intruder")).status_code == 404


def test_draft_endpoints_409_after_plan_dropped(monkeypatch, tmp_path):
    """After a successful rebuild plan.json is dropped (the session becomes
    HTML-as-truth). A stale tab's draft calls must then get a clear 409 — not
    silently operate on an empty plan and overwrite the built deck."""
    with _client(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cover"}, headers=H())
        draft.plan_path(sid).unlink()          # what a successful rebuild does
        assert c.get(f"/api/drafts/{sid}", headers=H()).status_code == 409
        assert c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cover"},
                      headers=H()).status_code == 409
        assert c.put(f"/api/drafts/{sid}/slides/1", json={"content": {}},
                     headers=H()).status_code == 409
        assert c.delete(f"/api/drafts/{sid}/slides/1", headers=H()).status_code == 409
        assert c.post(f"/api/drafts/{sid}/rebuild", headers=H()).status_code == 409
        assert c.post(f"/api/drafts/{sid}/agent", json={"message": "привет"},
                      headers=H()).status_code == 409


def test_draft_mutation_blocked_during_rebuild(monkeypatch, tmp_path):
    """While a rebuild is running the worker holds a snapshot of the plan; a
    concurrent edit would be silently lost when it finishes. Mutations → 409,
    reads stay allowed."""
    with _client(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cover"}, headers=H())
        monkeypatch.setattr(appmod.runner, "status",
                            lambda s: {"terminal": False} if s == sid else None)
        assert c.put(f"/api/drafts/{sid}/slides/1",
                     json={"content": {"title": "x"}}, headers=H()).status_code == 409
        assert c.post(f"/api/drafts/{sid}/slides", json={"template_id": "cover"},
                      headers=H()).status_code == 409
        # reads are safe during a rebuild
        assert c.get(f"/api/drafts/{sid}", headers=H()).status_code == 200


def test_rebuild_unknown_session_404(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert c.post("/api/drafts/deadbeef/rebuild", headers=H()).status_code == 404


def test_malformed_json_body_returns_400_not_500(monkeypatch, tmp_path):
    """A bad JSON body at these boundaries must be a clean 400, never a 500."""
    with _client(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        r = c.post(f"/api/drafts/{sid}/slides",
                   content="{not json", headers={**H(),
                                                 "Content-Type": "application/json"})
        assert r.status_code == 400
