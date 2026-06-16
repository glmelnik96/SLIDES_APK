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
