"""Донорская починка промаха: собирается ли слайд, которого нет в таблице.

Проверять тут надо не «вызвался ли repair», а два факта, которые только и имеют
значение: слайд не пропал, и геометрия у него осмысленная. Поэтому тесты идут от
РЕЗУЛЬТАТА — от собранного слайда и от чисел в его строках, — а не от внутренних
структур модуля.

Почему смеси строятся руками, а не берутся из refdeck. Эталонная дека по
определению содержит только то, что калибровка измерила, и промаха по таблице в
ней быть не может. Нужен ровно обратный случай: контент, который контракт
разрешает, а перебор не покрывает. Это и есть «у одного элемента списка
необязательный под-слот есть, у другого нет» — ось, которую нельзя перебрать
(у grid-2x2 это 2^15 деревьев на одну длину ряда).
"""
import os

os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

import lxml.html as LH
import pytest

from htmlslides.topptx import refdeck, repair
from htmlslides.topptx.convert import _Builder, _table, own_text
from htmlslides.topptx.domkey import variant_key, walk_paths
from htmlslides.topptx.reflow import apply as reflow_apply

# Смешанный ряд: у первой ячейки заголовок есть, у второй нет. Такого дерева в
# таблице нет и быть не может — см. docstring модуля.
MIXED = refdeck.Variant("grid-2x2", "смешанный ряд", {
    "title": "Заголовок слайда",
    "row1": [{"heading": "Есть", "text": "Текст первой ячейки"},
             {"text": "Текст второй ячейки"}],
}, "wide")

# Те же два состояния по отдельности — их калибровка как раз перебирает.
ALL_ON = refdeck.Variant("grid-2x2", "все с заголовком", {
    "title": "Заголовок слайда",
    "row1": [{"heading": "Есть", "text": "Текст первой ячейки"},
             {"heading": "Тоже", "text": "Текст второй ячейки"}],
}, "wide")


def _section(variant):
    html = refdeck.build_deck([variant], "dark")
    return LH.document_fromstring(html).xpath("//section[@data-template]")[0]


@pytest.fixture(scope="module")
def table():
    t = _table()
    by_template: dict[str, list[dict]] = {}
    for key, var in t["themes"]["dark"].items():
        by_template.setdefault(key.split("|")[0], []).append(var)
    return t["styles"], by_template


def test_the_mixed_row_really_is_absent_from_the_table():
    """Страховка от «тест проверяет починку там, где её не было».

    Смешайся этот вариант с перебранными — все проверки ниже прошли бы по
    обычному пути и про починку не сказали бы ничего.
    """
    variants = _table()["themes"]["dark"]
    assert variant_key(_section(MIXED)) not in variants
    # А крайний случай в таблице есть, иначе донора взять было бы негде.
    assert variant_key(_section(ALL_ON)) in variants


def test_a_donor_is_found_and_has_exactly_the_absent_element_to_spare(table):
    """Донор — надмножество: лишнее у него есть, недостающего нет.

    Если бы донор мог недоставать элемента, пришлось бы выдумывать геометрию
    того, чего не мерили. Весь смысл отбора в том, что этого не бывает.
    """
    styles, by_template = table
    section = _section(MIXED)
    got = repair.donor(section, by_template["grid-2x2"])
    assert got is not None
    _, mapping, extra = got

    # Каждый элемент слайда сопоставлен — ничего не выдумано.
    mine = {p for p, el in walk_paths(section) if el.tag != "svg"
            and not any(a.tag == "svg" for a in el.iterancestors())}
    assert mine - set(mapping) == set()
    # Лишний ровно один: отсутствующий .g22-name второй ячейки.
    assert len(extra) == 1


def test_the_repaired_rows_cover_the_whole_slide(table):
    """У каждого элемента слайда есть строка геометрии, и ни одной лишней."""
    styles, by_template = table
    section = _section(MIXED)
    rows, _ = repair.rows_for(section, by_template["grid-2x2"], styles)

    paths = {r["p"] for r in rows}
    assert len(paths) == len(rows)            # пути не продублировались
    for path, el in walk_paths(section):
        if el.tag == "svg" or any(a.tag == "svg" for a in el.iterancestors()):
            continue                          # авторские координаты, не наши
        assert path in paths, f"нет геометрии для {path} ({el.tag})"


