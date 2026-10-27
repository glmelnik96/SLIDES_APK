import io

from PIL import Image
from pptx import Presentation

import webapp.render_pptx as render_pptx


def _png(path, size=(1920, 1080)):
    Image.new("RGB", size, (10, 20, 30)).save(path)


def test_build_pptx_one_full_bleed_image_per_slide(tmp_path):
    a = tmp_path / "s1.png"
    b = tmp_path / "s2.png"
    _png(a)
    _png(b)
    out = tmp_path / "deck.pptx"
    render_pptx.build_pptx({1: a, 2: b}, out)

    prs = Presentation(str(out))
    assert len(prs.slides) == 2
    # 16:9 widescreen canvas
    assert prs.slide_width == render_pptx._SLIDE_W
    assert prs.slide_height == render_pptx._SLIDE_H
    for slide in prs.slides:
        pics = [s for s in slide.shapes if s.shape_type == 13]  # PICTURE
        assert len(pics) == 1
        pic = pics[0]
        # full-bleed: top-left origin, fills the whole slide
        assert pic.left == 0 and pic.top == 0
        assert pic.width == render_pptx._SLIDE_W
        assert pic.height == render_pptx._SLIDE_H


def test_export_pictures_reuses_supplied_pngs_without_rendering(monkeypatch, tmp_path):
    deck = tmp_path / "deck.html"
    deck.write_text('<section class="slide">a</section>', encoding="utf-8")
    png = tmp_path / "s1.png"
    _png(png)

    def boom(*a, **k):
        raise AssertionError("render_slides must not run when pngs are supplied")

    monkeypatch.setattr(render_pptx.render_png, "render_slides", boom)
    out = render_pptx.export_pictures(deck, tmp_path / "out.pptx", pngs={1: png})
    assert out.is_file()
    assert len(Presentation(str(out)).slides) == 1


def test_export_pictures_renders_when_pngs_missing(monkeypatch, tmp_path):
    deck = tmp_path / "deck.html"
    deck.write_text('<section class="slide">a</section>'
                    '<section class="slide">b</section>', encoding="utf-8")

    def fake_render(html_path):
        paths = {}
        for i in (1, 2):
            p = tmp_path / f"qa-{i}.png"
            _png(p)
            paths[i] = p
        return paths

    monkeypatch.setattr(render_pptx.render_png, "render_slides", fake_render)
    out = render_pptx.export_pictures(deck, tmp_path / "out.pptx")
    assert len(Presentation(str(out)).slides) == 2


# ------------------------------------------------- редактируемый PPTX
def _blank_pptx(out, n):
    """Что вернул бы конвертер: n слайдов без картинок."""
    prs = Presentation()
    for _ in range(n):
        prs.slides.add_slide(prs.slide_layouts[6])
    prs.save(str(out))
    return {}


def _fake_shots(calls):
    def shoot(html_path, indices, out_dir, viewport=(1920, 1080)):
        calls.append(list(indices))
        shots = {}
        for i in indices:
            p = out_dir / f"qa-slide-{i:02d}.png"
            _png(p)
            shots[i] = p
        return shots
    return shoot


def test_export_pptx_is_the_editable_one(monkeypatch, tmp_path):
    """Кнопка PPTX отдаёт сборку конвертера, а не слайды-картинки."""
    import htmlslides.topptx.convert as conv

    deck = tmp_path / "deck.html"
    deck.write_text('<section class="slide" data-template="cover">a</section>',
                    encoding="utf-8")
    monkeypatch.setattr(conv, "convert", lambda deck_html, out: _blank_pptx(out, 1))

    def pictures(*a, **k):
        raise AssertionError("картинками — только запасной путь")

    monkeypatch.setattr(render_pptx, "export_pictures", pictures)
    calls = []
    monkeypatch.setattr(render_pptx.render_png, "_screenshot_slides",
                        _fake_shots(calls))

    out = render_pptx.export_pptx(deck, tmp_path / "out.pptx")
    assert len(Presentation(str(out)).slides) == 1
    assert calls == [], "без схем браузер для картинок не нужен"


def test_a_diagram_lands_as_a_picture_in_its_box(monkeypatch, tmp_path):
    """Схему рисует diagram.js, в разметке её нет — кладём снимок её области."""
    import htmlslides.topptx.convert as conv

    deck = tmp_path / "deck.html"
    deck.write_text('<section class="slide" data-template="cover">a</section>'
                    '<section class="slide" data-template="diagram">'
                    '<div class="diagram-host" data-diagram="{}"></div></section>',
                    encoding="utf-8")
    monkeypatch.setattr(conv, "convert", lambda deck_html, out: _blank_pptx(out, 2))
    calls = []
    monkeypatch.setattr(render_pptx.render_png, "_screenshot_slides",
                        _fake_shots(calls))

    out = render_pptx.export_pptx(deck, tmp_path / "out.pptx")
    assert calls == [[2]], "снимаем только слайды со схемой"
    first, second = Presentation(str(out)).slides
    assert not [s for s in first.shapes if s.shape_type == 13]
    (pic,) = [s for s in second.shapes if s.shape_type == 13]
    x, y, w, h = (v * render_pptx._EMU_PX for v in render_pptx._DIAGRAM_BOX)
    assert (pic.left, pic.top, pic.width, pic.height) == (x, y, w, h)
    assert pic.name == "Схема"
    size = Image.open(io.BytesIO(pic.image.blob)).size
    assert size == render_pptx._DIAGRAM_BOX[2:], "картинка — область схемы, не слайд"


def test_a_failed_conversion_falls_back_to_pictures(monkeypatch, tmp_path):
    """Не собрался редактируемый — человек всё равно получает PPTX."""
    import htmlslides.topptx.convert as conv

    deck = tmp_path / "deck.html"
    deck.write_text('<section class="slide">a</section>', encoding="utf-8")

    def boom(deck_html, out):
        raise conv.ConvertError("нет геометрии для слайдов: 1")

    monkeypatch.setattr(conv, "convert", boom)
    png = tmp_path / "s1.png"
    _png(png)
    monkeypatch.setattr(render_pptx.render_png, "render_slides",
                        lambda deck_html: {1: png})

    out = render_pptx.export_pptx(deck, tmp_path / "out.pptx")
    (slide,) = Presentation(str(out)).slides
    (pic,) = [s for s in slide.shapes if s.shape_type == 13]
    assert (pic.width, pic.height) == (render_pptx._SLIDE_W, render_pptx._SLIDE_H)
