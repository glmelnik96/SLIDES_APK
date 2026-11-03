import os
import re
import json
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from htmlslides.assembler import assemble
from htmlslides.models import DeckPlan, SlidePlan
from webapp import draft, deck_edit
from tests.test_draft import _client, H


def built_html():
    return assemble(DeckPlan(title="Рабочая презентация", slides=[SlidePlan(
        index=1, type="content", template_id="three-col",
        content={"title": "Результаты", "columns": [
            {"heading": "Регион", "text": "Сохранить весь исходный текст"}]}),
    ]), theme="light")


def test_built_deck_opens_full_editor_without_replacing_content(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        sid = c.post("/api/drafts", headers=H(), json={"mode": "manual"}).json()["session_id"]
        draft.plan_path(sid).unlink()
        original = built_html()
        deck_edit.save_deck(sid, original)
        url = f"/api/jobs/{sid}/edit"
        assert c.post(url, headers=H("other")).status_code == 404
        r = c.post(url, headers=H())
        assert r.status_code == 200
        plan = r.json()
        assert plan["title"] == "Рабочая презентация"
        assert plan["theme"] == "light"
        assert plan["slides"][0]["content"]["columns"][0]["heading"] == "Регион"
        assert deck_edit.ensure_deck(sid, None).read_text("utf-8") == original
        # Opening again must never reset edits from an older HTML snapshot.
        content = plan["slides"][0]["content"] | {"title": "Изменено"}
        assert c.put(f"/api/drafts/{sid}/slides/1", headers=H(), json={"content": content}).status_code == 200
        assert c.post(url, headers=H()).json()["slides"][0]["content"]["title"] == "Изменено"
        assert c.post(f"/api/drafts/{sid}/slides", headers=H(), json={"template_id": "cover-image"}).status_code == 200
        assert len(c.get(f"/api/drafts/{sid}", headers=H()).json()["slides"]) == 2
        assert "Сохранить" in deck_edit.ensure_deck(sid, None).read_text("utf-8")


def test_legacy_deck_preserves_markup_styles_and_edits(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        sid = c.post("/api/drafts", headers=H(), json={"mode": "manual"}).json()["session_id"]
        draft.plan_path(sid).unlink()
        original = '<html data-theme="dark"><head><title>Старый</title><style>.custom{color:red}</style></head><body><div class="deck-stage"><section class="slide custom"><h1>Исходный</h1><p>Текст</p></section></div></body></html>'
        deck_edit.save_deck(sid, original)
        r = c.post(f"/api/jobs/{sid}/edit", headers=H())
        assert r.status_code == 200
        slide = r.json()["slides"][0]
        assert slide["freeform"] and slide["filled"]
        assert 'class="slide custom"' in slide["content"]["html"]
        assert c.put(f"/api/drafts/{sid}/slides/1", headers=H(), json={"content": {"html": slide["content"]["html"].replace("Текст", "Правка")}}).status_code == 200
        result = deck_edit.ensure_deck(sid, None).read_text("utf-8")
        assert ".custom{color:red}" in result and "Правка" in result
        assert "Исходный" in result and "freeform-zone" not in result


def test_stale_metadata_never_reverts_inline_edits(monkeypatch, tmp_path):
    from webapp.editable_deck import import_html
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    plan = import_html("stale", built_html().replace("Результаты</h3>", "Правка в HTML</h3>"))
    assert plan.slides[0].freeform
    assert "Правка в HTML" in plan.slides[0].content["html"]


def test_legacy_diagram_gets_node_and_edge_fields(monkeypatch, tmp_path):
    from htmlslides.diagrams import sample_spec
    from webapp.editable_deck import import_html
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    diagram = sample_spec("flowchart")
    html = assemble(DeckPlan(slides=[SlidePlan(index=1, type="content", template_id="diagram",
        content={"title": "Процесс", "diagram": json.dumps(diagram)})]))
    html = re.sub(r' data-editor-plan="[^"]*"', '', html)
    slide = import_html("diagram", html).slides[0]
    assert slide.slide_type == "diagram"
    assert slide.fields["heading"] == "Процесс"
    assert slide.fields["diagram"]["nodes"]
    assert slide.fields["diagram"]["edges"]


def test_open_refuses_running_build_and_missing_content(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        sid = c.post("/api/drafts", headers=H(), json={"mode": "manual"}).json()["session_id"]
        draft.plan_path(sid).unlink()
        import webapp.app as appmod
        monkeypatch.setattr(appmod.runner, "status", lambda _: {"terminal": False})
        assert c.post(f"/api/jobs/{sid}/edit", headers=H()).status_code == 409
        assert not draft.plan_path(sid).exists()
        monkeypatch.setattr(appmod.runner, "status", lambda _: None)
        deck_edit.ensure_deck(sid, None).unlink()
        assert c.post(f"/api/jobs/{sid}/edit", headers=H()).status_code == 404
        assert not draft.plan_path(sid).exists()


def test_all_template_fields_survive_import(monkeypatch, tmp_path):
    from webapp.editable_deck import import_html
    from webapp.templates_api import catalog, sample_content
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    for template in catalog(include_hidden=True):
        tid = template["id"]
        content = sample_content(tid)
        html = assemble(DeckPlan(slides=[SlidePlan(index=1, type=template["type"], template_id=tid, content=content)]))
        slide = import_html("catalog", html).slides[0]
        assert not slide.freeform, tid
        if tid != "diagram":
            assert slide.content == content, tid
