"""Модель раскладки из htmlslides.topptx.reflow на маленьких ручных примерах.

Зачем они, если есть calibrate --check-reflow. Та проверка сильнее: она гоняет
поправку на 93 тысячах настоящих рамок и ловит то, чего тут не выдумать. Но она
требует браузера и пяти минут, а ещё отвечает одним числом: «разошлось на
329px». Эти тесты отвечают на другой вопрос — ПОЧЕМУ: каждый закрепляет одно
правило модели отдельно, на трёх-четырёх рамках, где ответ считается в уме.

Каждый тест назван правилом, которое он держит, и каждое из этих правил в своё
время было нарушено и стоило расхождения в check-reflow.
"""
import os

os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from htmlslides.topptx import reflow


def _rows(*specs):
    """specs: (путь, стиль-индекс, x, y, w, h, **extra) -> (rows, by_path, kids)."""
    rows = []
    for path, s, x, y, w, h, *rest in specs:
        row = {"p": path, "t": "div", "s": s, "x": float(x), "y": float(y),
               "w": float(w), "h": float(h)}
        if rest:
            row.update(rest[0])
        rows.append(row)
    by_path = {r["p"]: r for r in rows}
    return rows, by_path, reflow.children_of(by_path)


def _slack(by_path, kids, styles):
    """Свободное место в рядах — ровно как apply(): до первой поправки.

    Отдельной функцией, а не inline, чтобы в тестах нельзя было случайно снять
    его с уже исправленной таблицы: тогда у ряда, который только что растянул
    min-width, «свободного места» окажется столько, сколько мы сами добавили, и
    _rejustify сдвинет детей дважды.
    """
    return reflow.row_slack(by_path, kids, styles)


COL = {"display": "flex", "flexDirection": "column"}
ROW = {"display": "flex", "flexDirection": "row"}
BLOCK = {"display": "block"}


# --------------------------------------------------------- высота по содержимому
def test_column_height_is_sum_of_children_with_gaps():
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 100, 230), ("0.0", 1, 0, 0, 100, 100),
        ("0.1", 1, 0, 130, 100, 100))
    styles = [dict(COL, rowGap="30px"), BLOCK]
    assert reflow.inner_height("0", by, kids, styles) == 230
    assert "0" in reflow.elastic_paths(by, kids, styles)


def test_row_height_is_the_tallest_child_not_the_sum():
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 200, 100), ("0.0", 1, 0, 0, 100, 100),
        ("0.1", 1, 100, 0, 100, 60))
    styles = [ROW, BLOCK]
    assert reflow.inner_height("0", by, kids, styles) == 100


def test_grid_with_children_up_to_column_count_is_one_row():
    """.sr-row — display:grid repeat(4,1fr): его высота равна максимуму.

    Просуммировав ячейки, модель решала, что высота задана раскладкой, и
    распространение останавливалось на ряд ниже, чем нужно.
    """
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 400, 120), ("0.0", 1, 0, 0, 200, 120),
        ("0.1", 1, 200, 0, 200, 80))
    styles = [{"display": "grid",
               "gridTemplateColumns": "200px 200px"}, BLOCK]
    assert reflow.inner_height("0", by, kids, styles) == 120


def test_row_gap_alone_is_not_read_as_zero():
    """.sr-rows задаёт только row-gap; computed gap приходит "56px normal".

    Такую строку парсер молча читал как 0, сумма детей расходилась с замером на
    все 56, и признак «высота по содержимому» переворачивался.
    """
    styles_ok = {"display": "flex", "flexDirection": "column",
                 "rowGap": "56px", "columnGap": "normal"}
    assert reflow._gap(styles_ok, "h") == 56
    assert reflow._gap(styles_ok, "w") == 0


def test_margins_count_towards_the_parents_content_height():
    """.kpi-desc margin-top:41px — без него сумма детей не сходится с замером."""
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 100, 241), ("0.0", 1, 0, 0, 100, 100),
        ("0.1", 2, 0, 141, 100, 100))
    styles = [COL, BLOCK, dict(BLOCK, marginTop="41px")]
    assert reflow.inner_height("0", by, kids, styles) == 241
    assert "0" in reflow.elastic_paths(by, kids, styles)


def test_out_of_flow_child_does_not_count_towards_parent_height():
    """.svc-desc задан absolute: на высоту .service-table он не влияет."""
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 100, 100), ("0.0", 1, 0, 0, 100, 100),
        ("0.1", 2, 0, 0, 100, 400))
    styles = [COL, BLOCK, dict(BLOCK, position="absolute")]
    assert reflow.inner_height("0", by, kids, styles) == 100


