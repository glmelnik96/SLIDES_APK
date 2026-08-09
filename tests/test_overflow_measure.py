"""Замер вылета за край: браузерный, поэтому под скипом без chromium.

Ценность именно в реальном браузере: measure_overflow кормит заметками vision-QA,
и ложное «вылезло за край» на здоровом слайде отправляет модель чинить то, что
чинить не нужно (декоративный line-art намеренно уходит за кадр).
"""
import pytest

from htmlslides.assembler import assemble
from htmlslides.models import DeckPlan, SlidePlan
from htmlslides.pipeline.screenshot import QAUnavailable, measure_overflow

_HEALTHY = [
    ("cover", {"title": "Облачная платформа", "subtitle": "Отчёт за квартал"}),
    # оба слайда с декором, который по макету уходит за кадр:
    # .quote-art--clouds (right:-259px) и .tl-art со стрелками
    ("quote", {"quote": "Облако — это не место, а способ работы"}),
    ("timeline", {"title": "План", "items": [
        {"text": "Пилот"}, {"text": "Масштабирование"},
        {"text": "Промышленная эксплуатация"}]}),
    ("section-dots", {"label": "Раздел", "number": "01"}),
    ("section-frame", {"label": "Итоги", "number": "02"}),
    ("contacts", {"title": "Контакты", "subtitle": "sales@example.ru"}),
    ("bar-chart", {"title": "Рынок", "bars": [
        {"label": "Облака", "value": "1200", "display": "1 200"},
        {"label": "Сервисы", "value": "860", "display": "860"}]}),
    ("stats-row", {"title": "В цифрах", "stats": [
        {"value": "99,9%", "label": "Доступность", "caption": "SLA по сервисам"},
        {"value": "3", "label": "Региона", "caption": "Точки присутствия"}]}),
]


@pytest.fixture(scope="module")
def healthy_deck(tmp_path_factory):
    slides = [SlidePlan(index=i + 1, type="content", template_id=tpl, content=c)
              for i, (tpl, c) in enumerate(_HEALTHY)]
    html = assemble(DeckPlan(title="Здоровая дека", slides=slides), theme="dark")
    path = tmp_path_factory.mktemp("ovf") / "deck.html"
    path.write_text(html, "utf-8")
    return path


def test_healthy_deck_reports_nothing(healthy_deck):
    # раньше здоровая дека отдавала 4 «вылета»: quote-art--clouds, tl-art и их
    # внутренние SVG — весь декор помечен aria-hidden и за кадр уходит по макету
    try:
        issues = measure_overflow(healthy_deck)
    except QAUnavailable as exc:
        pytest.skip(str(exc))
    assert issues == [], [f"{i.slide_index} {i.code} {i.detail}" for i in issues]


def test_overflowing_svg_is_named(healthy_deck, tmp_path):
    # у SVG className — SVGAnimatedString, и в заметке для vision-QA стояло
    # «[object SVGAnimatedString]». Все графики — SVG, то есть самое интересное
    # место замера описывалось строкой, из которой ничего не следует.
    rogue = ('<svg class="rogue-chart" style="position:absolute;left:1900px;'
             'top:10px;width:200px;height:50px"><rect width="10" height="10"/></svg>')
    html = healthy_deck.read_text("utf-8").replace("</section>", rogue + "</section>", 1)
    path = tmp_path / "rogue.html"
    path.write_text(html, "utf-8")
    try:
        issues = measure_overflow(path)
    except QAUnavailable as exc:
        pytest.skip(str(exc))
    assert [(i.slide_index, i.code, i.detail) for i in issues] == \
        [(1, "overflow", "rogue-chart")]
