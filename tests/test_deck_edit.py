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


def test_strip_is_active_removes_from_section():
    html = '<section class="slide is-active slide--chrome-sm">A</section>'
    out = deck_edit.strip_is_active(html)
    assert 'class="slide slide--chrome-sm"' in out
    assert "is-active" not in out
    assert ">A<" in out  # content intact


def test_strip_is_active_preserves_css_selectors():
    # The runtime marker lives in a section's class; the SAME token names CSS
    # animation selectors in the embedded <style>. Stripping must touch only the
    # section attribute — never the stylesheet (else all motion breaks).
    html = ('<style>.is-active .m-enter{opacity:1}</style>'
            '<section class="slide is-active">A</section>')
    out = deck_edit.strip_is_active(html)
    assert ".is-active .m-enter{opacity:1}" in out  # CSS untouched
    assert '<section class="slide">A</section>' in out  # section cleaned


def test_save_deck_strips_stale_is_active(monkeypatch, tmp_path):
    # Reproduces the "all slides became contacts after refresh" bug: the editor
    # persisted .is-active on whichever slide was showing (here the last), so on
    # reload deck.js left two slides active and the last overlaid every slide.
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    path = deck_edit.save_deck(
        "sess1",
        '<style>.is-active .m{width:1px}</style>'
        '<section class="slide">one</section>'
        '<section class="slide is-active">two</section>')
    saved = path.read_text("utf-8")
    assert ".is-active .m{width:1px}" in saved          # CSS intact
    assert saved.count("is-active") == 1                # only the CSS one remains
    assert '<section class="slide">two</section>' in saved


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


# ── theme repaint: one attribute flip recolors the whole deck ────────────────
_DECK = ('<!DOCTYPE html><html lang="ru" data-theme="dark"><head>'
         '<style>html[data-theme="dark"]{--bg:#222}</style></head>'
         '<body><section class="slide">A</section></body></html>')


def test_get_theme_reads_attribute():
    assert deck_edit.get_theme(_DECK) == "dark"
    assert deck_edit.get_theme(_DECK.replace('"dark"', '"light"', 1)) == "light"


def test_get_theme_defaults_to_dark():
    # missing attribute (old editor build) or garbage value → dark
    assert deck_edit.get_theme("<html><body></body></html>") == "dark"
    assert deck_edit.get_theme('<html data-theme="sepia"></html>') == "dark"


def test_set_theme_flips_only_the_html_attribute():
    out = deck_edit.set_theme(_DECK, "light")
    assert '<html lang="ru" data-theme="light">' in out
    # the embedded stylesheet's [data-theme="dark"] selector must stay intact —
    # it's the token block, not state
    assert 'html[data-theme="dark"]{--bg:#222}' in out
    # round-trip back
    assert deck_edit.set_theme(out, "dark") == _DECK


def test_set_theme_injects_attribute_when_missing():
    out = deck_edit.set_theme('<html lang="ru"><body>x</body></html>', "light")
    assert '<html lang="ru" data-theme="light">' in out


def test_set_theme_rejects_unknown():
    import pytest
    with pytest.raises(ValueError):
        deck_edit.set_theme(_DECK, "sepia")


def test_hardcoded_colors_flags_freeform_slide_only():
    html = ('<html data-theme="dark"><body>'
            # slide 1 — token-driven markup: never flagged
            '<section class="slide"><h3 style="width:40px">A</h3>'
            '<svg><path fill="var(--accent)"/></svg></section>'
            # slide 2 — freeform with a literal color baked for the dark bg
            '<section class="slide"><p style="color:#fff">B</p></section>'
            # slide 3 — literal rgb() in an SVG attr
            '<section class="slide"><svg><rect fill="rgb(34,34,34)"/></svg>'
            '</section>'
            # embedded deck.js after the last slide must not leak into the scan
            '<script>el.setAttribute("style", "color:#26D07C");</script>'
            '</body></html>')
    assert deck_edit.slides_with_hardcoded_colors(html) == [2, 3]


def test_hardcoded_colors_ignores_comment_text():
    # template comments mention hexes in prose («фон #222222») — not markup
    html = ('<section class="slide"><!-- фон #222222, полностью непрозрачный -->'
            '<p>A</p></section>')
    assert deck_edit.slides_with_hardcoded_colors(html) == []
