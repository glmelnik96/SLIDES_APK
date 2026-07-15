from htmlslides.assembler import assemble
from htmlslides.models import DeckPlan, SlidePlan


def _exact_plan():
    html = ('<div class="content-head"><h2 class="content-head-title t-head-42">'
            'Шапка</h2></div><div class="exact-text">'
            '<p class="t-body-30">дословный текст</p></div>')
    return DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="exact", freeform=True,
                  content={"html": html, "exact": True})])


def test_assemble_exact_marks_and_zone():
    out = assemble(_exact_plan(), theme="dark")
    assert 'data-template="exact"' in out
    assert 'class="exact-zone"' in out
    assert 'class="exact-fit"' in out
    assert "дословный текст" in out
    # шапка поднята на уровень слайда (вне .exact-zone)
    assert '<div class="content-head">' in out


def test_exact_both_themes():
    for theme in ("dark", "light"):
        out = assemble(_exact_plan(), theme=theme)
        assert 'data-template="exact"' in out


def test_freeform_still_freeform():
    plan = DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="content", freeform=True,
                  content={"html": '<div class="exact-text">x</div>'})])
    out = assemble(plan, theme="dark")
    assert 'data-template="freeform"' in out
    assert 'data-template="exact"' not in out


def _assembled_section_html():
    """What the browser editor snapshots into content.html for a green statement
    slide: the full rendered <section>, chrome already baked in, plus the
    statement--accent modifier class that IS the green identity."""
    return (
        '<section class="slide statement--accent" data-template="statement-green">'
        '<div class="chrome-logo" aria-hidden="true"><svg></svg></div>'
        '<div class="chrome-note">© 2026 Cloud.ru</div>'
        '<div class="chrome-num" aria-hidden="true"></div>'
        '<div class="content-head"><h2 class="content-head-title t-head-42">'
        'Заголовок</h2></div>'
        '<div class="statement-plate"><p class="statement-plate-text t-hero-92">'
        'Тезис</p></div>'
        '</section>')


def test_freeform_preassembled_section_keeps_modifier_class():
    """Regression: a freeform slide carrying a fully-assembled section (from an
    inline/chat edit) must keep its modifier class. Before the fix the assembler
    unwrapped the section and dropped statement--accent, so statement-green
    collapsed to a plain statement after reload."""
    plan = DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="content", freeform=True,
                  content={"html": _assembled_section_html()})])
    out = assemble(plan, theme="dark")
    # The bare token "statement--accent" also lives in deck.css, so assert the
    # section's own class attribute survived (that only appears in the markup).
    assert 'class="slide statement--accent"' in out
    assert 'data-template="statement-green"' in out


def test_freeform_preassembled_section_no_double_chrome():
    """The stored section already contains chrome; re-injecting a second chrome
    left two logos stacked. Exactly one chrome-logo must survive."""
    plan = DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="content", freeform=True,
                  content={"html": _assembled_section_html()})])
    out = assemble(plan, theme="dark")
    assert out.count('class="chrome-logo"') == 1
