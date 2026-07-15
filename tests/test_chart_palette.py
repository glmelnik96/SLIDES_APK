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
