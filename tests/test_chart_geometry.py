"""Регресс геометрии графиков: подписи не вылезают за viewBox.

SVG режет всё, что вышло за viewBox, молча — «1 200 млрд» превращалось в
«1 200 мл», и человек читал это как испорченные данные, а не как обрезку.
"""
import re

from htmlslides.assembler import assemble
from htmlslides.models import DeckPlan, SlidePlan

# Оценка ширины символа при font-size 30 Medium (замер в Chromium: 15.8–16.5px).
# Тот же коэффициент зашит в bar-chart.html — тест сторожит именно его.
CHAR_W = 17
VIEWBOX_W = 1800

_VALUE_TEXT = re.compile(
    r'<text x="([\d.]+)"[^>]*font-weight="500"[^>]*>([^<]*)</text>')


def _bar_html(bars, theme="dark"):
    plan = DeckPlan(title="qa", slides=[SlidePlan(
        index=1, type="content", template_id="bar-chart",
        content={"title": "T", "bars": bars})])
    return assemble(plan, theme=theme)


def _value_labels(html):
    found = _VALUE_TEXT.findall(html)
    assert found, "подписи значений не найдены — изменилась разметка шаблона"
    return [(float(x), text) for x, text in found]


def test_bar_value_labels_stay_inside_viewbox():
    # display на слотовом капе (10 символов) у самого длинного бара — прежде
    # он уезжал на x=1684 при ширине 158px, то есть за 1800 на 42px
    bars = [
        {"label": "Облачная инфраструктура", "value": "1200", "display": "1 200 млрд"},
        {"label": "Платформенные сервисы", "value": "860", "display": "860 млрд"},
        {"label": "Кибербезопасность", "value": "410", "display": "410 млрд"},
    ]
    for x, text in _value_labels(_bar_html(bars)):
        assert x + len(text) * CHAR_W <= VIEWBOX_W, \
            f"подпись {text!r} @ x={x} вылезает за viewBox"


def test_bar_track_shrinks_only_as_much_as_needed():
    # короткие подписи ограничения не дают → трек остаётся полным (1100)
    bars = [{"label": f"B{i}", "value": v, "display": d} for i, (v, d) in
            enumerate([("90", "90%"), ("60", "60%"), ("30", "30%")])]
    xs = [x for x, _ in _value_labels(_bar_html(bars))]
    assert max(xs) == 560 + 1100 + 24, f"трек ужался без нужды: x={max(xs)}"


def test_bar_zero_values_do_not_break_layout():
    # все значения нулевые → maxv защищён (||1), доли нулевые, ограничений нет
    bars = [{"label": f"B{i}", "value": "0", "display": "0"} for i in range(3)]
    for x, text in _value_labels(_bar_html(bars)):
        assert x + len(text) * CHAR_W <= VIEWBOX_W