def test_dropping_a_heading_lifts_what_was_under_it(table):
    """Главное следствие выброса: текст без заголовка встаёт выше.

    Это и есть та ошибка, из-за которой починка без распространения врала на
    79px: строки донора брались как есть, и текст второй ячейки оставался на
    месте, освобождённом удалённым заголовком.
    """
    styles, by_template = table
    section = _section(MIXED)
    rows, _ = repair.rows_for(section, by_template["grid-2x2"], styles)
    reflow_apply(rows, styles,
                 {p: own_text(el) for p, el in walk_paths(section)})
    geo = {r["p"]: r for r in rows}

    def inside(cell: str, cls: str) -> list[dict]:
        return [r for p, r in geo.items() if p.startswith(cell + ".")
                and cls in (r.get("c") or "")]

    cells = sorted((p for p, r in geo.items()
                    if "g22-cell" in (r.get("c") or "")), key=len)
    assert len(cells) == 2
    first, second = cells
    assert len(inside(first, "g22-name")) == 1
    assert len(inside(second, "g22-name")) == 0, "заголовок не выброшен"

    with_head = inside(first, "g22-text")[0]
    without = inside(second, "g22-text")[0]
    # Высота заголовка с зазором — её и должен освободить выброс.
    head_h = inside(first, "g22-name")[0]["h"]
    assert without["y"] < with_head["y"] - head_h / 2, (
        f'текст без заголовка на y={without["y"]}, с заголовком '
        f'{with_head["y"]} при высоте заголовка {head_h} — '
        "выброс не поднял содержимое")


def test_the_slide_is_actually_built_and_counted_as_repaired(table):
    """Сквозная проверка: слайд есть в PPTX, и это видно в статистике."""
    section = _section(MIXED)
    builder = _Builder("dark")
    slide = builder.add_slide(section, 1)

    assert slide is not None, "слайд потерялся — починка не сработала"
    assert builder.stats["missing"] == []
    assert len(builder.stats["repaired"]) == 1
    assert builder.stats["repaired"][0][0] == 1
    # Фигуры действительно расставлены, а не слайд создан пустым.
    assert len(slide.shapes) >= 5


def test_a_measured_variant_never_goes_through_the_repair(table):
    """Что перебрано — берём из таблицы. Починка всегда приближение."""
    builder = _Builder("dark")
    assert builder.add_slide(_section(ALL_ON), 1) is not None
    assert builder.stats["repaired"] == []


def test_a_foreign_template_is_reported_as_a_miss_not_silently_repaired():
    """Донора нет — промах, и он должен быть ВИДЕН, а не замаскирован.

    Починка не имеет права превращаться в «что-нибудь нарисуем»: шаблон вне
    каталога не имеет измеренного надмножества, и единственный честный ответ —
    строка в промахах.
    """
    section = LH.fromstring(
        '<section class="slide" data-template="нет-такого">'
        '<div class="x">текст</div></section>')
    builder = _Builder("dark")
    assert builder.add_slide(section, 7) is None
    assert len(builder.stats["missing"]) == 1
    assert builder.stats["repaired"] == []


# ------------------------------------------------------- псевдоэлементы (::b/::a)
MIXED_3COL = refdeck.Variant("three-col", "смешанные колонки", {
    "title": "Заголовок слайда",
    "columns": [{"heading": "Есть", "text": "Текст первой колонки"},
                {"text": "Только текст, без заголовка"},
                {"heading": "Тоже", "text": "Текст третьей колонки"}],
}, "wide")


def test_pseudo_rows_follow_their_element_through_repair(table):
    """Сепараторы three-col переживают донорскую починку.

    Зелёные линии между колонками — ::before, то есть синтетические строки
    "путь::b" в таблице. В children_of они не входят, значит и в карту
    сопоставления не попадают — rows_for обязан перенести их вслед за своим
    элементом, иначе на смешанном дереве (у одной колонки заголовок есть, у
    другой нет) слайд выйдет без сепараторов — молча.
    """
    styles, by_template = table
    section = _section(MIXED_3COL)
    assert variant_key(section) not in _table()["themes"]["dark"], \
        "смысл теста — именно промах по ключу"
    got = repair.rows_for(section, by_template["three-col"], styles)
    assert got is not None, "донор для смешанного three-col не нашёлся"
    rows, _ = got
    seps = [r for r in rows if r["p"].endswith("::b")]
    # колонок три -> сепаратора два, у второй и третьей
    assert len(seps) == 2
    for r in seps:
        assert r["w"] == 1.0
        assert r["h"] > 600