# ------------------------------------------------------------ распространение
def test_grown_leaf_pushes_following_siblings_and_raises_the_parent():
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 100, 200), ("0.0", 1, 0, 0, 100, 100),
        ("0.1", 1, 0, 100, 100, 100))
    styles = [COL, BLOCK]
    elastic = reflow.elastic_paths(by, kids, styles)
    by["0.0"]["h"] = 150
    reflow._propagate("0.0", 50, by, kids, styles, elastic)
    assert by["0.1"]["y"] == 150          # сосед уехал вниз
    assert by["0"]["h"] == 250            # родитель вырос вместе с ним


def test_stretched_parent_absorbs_the_change_and_nothing_above_moves():
    """.g22-cell растянут на ряд (flex:1 1 0): перенос внутри не выходит наружу."""
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 100, 659), ("0.0", 1, 0, 0, 100, 100),
        ("0.1", 1, 0, 100, 100, 74))
    styles = [COL, BLOCK]
    elastic = reflow.elastic_paths(by, kids, styles)
    assert "0" not in elastic
    by["0.0"]["h"] = 150
    reflow._propagate("0.0", 50, by, kids, styles, elastic)
    assert by["0.1"]["y"] == 150          # сосед внутри плашки всё равно уехал
    assert by["0"]["h"] == 659            # сама плашка — нет


def test_out_of_flow_leaf_does_not_push_its_siblings():
    """Абсолютный элемент вырос — соседи по потоку стоят на месте."""
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 100, 200), ("0.0", 1, 0, 0, 100, 100),
        ("0.1", 2, 0, 100, 100, 100))
    styles = [COL, dict(BLOCK, position="absolute"), BLOCK]
    elastic = reflow.elastic_paths(by, kids, styles)
    by["0.0"]["h"] = 150
    reflow._propagate("0.0", 50, by, kids, styles, elastic)
    assert by["0.1"]["y"] == 100
    assert by["0"]["h"] == 200


def test_centered_child_of_a_fixed_row_slides_by_half_the_change():
    """.quote-box высотой 1080 с align-items:center.

    Текст укоротился на 120.64 -> рамка едет вниз на 60.32. Высоту ряда это не
    меняет вовсе, и без поперечной выключки сдвиг не находился ничем.
    """
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 1620, 1080), ("0.0", 1, 0, 359.06, 1620, 361.88))
    styles = [dict(ROW, alignItems="center"), BLOCK]
    elastic = reflow.elastic_paths(by, kids, styles)
    assert "0" not in elastic                     # высота прибита, 1080
    by["0.0"]["h"] = 241.24
    reflow._propagate("0.0", -120.64, by, kids, styles, elastic)
    assert abs(by["0.0"]["y"] - 419.38) < 0.02


def test_cross_axis_alignment_reads_align_self_over_align_items():
    assert reflow._cross_k({"alignSelf": "center"},
                           {"alignItems": "flex-start"}) == 0.5
    assert reflow._cross_k({"alignSelf": "auto"},
                           {"alignItems": "flex-end"}) == 1.0
    assert reflow._cross_k({}, {"alignItems": "center"}) == 0.5
    # stretch и baseline свободной высоты не оставляют / не моделируются
    assert reflow._cross_k({}, {"alignItems": "stretch"}) == 0.0
    assert reflow._cross_k({}, {"alignItems": "baseline"}) == 0.0


def test_subtree_moves_with_the_element_it_belongs_to():
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 100, 100), ("0.0", 0, 10, 10, 50, 50),
        ("0.0.0", 0, 20, 20, 10, 10))
    reflow._shift("0", 7, by, kids)
    assert [by[p]["y"] for p in ("0", "0.0", "0.0.0")] == [7, 17, 27]


# ----------------------------------------------------------------- ширина
def test_fit_content_is_clamped_by_max_width_then_raised_by_min_width():
    """У .tcc-tab width:fit-content с min-width:299px и max-width:869px.

    Порядок именно такой: min побеждает max. Без пределов подгонка под текст
    давала 186 вместо 299.
    """
    st = {"minWidth": "299px", "maxWidth": "869px", "boxSizing": "border-box"}
    assert reflow._clamp_w(st, 186, 2000) == 299      # min поднял
    assert reflow._clamp_w(st, 500, 2000) == 500      # в пределах
    assert reflow._clamp_w(st, 1200, 2000) == 869     # max урезал
    assert reflow._clamp_w(st, 1200, 700) == 700      # полоса уже предела


def test_fit_x_keeps_the_measured_edge_not_the_band():
    """Выключка считается ОТНОСИТЕЛЬНО замеренной рамки, а не вычисленной полосы."""
    assert reflow._fit_x(100, 400, 200, "l") == 100            # левый край
    assert reflow._fit_x(100, 400, 200, "c") == 200            # центр 300
    assert reflow._fit_x(100, 400, 200, "r") == 300            # правый край 500


