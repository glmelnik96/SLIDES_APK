"""Регресс палитры графиков: --chart-* определены, мёртвые токены удалены,
шаблоны красят сегменты по индексу без opacity-лесенок."""
from importlib import resources

from htmlslides.assembler import assemble
from htmlslides.models import DeckPlan, SlidePlan


def _deck_css() -> str:
    return (resources.files("htmlslides") / "engine" / "deck.css").read_text("utf-8")


def test_chart_tokens_defined_root_and_themes():
    css = _deck_css()
    # --chart-1 = зелёный в :root (общий для обеих тем)
    assert "--chart-1:var(--cl-green)" in css
    # --chart-2..6 объявлены литералами (по разу в dark и по разу в light → 2)
    for n, dark, light in [
        (2, "#5D779F", "#9CADCE"), (3, "#8372A1", "#BDB0D2"),
        (4, "#989898", "#A6A6A6"), (5, "#737373", "#8C8C8C"),
        (6, "#525252", "#737373"),
    ]:
        assert f"--chart-{n}:{dark}" in css, f"dark --chart-{n} missing"
        assert f"--chart-{n}:{light}" in css, f"light --chart-{n} missing"


def test_dead_tokens_removed():
    css = _deck_css()
    for dead in ["--cl-magenta", "--cl-aqua", "--cl-dk2", "--cl-pastel-mint",
                 "--cl-pastel-yellow", "--cl-pastel-green", "--cl-mid-gray",
                 "--cl-dark-gray"]:
        assert dead not in css, f"{dead} should be removed"


def test_vivid_reserve_and_used_tokens_kept():
    css = _deck_css()
    for keep in ["--cl-ultramarine", "--cl-purple", "--cl-carrot",
                 "--cl-coral", "--cl-yellow", "--cl-blue-lt"]:
        assert keep in css, f"{keep} must stay (reserve/used)"


def _assemble_one(template_id, content, theme="dark"):
    plan = DeckPlan(title="qa", slides=[SlidePlan(
        index=1, type="content", template_id=template_id, content=content)])
    return assemble(plan, theme=theme)


def test_donut_colors_by_index_no_opacity():
    content = {"title": "T", "segments": [
        {"label": f"S{i}", "value": str(v)}
        for i, v in enumerate([86, 72, 54, 40, 22])]}
    html = _assemble_one("donut-chart", content)
    # 5 сегментов → chart-1..5 и на дугах, и на свотчах легенды (по 2 вхождения)
    for n in range(1, 6):
        assert html.count(f"var(--chart-{n})") == 2, f"chart-{n} expected twice"
    # opacity-лесенка снята
    assert "stroke-opacity" not in html
    assert "fill-opacity" not in html
    # accent больше не красит сегменты donut
    assert 'stroke="var(--accent)"' not in html
