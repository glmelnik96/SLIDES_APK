"""Thumb-эндпоинты пикера: ленивая генерация, кэш, инвалидация, 404-фолбэк."""
import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from webapp import tpl_thumbs
from tests.test_draft import H, _client


def _fake_render(calls):
    def fake(html, out):
        calls.append(html)
        out.write_bytes(b"\x89PNG-fake")
    return fake


def test_thumb_generates_then_serves_from_cache(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tpl_thumbs, "_render_png", _fake_render(calls))
    with _client(monkeypatch, tmp_path) as c:
        r1 = c.get("/api/templates/cover/thumb?theme=dark", headers=H())
        assert r1.status_code == 200
        assert r1.headers["content-type"] == "image/png"
        r2 = c.get("/api/templates/cover/thumb?theme=dark", headers=H())
        assert r2.status_code == 200
        assert len(calls) == 1          # второй запрос — из кэша


def test_thumb_theme_is_separate_cache_entry(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tpl_thumbs, "_render_png", _fake_render(calls))
    with _client(monkeypatch, tmp_path) as c:
        c.get("/api/templates/cover/thumb?theme=dark", headers=H())
        c.get("/api/templates/cover/thumb?theme=light", headers=H())
        assert len(calls) == 2
        # светлая тема реально уходит в рендер светлой декой
        assert 'data-theme="light"' in calls[1]


def test_thumb_regenerated_after_catalog_change(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tpl_thumbs, "_render_png", _fake_render(calls))
    monkeypatch.setattr(tpl_thumbs, "_library_mtime", lambda: 0.0)
    with _client(monkeypatch, tmp_path) as c:
        c.get("/api/templates/cover/thumb", headers=H())
        # «каталог обновили»: mtime library.json стал больше mtime PNG
        monkeypatch.setattr(tpl_thumbs, "_library_mtime", lambda: 1e12)
        c.get("/api/templates/cover/thumb", headers=H())
        assert len(calls) == 2


def test_thumb_unknown_template_404(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/api/templates/nope/thumb",
                     headers=H()).status_code == 404


def test_thumb_chromium_unavailable_404(monkeypatch, tmp_path):
    """Chromium нет → 404 → клиент падает на live-iframe (мягкая деградация)."""
    def boom(html, out):
        raise tpl_thumbs.ThumbUnavailable("no chromium")
    monkeypatch.setattr(tpl_thumbs, "_render_png", boom)
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/api/templates/cover/thumb",
                     headers=H()).status_code == 404


def test_diagram_thumb(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tpl_thumbs, "_render_png", _fake_render(calls))
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/api/diagrams/flowchart/thumb", headers=H())
        assert r.status_code == 200 and len(calls) == 1
        assert c.get("/api/diagrams/nope/thumb",
                     headers=H()).status_code == 404
