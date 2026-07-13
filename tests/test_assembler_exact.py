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
