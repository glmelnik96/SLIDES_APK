import webapp.deck_edit as deck_edit


def test_count_slides():
    html = ('<div class="deck-stage">'
            '<section class="slide">a</section>'
            '<section class="slide slide--chrome-sm">b</section>'
            '</div>')
    assert deck_edit.count_slides(html) == 2


def test_save_deck_writes_file(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    path = deck_edit.save_deck("sess1", "<html>edited</html>")
    assert path.read_text("utf-8") == "<html>edited</html>"
    assert path.name == "deck.html"
    assert path.parent.name == "sess1"


def test_save_deck_rejects_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    import pytest
    with pytest.raises(ValueError):
        deck_edit.save_deck("sess1", "   ")


def test_strip_contenteditable():
    html = ('<section class="slide"><h3 contenteditable="true">A</h3>'
            '<p contenteditable=true>B</p><span CONTENTEDITABLE="true">C</span></section>')
    out = deck_edit.strip_contenteditable(html)
    assert "contenteditable" not in out.lower()
    assert ">A<" in out and ">B<" in out and ">C<" in out  # text/structure intact


def test_save_deck_strips_editor_contenteditable(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    path = deck_edit.save_deck(
        "sess1", '<section class="slide"><h3 contenteditable="true">Hi</h3></section>')
    saved = path.read_text("utf-8")
    assert "contenteditable" not in saved
    assert "Hi" in saved


def test_ensure_deck_seeds_from_source(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    src = tmp_path / "report.html"
    src.write_text("<html>engine</html>", encoding="utf-8")
    p = deck_edit.ensure_deck("s1", str(src))
    assert p is not None
    assert p.name == "deck.html"
    assert p.read_text("utf-8") == "<html>engine</html>"


def test_ensure_deck_keeps_existing_edits(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    deck_edit.save_deck("s1", "<html>edited</html>")
    src = tmp_path / "report.html"
    src.write_text("<html>engine</html>", encoding="utf-8")
    p = deck_edit.ensure_deck("s1", str(src))
    assert p.read_text("utf-8") == "<html>edited</html>"


def test_ensure_deck_none_when_no_source(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    assert deck_edit.ensure_deck("s1", None) is None