def test_narrowed_flex_item_shifts_the_items_after_it():
    """.kpi-value сузился -> .kpi-side, .kpi-unit и .kpi-desc едут влево.

    Это разностная модель: собственная ширина родителя, зазоры и поля в разнице
    двух состояний сокращаются, поэтому считать их не нужно.
    """
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 900, 100), ("0.0", 1, 0, 0, 500, 100),
        ("0.1", 1, 540, 0, 200, 100))
    styles = [dict(ROW, columnGap="40px"), BLOCK]
    reflow._resize("0.0", 300, by, kids, styles, _slack(by, kids, styles))
    assert by["0.0"]["w"] == 300
    assert by["0.1"]["x"] == 340          # уехал ровно на -200


def test_a_flex_grow_neighbour_absorbs_the_freed_space_into_its_own_box():
    """Сосед с flex-grow:1 забирает освободившееся место — и рамкой тоже.

    Правый край ряда стоит на месте: левый край соседа уезжает вместе с
    сужением, а ширина на столько же растёт. Без второй половины фигура в PPTX
    получала бы новый x при старой ширине.
    """
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 700, 100), ("0.0", 1, 0, 0, 500, 100),
        ("0.1", 2, 500, 0, 200, 100))
    styles = [ROW, BLOCK, dict(BLOCK, flexGrow="1")]
    reflow._resize("0.0", 300, by, kids, styles, _slack(by, kids, styles))
    assert by["0.1"]["x"] == 300
    assert by["0.1"]["w"] == 400
    assert by["0.1"]["x"] + by["0.1"]["w"] == 700        # правый край не поехал


def test_min_width_on_a_hugging_row_recenters_its_own_child():
    """.contacts-subtitle-wrap в светлой теме: min-width:720px, padding:0 24px.

    Числа настоящие, из замера. На рыбе ряд обнимает текст (1115.8), на коротком
    реальном — min-width поднимает его до 720, и ребёнок шириной 552 обязан
    встать по центру 672 пикселей content-box: 84 + (672-552)/2 = 144.

    Выключка `al` этого знать не может — она снята с двух замеров, где ряд в
    обоих обнимал текст, и честно говорит «левый край на месте». Промах был
    293 px.
    """
    rows, by, kids = _rows(
        ("7", 0, 60, 487, 1115.80, 63), ("7.0", 1, 84, 496.91, 1067.80, 43.19))
    styles = [dict(ROW, justifyContent="center", alignItems="center",
                   paddingLeft="24px", paddingRight="24px",
                   minWidth="720px", boxSizing="border-box"), BLOCK]
    # Свободное место в ряду снимается с НЕИСПОРЧЕННОЙ таблицы, как в apply():
    # здесь оно нулевое, потому что по замеру ряд ровно обнимал рыбу.
    slack = _slack(by, kids, styles)
    assert slack["7"] == 0
    # так это и происходит в apply(): сначала ребёнок до своего max-content...
    reflow._resize("7.0", 551.99, by, kids, styles, slack)
    # ...потом родитель до своего содержимого, упёршись в min-width
    reflow._resize("7", reflow._clamp_w(styles[0],
                                        reflow._inner_width("7", by, kids,
                                                            styles), 1872.0),
                   by, kids, styles, slack)
    assert by["7"]["w"] == 720
    assert abs(by["7.0"]["x"] - 144.00) < 0.02
    assert abs(by["7.0"]["w"] - 552.00) < 0.02


def test_centered_row_splits_the_freed_space_on_both_sides():
    """justify-content:center: при сужении соседи сдвигаются на половину.

    Свою координату сузившаяся рамка берёт из выключки `al` — она снята с двух
    замеров браузера. Модель ряда отвечает только за соседей, и здесь обе
    половины обязаны дать один ответ: 300->200 при центровке ряда шириной 900
    ставит рамку на 200, а соседа сдвигает с 450 на 400.
    """
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 900, 100), ("0.0", 1, 150, 0, 300, 100, {"al": "c"}),
        ("0.1", 1, 450, 0, 300, 100))
    styles = [dict(ROW, justifyContent="center"), BLOCK]
    reflow._resize("0.0", 200, by, kids, styles, _slack(by, kids, styles))
    assert by["0.0"]["x"] == 200           # выключка: центр рамки на месте
    assert by["0.1"]["x"] == 400           # половина от освободившихся 100


