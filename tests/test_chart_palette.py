"""Регресс палитры графиков: --chart-* определены, мёртвые токены удалены,
шаблоны красят сегменты по индексу без opacity-лесенок."""
import re
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
        (2, "#989898", "#A6A6A6"), (3, "#737373", "#8C8C8C"),
        (4, "#525252", "#737373"), (5, "#5D779F", "#9CADCE"),
        (6, "#8372A1", "#BDB0D2"),
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
    html = assemble(plan, theme=theme)
    # Инлайновые движки (deck.js/diagram.js) содержат свои литералы
    # fill-opacity/var(--accent) — тесты судят только разметку слайдов.
    return re.sub(r"<script>.*?</script>", "", html, flags=re.S)


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


def test_stacked_colors_by_index_and_label_contrast():
    content = {
        "title": "T",
        "legend": [{"label": f"L{i}"} for i in range(4)],
        "bars": [{"label": "B1", "v1": "50", "v2": "30", "v3": "15", "v4": "5"},
                 {"label": "B2", "v1": "40", "v2": "35", "v3": "20", "v4": "5"}]}
    html = _assemble_one("stacked-bar", content)
    # свотчи легенды (4) + сегменты в 2 барах (4×2=8) → chart-1..4
    for n in range(1, 5):
        assert f"var(--chart-{n})" in html
    assert "fill-opacity" not in html
    # логотип _chrome.html красится var(--accent) в КАЖДОМ слайде → остаётся ровно 1;
    # важно, что сегменты/свотчи бара им больше не красятся (было 13: 12 + логотип)
    assert html.count('fill="var(--accent)"') == 1
    # %-метка красится парным к заливке токеном --on-chart-N: фиксированный графит
    # тонул на тёмном хвосте шкалы (#525252 в тёмной теме — контраст 2:1)
    # (трек остаётся var(--bg-card) — это другая строка, её не задеваем)
    for n in range(1, 5):
        assert f'fill="var(--on-chart-{n})"' in html
    assert 'fill="var(--cl-graphite)"' not in html
    assert 'fill="var(--bg)"' not in html


def test_bar_colors_by_index():
    content = {"title": "T", "bars": [
        {"label": f"B{i}", "value": str(v)}
        for i, v in enumerate([86, 72, 54, 40, 28, 16])]}
    html = _assemble_one("bar-chart", content)
    # 6 баров → chart-1..6 (по разу на бар)
    for n in range(1, 7):
        assert f"var(--chart-{n})" in html
    # заливка бара больше не var(--accent) (трек остаётся var(--bg-card));
    # логотип _chrome.html держит ровно 1 var(--accent) в каждом слайде (было 7: 6 + логотип)
    assert html.count('fill="var(--accent)"') == 1
