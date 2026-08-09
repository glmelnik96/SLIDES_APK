"""Регресс геометрии графиков и KPI: подписи не вылезают за границы.

SVG режет всё, что вышло за viewBox, молча — «1 200 млрд» превращалось в
«1 200 мл», и человек читал это как испорченные данные, а не как обрезку.
Ровно так же молча наезжали друг на друга колонки KPI при 4-значной цифре.
"""
import re

from htmlslides.assembler import _text_w, assemble
from htmlslides.models import DeckPlan, SlidePlan

VIEWBOX_W = 1800

_VALUE_TEXT = re.compile(
    r'<text x="([\d.]+)"[^>]*font-weight="500"[^>]*>([^<]*)</text>')


def _one(template_id, content, theme="dark"):
    plan = DeckPlan(title="qa", slides=[SlidePlan(
        index=1, type="content", template_id=template_id, content=content)])
    return assemble(plan, theme=theme)


def _bar_html(bars, theme="dark"):
    return _one("bar-chart", {"title": "T", "bars": bars}, theme)


def _value_labels(html):
    found = _VALUE_TEXT.findall(html)
    assert found, "подписи значений не найдены — изменилась разметка шаблона"
    return [(float(x), text) for x, text in found]


# ---- оценка ширины текста (её же использует bar-chart) ----

def test_textw_is_conservative_per_char_class():
    # замер в Chromium при font-size 30 Medium: цифры 17.2, строчная кириллица
    # 17.5, заглавные 22–31, пробел ~8 — оценка обязана быть НЕ МЕНЬШЕ
    assert _text_w("1234567890") >= 171
    assert _text_w("инфраструк") >= 175
    assert _text_w("ШЩЮЖМШЩЮЖМ") >= 306      # худший случай по заглавным
    assert _text_w("1 200 млрд") >= 159
    # пробел дешевле буквы, заглавная дороже строчной
    assert _text_w("а а") < _text_w("ааа") < _text_w("ААА")
    # кегль масштабирует линейно
    assert _text_w("абв", 15) == _text_w("абв", 30) / 2
    assert _text_w(None) == 0


# ---- bar-chart ----

def test_bar_value_labels_stay_inside_viewbox():
    # display на слотовом капе (10 символов) у самого длинного бара — прежде
    # он уезжал на x=1684 при ширине 158px, то есть за 1800 на 42px
    bars = [
        {"label": "Облачная инфраструктура", "value": "1200", "display": "1 200 млрд"},
        {"label": "Платформенные сервисы", "value": "860", "display": "860 млрд"},
        {"label": "Кибербезопасность", "value": "410", "display": "410 млрд"},
    ]
    for x, text in _value_labels(_bar_html(bars)):
        assert x + _text_w(text) <= VIEWBOX_W, \
            f"подпись {text!r} @ x={x} вылезает за viewBox"


def test_bar_reserves_more_for_capital_letters():
    # буквенная подпись шире цифровой той же длины — резерв должен это учесть
    bars = [{"label": "A", "value": "100", "display": "ШИРОКАЯ"},
            {"label": "B", "value": "40", "display": "40"}]
    for x, text in _value_labels(_bar_html(bars)):
        assert x + _text_w(text) <= VIEWBOX_W


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
        assert x + _text_w(text) <= VIEWBOX_W


# ---- line-chart ----

def _points(n):
    return [{"label": f"IV кв. {2020 + i}", "value": str(10 + i * 7),
             "display": f"{10 + i * 7} млрд ₽"} for i in range(n)]


def _line_anchors(n):
    html = _one("line-chart", {"title": "T", "points": _points(n)})
    svg = re.search(r'<svg class="line-svg.*?</svg>', html, re.S)
    assert svg, "разметка line-chart изменилась"
    return re.findall(r'text-anchor="(\w+)"', svg.group(0))


def test_line_edge_labels_are_anchored_to_the_edge():
    # крайние точки стоят в 80px от края viewBox: центрированная подпись шире
    # 160px уезжала за поле и молча срезалась
    anchors = _line_anchors(4)
    assert anchors[0] == "start" and anchors[-1] == "end", anchors
    assert "middle" in anchors[1:-1], "внутренние подписи должны остаться по центру"


def test_line_single_point_stays_centered():
    # единственная точка стоит в центре плота — центрирование там безопасно
    assert set(_line_anchors(1)) == {"middle"}


# ---- kpi ----

def _kpi_grid_class(*values):
    """Класс .kpi-grid именно из разметки слайда: имена модификаторов есть и в
    инлайновом CSS деки, поиск по всему документу их бы поймал."""
    html = _one("kpi", {"title": "T", "kpis": [
        {"value": v, "unit": "млн ₽", "desc": "Выручка за отчётный период"}
        for v in values]})
    m = re.search(r'<div class="(kpi-grid[^"]*)"', html)
    assert m, "разметка kpi изменилась"
    return m.group(1)


def test_kpi_shrinks_digits_when_value_is_long():
    # 4 знака («9999», слотовый максимум) разводили колонки на 94px перекрытия
    # и уносили вторую пару за правый край слайда
    assert "--tight" not in _kpi_grid_class("98", "150")
    assert "kpi-grid--tight" in _kpi_grid_class("98", "9999")
    # ужимаем по САМОМУ длинному значению — цифры остаются одного размера
    assert _kpi_grid_class("1500", "12").endswith("kpi-grid--tight")
