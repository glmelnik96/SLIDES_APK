"""API каталога типов диаграмм: /api/diagrams/catalog и превью типов."""
import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from fastapi.testclient import TestClient
import webapp.app as appmod
import webapp.config as cfg


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(cfg.settings, "db_url",
                        f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cfg.settings, "dev_user_id", "")
    return TestClient(appmod.app)


def H(uid="u1"):
    return {"X-User-Id": uid}


def test_catalog_lists_all_types_with_availability(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        cat = c.get("/api/diagrams/catalog", headers=H()).json()
    assert len(cat) == 15
    by_kind = {t["kind"]: t for t in cat}
    assert by_kind["flowchart"]["available"] is True
    assert by_kind["flowchart"]["display_name"] == "Блок-схема"
    assert by_kind["matrix"]["available"] is True        # волна 2 реализована
    assert by_kind["gantt_lite"]["available"] is False   # волна 3 — «скоро»
    # sample питает материализацию типа в поля слайда на клиенте
    assert by_kind["flowchart"]["sample"]["kind"] == "flowchart"
    assert by_kind["matrix"]["sample"]["kind"] == "matrix"
    assert by_kind["gantt_lite"]["sample"] is None
    for t in cat:
        assert t["display_name"] and t["when_to_use"]


def test_catalog_requires_auth(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/api/diagrams/catalog").status_code == 401


def test_kind_preview_renders_one_slide_deck(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/api/diagrams/cycle/preview", headers=H())
        assert r.status_code == 200
        assert 'data-template="diagram"' in r.text
        assert "diagram-host" in r.text and "DiagramEngine" in r.text
        assert "Цикл" in r.text                          # display_name в шапке
        # static=1 — тихие правила покоя для сетки превью
        r2 = c.get("/api/diagrams/cycle/preview?static=1", headers=H())
        assert "animation:none" in r2.text


def test_kind_preview_404_on_unknown_and_unimplemented(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/api/diagrams/nope/preview",
                     headers=H()).status_code == 404
        # gantt_lite есть в каталоге, но ещё не реализована — превью нет
        assert c.get("/api/diagrams/gantt_lite/preview",
                     headers=H()).status_code == 404
