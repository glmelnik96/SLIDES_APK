import pytest

from htmlslides.pipeline.build import build_deck


def _write_text_deck(path):
    path.write_text(
        "Слайд 1: Первый\n"
        "Дословный текст один.\n"
        "\n"
        "Слайд 2: Второй\n"
        "Дословный текст два.\n",
        encoding="utf-8")
    return path


def test_build_exact_from_text_offline(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOUDRU_API_KEY", raising=False)     # нет ключа → без ИИ
    src = _write_text_deck(tmp_path / "deck.txt")
    out = tmp_path / "deck.html"
    result = build_deck(src, out, mode="exact")
    assert result == out
    html = out.read_text(encoding="utf-8")
    assert html.count('data-template="exact"') == 2
    assert "Дословный текст один." in html
    assert "Дословный текст два." in html


def test_build_exact_light_theme(tmp_path, monkeypatch):
    monkeypatch.delenv("CLOUDRU_API_KEY", raising=False)
    src = _write_text_deck(tmp_path / "deck.txt")
    out = tmp_path / "deck.html"
    build_deck(src, out, mode="exact", theme="light")
    assert 'data-theme="light"' in out.read_text(encoding="utf-8")


def test_build_exact_no_marker_single_slide(tmp_path, monkeypatch):
    """Текст без разделителей → 1 слайд (не ошибка), дословно."""
    monkeypatch.delenv("CLOUDRU_API_KEY", raising=False)
    src = tmp_path / "bad.txt"
    src.write_text("текст без меток слайдов", encoding="utf-8")
    out = tmp_path / "o.html"
    build_deck(src, out, mode="exact")
    html = out.read_text(encoding="utf-8")
    assert html.count('data-template="exact"') == 1
    assert "текст без меток слайдов" in html


def test_build_exact_docx_rejected(tmp_path):
    src = tmp_path / "x.docx"
    src.write_bytes(b"PK\x03\x04stub")
    with pytest.raises(ValueError):
        build_deck(src, tmp_path / "o.html", mode="exact")


def test_build_exact_no_key_skips_ai(tmp_path, monkeypatch):
    """Без ключа design_exact_deck НЕ зовётся — детерминированный результат."""
    monkeypatch.delenv("CLOUDRU_API_KEY", raising=False)

    import htmlslides.pipeline.exact_designer as designer

    def _boom(*a, **k):
        raise AssertionError("ИИ не должен вызываться без ключа")

    monkeypatch.setattr(designer, "design_exact_deck", _boom)
    src = _write_text_deck(tmp_path / "deck.txt")
    out = tmp_path / "deck.html"
    build_deck(src, out, mode="exact")                       # не падает
    assert out.read_text(encoding="utf-8").count('data-template="exact"') == 2


def test_build_exact_designs_with_client(tmp_path, monkeypatch):
    """Есть клиент → слайды проходят через ИИ-вёрстку; текст остаётся дословным."""
    import htmlslides.pipeline.build as buildmod

    good = ('```html<section class="slide" data-template="freeform">'
            '<div class="content-head"><h3 class="content-head-title t-head-42">'
            '{{a1}}</h3></div><div class="row"><div class="col">{{a2}}</div>'
            '</div></section>```')

    class _FakeClient:
        model = "fake"
        usage_total = {}
        def chat(self, messages, *, max_tokens=4096, extra_body=None):
            return good

    monkeypatch.setattr(buildmod, "_exact_client_or_none",
                        lambda progress: _FakeClient())
    src = _write_text_deck(tmp_path / "deck.txt")
    out = tmp_path / "deck.html"
    build_deck(src, out, mode="exact")
    html = out.read_text(encoding="utf-8")
    assert 'class="col"' in html                             # брендовая вёрстка от ИИ
    assert "Дословный текст один." in html                   # дословность
    assert html.count('data-template="exact"') == 2
