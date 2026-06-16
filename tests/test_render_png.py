import zipfile
import webapp.render_png as render_png


def test_zip_pngs_packs_files(tmp_path):
    a = tmp_path / "qa-slide-01.png"
    b = tmp_path / "qa-slide-02.png"
    a.write_bytes(b"\x89PNG-a")
    b.write_bytes(b"\x89PNG-b")
    out = tmp_path / "deck.zip"
    render_png.zip_pngs({1: a, 2: b}, out)
    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.namelist()) == ["slide-01.png", "slide-02.png"]


def test_export_zip_invokes_screenshotter(monkeypatch, tmp_path):
    deck = tmp_path / "deck.html"
    deck.write_text('<section class="slide">a</section>'
                    '<section class="slide">b</section>', encoding="utf-8")

    captured = {}

    def fake_shot(html_path, indices, out_dir, viewport=(1920, 1080)):
        captured["indices"] = list(indices)
        captured["viewport"] = viewport
        paths = {}
        for i in captured["indices"]:
            p = tmp_path / f"qa-slide-{i:02d}.png"
            p.write_bytes(b"\x89PNG")
            paths[i] = p
        return paths

    monkeypatch.setattr(render_png, "_screenshot_slides", fake_shot)
    out = render_png.export_zip(deck, tmp_path / "out.zip")
    assert captured["indices"] == [1, 2]
    assert captured["viewport"] == (1920, 1080)
    assert out.is_file()