def test_a_flex_grow_child_makes_the_sum_exact_so_the_sum_proves_nothing():
    """Контейнер, остаток которого поглощает ребёнок, упругим считать нельзя.

    У .tcc-card высота 720 задана снаружи (height:100%), а сумма детей равна
    ровно 720 — потому что .tcc-body взял flex:1 и забрал остаток. Признак
    «сумма сошлась» тут срабатывает ПО ПОСТРОЕНИЮ при любой высоте родителя, и
    донорская починка из-за этого сжимала карточку на 192px.
    """
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 100, 720), ("0.0", 1, 0, 0, 100, 60),
        ("0.1", 2, 0, 60, 100, 660))
    styles = [COL, BLOCK, dict(BLOCK, flexGrow="1")]
    elastic = reflow.elastic_paths(by, kids, styles)

    assert reflow.inner_height("0", by, kids, styles) == 720   # сумма сошлась
    assert "0" not in elastic, "сумма сошлась из-за flex:1, а не по содержимому"
    assert "0.1" not in elastic, "остаток поглощает сам элемент"


def test_a_genuinely_content_driven_column_is_still_elastic():
    """Страховка от перестраховки: обычная колонка упругой быть обязана.

    Без этого предыдущая правка «починила» бы признак, запретив ему
    срабатывать вообще, и поправка на перенос перестала бы доходить наверх.
    """
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 100, 200), ("0.0", 1, 0, 0, 100, 100),
        ("0.1", 1, 0, 100, 100, 100))
    assert "0" in reflow.elastic_paths(by, kids, [COL, BLOCK])


def test_a_child_flush_with_the_bottom_edge_is_held_there_not_by_its_neighbour():
    """margin-top:auto: что выше укоротилось — остаток вырос, элемент на месте.

    По стилю это не отличить: getComputedStyle отдаёт автополе разрешённым
    (у .tcc-arrow — '412px'), поэтому признак берётся из замера. Рамки те же,
    что на слайде: тело 660 с нижним padding 36, стрелка 140 кончается ровно
    на кромке содержимого.
    """
    rows, by, kids = _rows(
        ("0", 0, 0, 360, 100, 660), ("0.0", 1, 0, 396, 100, 36),
        ("0.1", 1, 0, 844, 100, 140, {"mt": 1}))
    styles = [dict(COL, flexGrow="1", paddingBottom="36px"), BLOCK]
    elastic = reflow.elastic_paths(by, kids, styles)

    assert "0" not in elastic                     # высота навязана снаружи
    assert reflow._pinned_low("0.1", "0", by, styles, elastic)
    assert not reflow._pinned_low("0.0", "0", by, styles, elastic)

    # Текст над стрелкой выброшен: стрелка стоит, тело не сжалось.
    reflow.drop("0.0", by, kids, styles, elastic)
    assert by["0.1"]["y"] == 844, "прижатую к низу стрелку подняли за соседом"
    assert by["0"]["h"] == 660, "тело сжалось, хотя его высота задана снаружи"


def test_a_bottom_pinned_child_of_an_elastic_column_does_move():
    """В колонке ПО СОДЕРЖИМОМУ дети у кромки просто потому, что места ровно.

    Поэтому признак и проверяет elastic: иначе любой последний ребёнок любой
    колонки оказался бы «прижатым» и перестал бы двигаться вовсе.
    """
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 100, 200), ("0.0", 1, 0, 0, 100, 100),
        ("0.1", 1, 0, 100, 100, 100))
    styles = [COL, BLOCK]
    elastic = reflow.elastic_paths(by, kids, styles)

    assert "0" in elastic
    assert not reflow._pinned_low("0.1", "0", by, styles, elastic)
    reflow.drop("0.0", by, kids, styles, elastic)
    assert by["0.1"]["y"] == 0             # поднялся на освободившееся место
    assert by["0"]["h"] == 100             # и колонка укоротилась вместе с ним


# ------------------------------------------------------- псевдоэлементы (::b/::a)
def test_pseudo_rows_stay_out_of_the_flow_tree():
    """Синтетическая строка "путь::b" — не ребёнок и не участник потока.

    Калибровка синтезирует строки для декоративных ::before/::after (сепараторы
    three-col, замыкающая линия timeline). Они всегда position:absolute, то есть
    на раскладку не влияют, а int("0::b") в сортировке детей просто упал бы —
    поэтому children_of обязан их пропускать целиком.
    """
    rows, by, kids = _rows(
        ("0", 0, 0, 0, 100, 100), ("0.0", 1, 0, 0, 50, 100),
        ("0.0::b", 1, 45, 2, 1, 96))
    assert kids[""] == ["0"]
    assert kids["0"] == ["0.0"]
    assert all("::" not in p for group in kids.values() for p in group)
