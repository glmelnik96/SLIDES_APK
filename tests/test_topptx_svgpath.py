"""Разбор <path>, сборка custGeom и кадрирование картинок (topptx.svgshapes).

Зачем отдельные тесты. Точность остальной конвертации сверяется с браузером
(calibrate --check-reflow, fidelity), но обе проверки сравнивают РАМКИ, а внутрь
<svg> не заглядывают вовсе: браузер отдаёт одну рамку на весь блок, а не на
каждый контур. Значит кривая, уехавшая на пол-экрана, прошла бы все проверки
незамеченной. По той же причине сюда попало и кадрирование: object-fit на рамку
не влияет, поэтому растянутую картинку сверка с браузером тоже не поймает.
Единственное, что держит и то и другое, — тесты здесь.

Числа взяты из разметки каталога: логотип cloud.ru (_chrome.html), брендовая
стрелка (_arrow.html), фигура Union обложки (cover-image.html) и её ассет
1000x1057.
"""
import math
import os

os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from htmlslides.topptx import svgshapes
from htmlslides.topptx.svgshapes import parse_path


# ------------------------------------------------------------------- разбор
def test_icon_of_the_logo_is_straight_lines_only():
    """Иконка логотипа: H/V/L в абсолютных координатах и Z. Числа из _chrome.html."""
    segs, bad = parse_path(
        "M26.8186 30.9694H49.9737V45.7158L26.8144 55.4652V30.9652"
        "L26.8186 30.9694Z")
    assert bad == []
    assert segs[0] == ("M", 26.8186, 30.9694)
    # H достраивает y из текущей точки, V достраивает x — иначе контур уехал бы
    assert segs[1] == ("L", 49.9737, 30.9694)
    assert segs[2] == ("L", 49.9737, 45.7158)
    assert segs[-1] == ("Z",)


def test_union_of_the_cover_closes_into_one_contour():
    """Фигура обложки: 8 углов, один подпуть. Число сегментов — защита от обрыва."""
    segs, bad = parse_path("M1020 722H960V1080H0V360H60V0H1020V722Z")
    assert bad == []
    assert [s[0] for s in segs] == ["M"] + ["L"] * 8 + ["Z"]


def test_brand_arrow_stays_open():
    """Стрелка-уголок: два незамкнутых штриха, Z в разметке нет.

    Это важно не формально: незамкнутый контур нельзя заливать, иначе «дырка»
    между штрихами станет сплошным треугольником (см. fill="none" в _do_path).
    """
    segs, _ = parse_path("M0.353553 0.5H120.131V120.278")
    assert ("Z",) not in segs
    assert segs == [("M", 0.353553, 0.5), ("L", 120.131, 0.5),
                    ("L", 120.131, 120.278)]


def test_relative_commands_accumulate_from_the_current_point():
    segs, bad = parse_path("m10 10 l5 0 5 5z")
    assert bad == []
    assert segs == [("M", 10.0, 10.0), ("L", 15.0, 10.0), ("L", 20.0, 15.0),
                    ("Z",)]


def test_extra_pairs_after_moveto_are_implicit_linetos():
    """SVG разрешает `M x y x y x y`: лишние пары — это L, а не новые M.

    Прочитав их как M, мы не оборвали бы фигуру заметно — она просто соединила
    бы не те точки, и это видно только глазами.
    """
    segs, _ = parse_path("M0 0 10 20 30 40")
    assert segs == [("M", 0.0, 0.0), ("L", 10.0, 20.0), ("L", 30.0, 40.0)]


def test_repeated_curve_without_a_letter_is_a_second_curve():
    segs, _ = parse_path("M0 0C1 1 2 2 3 3 4 4 5 5 6 6")
    assert segs == [("M", 0.0, 0.0), ("C", 1.0, 1.0, 2.0, 2.0, 3.0, 3.0),
                    ("C", 4.0, 4.0, 5.0, 5.0, 6.0, 6.0)]


def test_smooth_curve_reflects_the_previous_control_point():
    """S достраивает первую контрольную точку отражением предыдущей.

    Предыдущая вторая контрольная (2,2) относительно текущей точки (3,3) даёт
    (4,4). Без отражения кривая в месте стыка ломается.
    """
    segs, _ = parse_path("M0 0C1 1 2 2 3 3S4 4 5 5")
    assert segs[-1] == ("C", 4.0, 4.0, 4.0, 4.0, 5.0, 5.0)


def test_smooth_curve_without_a_predecessor_starts_at_the_current_point():
    """По спецификации: если перед S не было C/S, отражать нечего."""
    segs, _ = parse_path("M3 3S4 4 5 5")
    assert segs[-1] == ("C", 3.0, 3.0, 4.0, 4.0, 5.0, 5.0)


def test_quadratic_is_raised_to_cubic_exactly():
    """Q -> C подъёмом степени: c1 = p0 + 2/3(q-p0), c2 = p1 + 2/3(q-p1).

    Это не приближение — квадратичная и полученная кубическая Безье являются
    одной и той же кривой. Приближение по точкам дало бы заметный промах на
    крупных радиусах.
    """
    segs, _ = parse_path("M0 0Q6 0 6 6")
    assert segs[-1] == ("C", 4.0, 0.0, 6.0, 2.0, 6.0, 6.0)


def test_shorthand_quadratic_reflects_its_own_control_point():
    segs, _ = parse_path("M0 0Q6 0 6 6T12 12")
    assert segs[-1] == ("C", 6.0, 10.0, 8.0, 12.0, 12.0, 12.0)


def test_closepath_returns_to_the_start_of_the_subpath():
    """После Z текущая точка — начало ПОДПУТИ, а не начало всего пути."""
    segs, _ = parse_path("M10 10L20 10ZM50 50L60 50Zl0 5")
    assert segs[-1] == ("L", 50.0, 55.0)       # от (50,50), не от (10,10)


# ---------------------------------------------------------------------- дуги
def _walk(segs, steps: int = 40) -> list[tuple[float, float]]:
    """Сегменты -> точки на самой кривой. Нужно, чтобы мерить ТОЧНОСТЬ дуги.

    Сравнивать контрольные точки с эталоном тут нельзя: та же дуга
    раскладывается на кубики не единственным способом, и тест сломался бы от
    любой смены числа кусков. А вот расстояние до окружности — свойство самой
    кривой, и его проверка не зависит от способа раскладки.
    """
    out: list[tuple[float, float]] = []
    cur = (0.0, 0.0)
    for s in segs:
        if s[0] in ("M", "L"):
            cur = (s[1], s[2])
            out.append(cur)
            continue
        if s[0] != "C":
            continue
        p = (cur, s[1:3], s[3:5], s[5:7])
        for i in range(1, steps + 1):
            t, u = i / steps, 1 - i / steps
            out.append(tuple(
                u ** 3 * p[0][k] + 3 * u * u * t * p[1][k]
                + 3 * u * t * t * p[2][k] + t ** 3 * p[3][k] for k in range(2)))
        cur = s[5:7]
    return out


def test_an_arc_lands_exactly_where_the_markup_says():
    """Концы дуги — единственное, что кубики передают ТОЧНО, и они обязаны.

    Чуть неверная кривизна внутри незаметна, а разъехавшийся конец рвёт контур:
    следующая команда считает от текущей точки, и остаток фигуры уезжает весь.
    """
    segs, bad = parse_path("M0 0A5 5 0 0 1 10 0L20 20")
    assert bad == []
    assert segs[0] == ("M", 0.0, 0.0)
    assert segs[-1] == ("L", 20.0, 20.0)       # разбор не оборвался на дуге
    assert abs(segs[-2][-2] - 10.0) < 1e-9 and abs(segs[-2][-1]) < 1e-9


# Предел отклонения кубиков от настоящей окружности, в ДОЛЯХ радиуса. Ошибка
# Безье пропорциональна радиусу, поэтому предел и задан относительным: куском
# в 90° (худший случай после дробления) намеряно 2.7e-4·r, здесь с запасом.
# В масштабе деки это 0.3 px на радиусе 1000 px — такого не видно.
_ARC_TOL = 3e-4


def test_a_half_circle_arc_stays_on_its_circle():
    """Кубики идут по окружности, а не рядом с ней.

    Безье не описывает эллипс точно, поэтому точность тут не формальность:
    дуга крупнее 90° режется на куски именно затем, чтобы ошибка осталась
    в этих пределах. Если дробление отключить, тест это поймает.
    """
    pts = _walk(parse_path("M0 0A5 5 0 0 1 10 0")[0])
    dev = max(abs(math.hypot(x - 5.0, y) - 5.0) for x, y in pts)
    assert dev < _ARC_TOL * 5.0


def test_the_sweep_flag_decides_which_way_the_arc_bulges():
    """Два варианта дуги через те же концы, и перепутать их нельзя.

    Перепутанный флаг даёт зеркальную фигуру с правильной рамкой: сверка с
    браузером мерит рамки и такого не поймает (см. шапку файла).
    """
    up = _walk(parse_path("M0 0A5 5 0 0 1 10 0")[0])
    down = _walk(parse_path("M0 0A5 5 0 0 0 10 0")[0])
    assert all(y <= 1e-9 for _, y in up)       # sweep=1 — вверх, y отрицателен
    assert all(y >= -1e-9 for _, y in down)


def test_the_large_arc_flag_picks_the_long_way_round():
    """large-arc=1 — больший из двух кусков окружности, и он правда больше."""
    short = _walk(parse_path("M0 0A5 5 0 0 1 6 0")[0])
    long_ = _walk(parse_path("M0 0A5 5 0 1 1 6 0")[0])
    assert max(abs(y) for _, y in long_) > max(abs(y) for _, y in short)
    for pts in (short, long_):
        assert abs(pts[-1][0] - 6.0) < 1e-9 and abs(pts[-1][1]) < 1e-9


def test_relative_arcs_of_a_timeline_cloud_are_all_parsed():
    """Облако из генерируемых дек: оно нарисовано дугами ЦЕЛИКОМ.

    Это не выдуманный путь — такие приходят от модели в сгенерированных деках.
    Без разбора дуг от фигуры оставалась одна точка M, и исчезала она молча.
    """
    segs, bad = parse_path(
        "M150 250 a55 55 0 0 1 55 -55 a70 70 0 0 1 130 10 "
        "a45 45 0 0 1 15 88 H175 a45 45 0 0 1 -25 -43 z")
    assert bad == []
    # пять дуг подряд, прямая от H и замыкание — ни одна команда не потерялась
    assert [s[0] for s in segs] == ["M"] + ["C"] * 5 + ["L", "C", "Z"]
    assert abs(segs[1][-2] - 205.0) < 1e-9     # первая дуга: от (150,250) на +55/-55
    assert abs(segs[1][-1] - 195.0) < 1e-9


def test_an_arc_with_a_zero_radius_becomes_a_straight_line():
    """Нулевой радиус — не ошибка разбора, спецификация велит прямую."""
    segs, bad = parse_path("M0 0A0 5 0 0 1 10 10")
    assert bad == []
    assert segs == [("M", 0.0, 0.0), ("L", 10.0, 10.0)]


def test_an_arc_that_returns_to_its_own_start_is_dropped():
    """Дуга в ту же точку по спецификации пропускается целиком.

    Центр для неё не определён: делить пришлось бы на ноль.
    """
    assert parse_path("M0 0A5 5 0 0 1 0 0") == ([("M", 0.0, 0.0)], [])


def test_radii_too_small_for_the_distance_are_scaled_up():
    """r=5 на 100 px врозь — эллипс не достаёт, и его растягивают, а не рвут.

    Иначе корень из отрицательного числа: без этой поправки тут падало бы
    исключение посреди конвертации всей деки.
    """
    segs, bad = parse_path("M0 0A5 5 0 0 1 100 0")
    assert bad == []
    assert abs(segs[-1][-2] - 100.0) < 1e-9 and abs(segs[-1][-1]) < 1e-9
    # растянули ровно до половины расстояния — получилась полуокружность r=50
    pts = _walk(segs)
    dev = max(abs(math.hypot(x - 50.0, y) - 50.0) for x, y in pts)
    assert dev < _ARC_TOL * 50.0


# ------------------------------------------- чего не умеем — видно, а не молча
def test_an_unknown_letter_does_not_become_an_implicit_lineto():
    """Любая буква ловится токенайзером, не только известная команда.

    Иначе лишний символ выпал бы из разбора, а его числа дочитались бы как
    аргументы предыдущей команды — контур поехал бы молча.
    """
    segs, bad = parse_path("M0 0X9 9")
    assert bad == ["X"]
    assert segs == [("M", 0.0, 0.0)]


def test_exponent_notation_survives_the_letter_branch():
    """«1e-5» не должно распасться на 1, e и -5: ветвь числа проверяется первой."""
    segs, bad = parse_path("M0 0L1e-5 2E+1")
    assert bad == []
    assert segs[-1] == ("L", 1e-5, 20.0)


def test_truncated_arguments_do_not_invent_coordinates():
    """Недостающие числа не достраиваются нулями — и обрыв попадает в отчёт.

    На обрыве кончается ВЕСЬ остаток пути, поэтому имя команды обязано быть
    в bad: иначе фигура молча приехала бы короче, чем нарисована в разметке.
    """
    segs, bad = parse_path("M0 0C1 1 2 2")
    assert segs == [("M", 0.0, 0.0)]
    assert bad == ["C"]


def test_numbers_before_any_command_are_ignored():
    assert parse_path("5 5L10 10") == ([], [])
    assert parse_path("") == ([], [])
    assert parse_path(None) == ([], [])


# -------------------------------------------------------------- преобразования
def test_rotation_is_applied_to_the_control_points():
    """Поворот кривой = поворот её контрольных точек (аффинная инвариантность).

    Поэтому rotate() из <g> у path обрабатывается точно, а не поворотом рамки
    готовой фигуры, как у кругов.
    """
    x, y = svgshapes._affine(10.0, 0.0, 0.0, 0.0, (90.0, 0.0, 0.0))
    assert abs(x - 0.0) < 1e-9 and abs(y - 10.0) < 1e-9


def test_translate_shifts_the_centre_of_rotation_too():
    """rotate(a cx cy) задан в системе ДО translate, значит центр тоже едет.

    Если сдвинуть точку, но не центр, фигура повернётся вокруг чужой оси и
    уедет на двойное расстояние.
    """
    x, y = svgshapes._affine(1.0, 0.0, 100.0, 0.0, (180.0, 1.0, 0.0))
    assert abs(x - 101.0) < 1e-9 and abs(y - 0.0) < 1e-9


def test_definition_tags_are_not_counted_as_unsupported():
    """clipPath/defs ничего не рисуют: в «не перенесено» им не место.

    Имена в нижнем регистре, потому что деку разбирает HTML-парсер lxml
    (та же причина, по которой viewBox читается через attr()).
    """
    assert "clippath" in svgshapes.DEFS
    assert "defs" in svgshapes.DEFS
    assert svgshapes.DEFS.isdisjoint(svgshapes.SUPPORTED)


# ------------------------------------------------------- сборка фигуры целиком
def _one_svg(markup: str, box=None, color=None, cssvars=None, clip=None):
    """Положить разметку <svg> на пустой слайд. -> (слайд, placer).

    color — то, что в бою приходит из таблицы геометрии: вычисленный
    браузером CSS-color контейнера <svg>. cssvars — переменные темы слайда,
    clip — многоугольник clip-path родителя в слайдовых пикселях (то, что в
    бою отдаёт parse_clip_polygon от рамки родителя из таблицы).
    """
    from lxml import html as LH
    from pptx import Presentation
    from pptx.util import Emu

    prs = Presentation()
    prs.slide_width, prs.slide_height = Emu(1920 * 6350), Emu(1080 * 6350)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    el = LH.fragment_fromstring(markup)
    n, bad = svgshapes.place_svg(slide, el, box or {"x": 0, "y": 0, "w": 100,
                                                    "h": 100}, cssvars or {},
                                 color, clip)
    return slide, n, bad


def test_a_path_becomes_a_shape_with_custom_geometry():
    """Контур уходит в a:custGeom, а не в прямоугольник-заготовку.

    Проверяется и то, что готовая форма (a:prstGeom) ЗАМЕНЕНА, а не осталась
    рядом: две формы в одном a:spPr — файл, который PowerPoint считает битым.
    """
    from pptx.oxml.ns import qn

    slide, n, bad = _one_svg(
        '<svg viewBox="0 0 100 100">'
        '<path d="M0 0C10 0 20 10 20 20Z" fill="#ff0000"/></svg>')
    assert (n, bad) == (1, {})
    spPr = slide.shapes[0]._element.spPr
    assert spPr.find(qn("a:prstGeom")) is None
    geom = spPr.find(qn("a:custGeom"))
    assert geom is not None
    path = geom.find(f'{qn("a:pathLst")}/{qn("a:path")}')
    assert path.get("fill") == "norm"
    assert [c.tag for c in path] == [qn("a:moveTo"), qn("a:cubicBezTo"),
                                     qn("a:close")]
    # Порядок детей a:spPr задан схемой: xfrm, форма, заливка, обводка.
    names = [c.tag for c in spPr]
    assert names.index(qn("a:xfrm")) < names.index(qn("a:custGeom"))


def test_a_stroke_only_path_is_not_filled():
    """У незамкнутого штриха fill="none": иначе PowerPoint сам замкнёт и зальёт."""
    from pptx.oxml.ns import qn

    slide, _, _ = _one_svg(
        '<svg viewBox="0 0 100 100">'
        '<path d="M0 100H100V0" stroke="#000000"/></svg>')
    path = slide.shapes[0]._element.spPr.find(
        f'{qn("a:custGeom")}/{qn("a:pathLst")}/{qn("a:path")}')
    assert path.get("fill") == "none"


# --------------------------------------------------- currentColor у глифов
def test_currentcolor_stroke_takes_the_containers_css_color():
    """stroke="currentColor" красится вычисленным color контейнера <svg>.

    Так устроены все брендовые глифы (_arrow.html, рамки contacts и
    section-frame, линейная графика quote): в их разметке цвета нет вовсе,
    его задаёт класс контейнера в deck.css. До починки такой контур получал
    пустую обводку (<a:ln><a:noFill/>) и был невидим: стрелки statement и
    рамка contacts молча пропадали из экспорта.
    """
    from pptx.dml.color import RGBColor

    slide, n, bad = _one_svg(
        '<svg viewBox="0 0 120.631 120.631" fill="none">'
        '<path d="M0.353553 120.278L108.446 12.1857" stroke="currentColor"/>'
        '<path d="M0.353553 0.5H120.131V120.278" stroke="currentColor"/></svg>',
        color="rgb(38, 208, 124)")
    assert (n, bad) == (2, {})
    for shp in slide.shapes:
        assert shp.line.color.rgb == RGBColor(0x26, 0xD0, 0x7C)


def test_currentcolor_without_a_known_color_stays_unpainted():
    """Нет контекста — нет и выдуманного цвета: обводка остаётся пустой."""
    from pptx.enum.dml import MSO_FILL

    slide, _, _ = _one_svg(
        '<svg viewBox="0 0 10 10">'
        '<path d="M0 0H10" stroke="currentColor"/></svg>')
    assert slide.shapes[0].line.fill.type == MSO_FILL.BACKGROUND


def test_a_color_attribute_inside_the_svg_beats_the_containers():
    """color на предке ВНУТРИ svg ближе цвета контейнера — он и побеждает."""
    from pptx.dml.color import RGBColor

    slide, _, _ = _one_svg(
        '<svg viewBox="0 0 10 10"><g color="#ff0000">'
        '<path d="M0 0H10" stroke="currentColor"/></g></svg>',
        color="rgb(38, 208, 124)")
    assert slide.shapes[0].line.color.rgb == RGBColor(0xFF, 0x00, 0x00)


# ------------------------------------------------- кольцевые диаграммы (дуги)
def test_a_dashed_circle_becomes_a_block_arc_in_degrees():
    """Дуга кольца: углы blockArc — градусы ×60000, adj3 — толщина, не радиус.

    Разметка — настоящий сегмент donut-chart: трек + штриховая окружность
    внутри <g transform="rotate(-90 310 310)">. Семантика пресета (ECMA-376):
    adj1/adj2 — углы начала и конца в 1/60000°, пин 0..21599999 (минус
    обнуляется, поэтому −90° обязан приехать как 270°); python-pptx пишет
    val = adjustment×100000, отсюда ×0.6 в конверторе. adj3 — толщина кольца
    долей короткой стороны. Старый код писал доли оборота ×100000 и
    внутренний радиус: все сегменты вырождались в щепку ~1° у трёх часов
    («график не тот» на donut-chart и kpi-rings).
    """
    import math

    from pptx.oxml.ns import qn

    slide, n, bad = _one_svg(
        '<svg viewBox="0 0 620 620" fill="none">'
        '<circle cx="310" cy="310" r="225" stroke="#333333" stroke-width="40"/>'
        '<g transform="rotate(-90 310 310)">'
        '<circle cx="310" cy="310" r="225" stroke="#26D07C" stroke-width="40"'
        ' stroke-dasharray="694.667 719.05" stroke-dashoffset="-0.0"/>'
        '</g></svg>')
    assert (n, bad) == (2, {})

    def vals(shp):
        geom = shp._element.spPr.find(qn("a:prstGeom"))
        gds = geom.findall(f'{qn("a:avLst")}/{qn("a:gd")}')
        return geom.get("prst"), [int(g.get("fmla").split()[1]) for g in gds]

    track, seg = vals(slide.shapes[0]), vals(slide.shapes[1])

    # Толщина у обоих одна: (1 - 205/245) / 2 от диаметра -> 8163.
    thick = round((1 - 205 / 245) / 2 * 100000)
    assert track == ("donut", [thick])

    prst, (a1, a2, a3) = seg
    assert prst == "blockArc"
    assert a1 == 270 * 60000                      # -90° нормализован в [0,360)
    sweep = 694.667 / (2 * math.pi * 225) * 360.0
    # python-pptx усекает дробную часть val, допуск 1 — это 1/60000 градуса.
    assert abs(a2 - (270 + sweep) % 360.0 * 60000) <= 1
    assert a2 < a1   # разворот через ноль: PowerPoint сам добавит оборот
    assert a3 == thick


def test_a_horizontal_line_gets_a_non_degenerate_box():
    """Рамка нулевой высоты для a:path недопустима: фигура просто не нарисуется."""
    slide, _, _ = _one_svg(
        '<svg viewBox="0 0 100 100">'
        '<path d="M0 50H100" stroke="#000000"/></svg>')
    assert slide.shapes[0].height > 0


# ----------------------------------------------------- кадрирование картинок
# Чистая геометрия, общая для <image preserveAspectRatio> в SVG и <img> с
# object-fit в HTML: это одно и то же под двумя именами. Поэтому формула проверена
# здесь напрямую, а не только через собранный слайд.
def test_fill_stretches_the_source_onto_the_box():
    """object-fit:fill (значение по умолчанию у <img>) — растянуть, обрезки нет."""
    box, crop = svgshapes.fit_image((200, 200), (0, 0, 100, 50), "fill")
    assert box == (0, 0, 100, 50)
    assert crop == (0.0, 0.0, 0.0, 0.0)


def test_cover_keeps_the_box_and_crops_the_source():
    """cover (= slice): масштаб по большей стороне, лишнее в обрезку."""
    box, (left, right, top, bottom) = svgshapes.fit_image(
        (200, 200), (0, 0, 100, 50), "cover")
    assert box == (0, 0, 100, 50)
    assert (left, right) == (0.0, 0.0)
    assert abs(top - 0.25) < 1e-12 and abs(bottom - 0.25) < 1e-12


def test_contain_shrinks_the_box_and_crops_nothing():
    """contain (= meet): рамка по вписанной картинке, обрезки нет.

    Именно рамка здесь и есть проверка: при растяжении обрезка тоже вышла бы
    нулевой (отрицательной её не бывает), так что по одной обрезке contain от
    cover не отличить.
    """
    box, crop = svgshapes.fit_image((200, 200), (0, 0, 100, 50), "contain")
    assert box == (25.0, 0.0, 50.0, 50.0)
    assert crop == (0.0, 0.0, 0.0, 0.0)


def test_none_keeps_the_natural_size_centred():
    """none: натуральный размер, центрирован, вылет за рамку в обрезку."""
    box, (left, right, top, bottom) = svgshapes.fit_image(
        (200, 200), (0, 0, 100, 50), "none")
    assert box == (0, 0, 100, 50)
    assert abs(left - 0.25) < 1e-12 and abs(right - 0.25) < 1e-12
    assert abs(top - 0.375) < 1e-12 and abs(bottom - 0.375) < 1e-12


def test_scale_down_does_not_enlarge_a_small_source():
    """scale-down: как contain, но мелкий исходник не растягивается."""
    box, crop = svgshapes.fit_image((40, 40), (0, 0, 100, 50), "scale-down")
    assert box == (30.0, 5.0, 40.0, 40.0)
    assert crop == (0.0, 0.0, 0.0, 0.0)


def test_rounding_noise_is_not_written_as_a_crop():
    """Доля 1.1e-16 — ноль по смыслу, и в файле её быть не должно.

    Числа настоящие: дежурный ассет .svc-image 640x777 в рамке 614x746. Нижняя
    доля там считается как 1.0 - 0.9999999999999999, то есть для if это
    «истина», и проверка на != 0 писала бы <a:srcRect b="0"/> на пустом месте.
    Порог в set_crop взят по разрядности формата (тысячные процента), поэтому
    тест стоит на том же: round(доля * 100000) == 0.
    """
    from pptx.oxml.ns import qn

    _, crop = svgshapes.fit_image((640, 777), (0.0, 226.0, 614.0, 746.0),
                                  "contain")
    assert 0 < crop[3] < 1e-5 and round(crop[3] * 100000) == 0

    slide, _, _ = _one_svg(
        f'<svg viewBox="0 0 614 746">'
        f'<image href="{_png_uri(640, 777)}" x="0" y="0" width="614" '
        f'height="746"/></svg>',
        box={"x": 0, "y": 226, "w": 614, "h": 746})
    assert slide.shapes[0]._element.find(f'.//{qn("a:srcRect")}') is None


def test_a_degenerate_source_or_box_yields_nothing():
    """Нулевой размер — не делить на ноль и не ставить пустую фигуру."""
    assert svgshapes.fit_image((0, 100), (0, 0, 100, 50), "cover") is None
    assert svgshapes.fit_image((100, 100), (0, 0, 0, 50), "cover") is None


def test_the_cover_of_the_deck_crops_by_the_measured_amount():
    """Числа обложки: ассет 1000x1057 в рамке 1020x1080 с контуром по ней же.

    k = max(1020/1000, 1080/1057) = 1.02176, то есть по ширине вылезает 1.76 px,
    по 0.88 с каждой стороны: 0.88/1021.76 = 0.000861 доли исходника. Именно это
    и лежит в собранном файле как <a:srcRect l="86" r="86"/> (srcRect — в сотых
    процента). Тест стоит на этих числах, потому что обложка — единственное
    место, где обрезка в деке вообще отлична от нуля.
    """
    clip = (900.0, 0.0, 1020.0, 1080.0)
    box, (left, right, top, bottom) = svgshapes.fit_image(
        (1000, 1057), (900.0, 0.0, 1020.0, 1080.0), "cover", clip)
    assert box == clip
    assert abs(left - 0.000861) < 1e-6 and abs(right - 0.000861) < 1e-6
    assert (top, bottom) == (0.0, 0.0)
    assert round(left * 100000) == 86


def _png_uri(w: int, h: int) -> str:
    import base64
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (1, 2, 3)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_a_clipped_image_becomes_a_picture_with_that_contour():
    """Обложка: фотография обрезается контуром, а не запекается в PNG.

    Так требование «редактируемы ВСЕ объекты» выполняется и здесь: в PPTX
    остаётся настоящая картинка, которую можно заменить и перекадрировать, а
    обрезана она тем же контуром, что в SVG, — у p:pic те же spPr, что у фигуры.
    """
    from pptx.oxml.ns import qn

    slide, n, bad = _one_svg(
        f'<svg viewBox="0 0 100 100">'
        f'<clipPath id="c1"><path d="M0 0H100V100H0Z"/></clipPath>'
        f'<image href="{_png_uri(100, 100)}" x="0" y="0" width="100" '
        f'height="100" clip-path="url(#c1)"/></svg>')
    assert (n, bad) == (1, {})
    pic = slide.shapes[0]
    spPr = pic._element.spPr
    assert spPr.find(qn("a:prstGeom")) is None
    assert spPr.find(qn("a:custGeom")) is not None


def test_slice_crops_the_source_to_the_box_aspect():
    """preserveAspectRatio="...slice" — это CSS cover: лишнее уходит за кадр.

    Квадратный исходник в рамке 100x50 обрезается сверху и снизу ровно на
    четверть каждый, и это должно попасть в a:srcRect, а не растянуть картинку.
    """
    slide, _, _ = _one_svg(
        f'<svg viewBox="0 0 100 100">'
        f'<image href="{_png_uri(200, 200)}" x="0" y="0" width="100" '
        f'height="50" preserveAspectRatio="xMidYMid slice"/></svg>')
    pic = slide.shapes[0]
    assert abs(pic.crop_top - 0.25) < 1e-6
    assert abs(pic.crop_bottom - 0.25) < 1e-6
    assert pic.crop_left == pic.crop_right == 0.0


def test_meet_letterboxes_instead_of_cropping():
    """По умолчанию (meet) картинка вписывается целиком, с полями по краям.

    Проверяется именно РАМКА, а не только нулевая обрезка: квадратный исходник в
    рамке 100x50 должен встать как 50x50 по центру, то есть с x=25. Если ставить
    фигуру по рамке <image>, обрезка тоже выйдет нулевой (прообраз уходит в
    минус, а отрицательной обрезки не бывает) — и картинку растянет на все
    100x50. Такое meet молча превращается в slice с перевранными пропорциями,
    и поймать это можно только по рамке.
    """
    slide, _, _ = _one_svg(
        f'<svg viewBox="0 0 100 100">'
        f'<image href="{_png_uri(200, 200)}" x="0" y="0" width="100" '
        f'height="50"/></svg>')
    pic = slide.shapes[0]
    assert (pic.left / 6350, pic.width / 6350) == (25.0, 50.0)
    assert (pic.top / 6350, pic.height / 6350) == (0.0, 50.0)
    assert (pic.crop_left, pic.crop_right) == (0.0, 0.0)
    assert (pic.crop_top, pic.crop_bottom) == (0.0, 0.0)


def test_the_clip_sets_the_picture_box_not_the_image_box():
    """Рамка фигуры — по контуру обрезки, и показан ровно её прообраз.

    Останься рамкой <image>, точки контура ушли бы в минус относительно своей
    фигуры, и PowerPoint обрезал бы их по-своему. Здесь контур — правая
    половина картинки, значит и рамка правая половина, и левая половина
    исходника уходит в srcRect.
    """
    slide, _, _ = _one_svg(
        f'<svg viewBox="0 0 100 100">'
        f'<clipPath id="c2"><path d="M50 0H100V100H50Z"/></clipPath>'
        f'<image href="{_png_uri(100, 100)}" x="0" y="0" width="100" '
        f'height="100" clip-path="url(#c2)"/></svg>',
        box={"x": 0, "y": 0, "w": 100, "h": 100})
    pic = slide.shapes[0]
    assert (pic.left / 6350, pic.width / 6350) == (50.0, 50.0)
    assert abs(pic.crop_left - 0.5) < 1e-6
    assert pic.crop_right == 0.0


def test_the_fit_and_the_clip_cut_the_box_together():
    """Обрезки складываются: сперва вписывание, потом контур.

    Та же картинка 200x200 в рамке 100x50 (meet даёт 50x50 с x=25), но контур
    отрезает правую половину РАМКИ, то есть x от 50. Видно пересечение: x 50..75.
    Рамка фигуры — по нему, а в srcRect уходит левая половина исходника, потому
    что доли считаются от вписанной картинки, а не от рамки <image>.
    """
    slide, _, _ = _one_svg(
        f'<svg viewBox="0 0 100 100">'
        f'<clipPath id="c3"><path d="M50 0H100V50H50Z"/></clipPath>'
        f'<image href="{_png_uri(200, 200)}" x="0" y="0" width="100" '
        f'height="50" clip-path="url(#c3)"/></svg>')
    pic = slide.shapes[0]
    assert (pic.left / 6350, pic.width / 6350) == (50.0, 25.0)
    assert abs(pic.crop_left - 0.5) < 1e-6
    assert pic.crop_right == 0.0


def test_a_contour_beside_the_picture_draws_nothing():
    """Не пересеклись — не рисуем, а не ставим фигуру нулевой/обратной ширины.

    Иначе получилась бы картинка с обрезкой больше единицы: для PowerPoint это
    битый srcRect, а на слайде — мусор на месте, где по разметке пусто.
    """
    slide, _, bad = _one_svg(
        f'<svg viewBox="0 0 100 100">'
        f'<clipPath id="c4"><path d="M0 0H20V50H0Z"/></clipPath>'
        f'<image href="{_png_uri(200, 200)}" x="0" y="0" width="100" '
        f'height="50" clip-path="url(#c4)"/></svg>')
    assert (len(slide.shapes), bad) == (0, {})


def test_a_clip_id_is_looked_up_inside_its_own_svg():
    """Ссылка на несуществующий id не должна молча обрезать по чужому контуру.

    В деке таких блоков несколько, и id обложки собран с номером слайда именно
    поэтому. Не найдя id, остаёмся с необрезанной картинкой — видимая ошибка
    лучше незаметно подмененного контура.
    """
    from pptx.oxml.ns import qn

    slide, _, _ = _one_svg(
        f'<svg viewBox="0 0 100 100">'
        f'<clipPath id="other"><path d="M50 0H100V100H50Z"/></clipPath>'
        f'<image href="{_png_uri(100, 100)}" x="0" y="0" width="100" '
        f'height="100" clip-path="url(#missing)"/></svg>')
    pic = slide.shapes[0]
    assert pic._element.spPr.find(qn("a:custGeom")) is None
    assert (pic.left / 6350, pic.width / 6350) == (0.0, 100.0)


def test_the_shape_box_matches_the_hull_of_the_control_points():
    """Рамка — по оболочке контрольных точек, в слайдовых пикселях.

    Рамка шире настоящих границ кривой, и это сознательно: точки задаются
    ОТНОСИТЕЛЬНО объявленной рамки, поэтому любая рамка, в которую они попадают
    неотрицательными, даёт одинаково точную кривую.
    """
    slide, _, _ = _one_svg(
        '<svg viewBox="0 0 100 100"><path d="M10 20L40 60" fill="#000000"/></svg>',
        box={"x": 500, "y": 300, "w": 100, "h": 100})
    shp = slide.shapes[0]
    assert (shp.left / 6350, shp.top / 6350) == (510.0, 320.0)
    assert (shp.width / 6350, shp.height / 6350) == (30.0, 40.0)


# --------------------------------------- стили из классов и clip-path родителя
def test_stylesheet_classes_paint_the_timeline_lineart():
    """Обводку задают правила deck.css по классам — конвертер обязан их видеть.

    Лайнарт таймлайна (облака + стрелки, figma 3393:11332) — единственная
    графика деки, у чьих контуров в разметке нет НИ атрибутов, ни инлайна:
    краску дают .tl-art-clouds path{...} и .tl-art-arrows path{...}. Таблица
    геометрии внутрь <svg> не заглядывает (браузер отдаёт одну рамку на весь
    svg), поэтому до починки фигуры строились, но выходили без заливки и без
    обводки — невидимые («на 72 и 74 нет иллюстрации»). Цвета защищает только
    этот тест: сверка fidelity сравнивает геометрию и краску не видит.
    """
    from pptx.util import Pt

    slide, n, bad = _one_svg(
        '<svg class="tl-art-svg" viewBox="0 0 1144 898" fill="none">'
        '<g class="tl-art-clouds m-loop-float">'
        '<path d="M100 100C200 100 300 200 300 300"/></g>'
        '<g class="tl-art-arrows"><path d="M100 800L600 300"/></g></svg>',
        box={"x": 0, "y": 0, "w": 1144, "h": 898},
        cssvars={"--cl-white": "#FFFFFF", "--cl-graphite": "#222222"})
    assert (n, bad) == (2, {})
    cloud, arrow = slide.shapes
    assert str(cloud.line.color.rgb) == "FFFFFF"
    assert str(arrow.line.color.rgb) == "222222"
    # stroke-width:2.5px -> 1.25pt (PT_PX), opacity:.85 -> альфа 85000.
    assert cloud.line.width == Pt(1.25)
    assert 'val="85000"' in cloud._element.xml


def test_clip_polygon_cuts_the_arrow_tails():
    """Хвосты за clip-path родителя режутся, как их режет браузер.

    Многоугольник — силуэт панелей .tl-art (deck.css): без обрезки хвосты
    стрелок легли бы диагоналями поверх строк слайда. Вырез в углу делает
    многоугольник НЕвыпуклым — линия через вырез должна распадаться на куски.
    """
    # Квадрат 100×100 с вырезом слева снизу (x<20, y>80).
    notch = [(0, 0), (100, 0), (100, 100), (20, 100), (20, 80), (0, 80)]

    def one(d):
        return _one_svg(
            f'<svg viewBox="0 0 100 100"><path d="{d}" stroke="#222222"/></svg>',
            box={"x": 0, "y": 0, "w": 100, "h": 100}, clip=notch)

    # Линия насквозь: остаётся ровно отрезок внутри квадрата.
    slide, n, bad = one("M-50 50L150 50")
    assert (n, bad) == (1, {})
    shp = slide.shapes[0]
    assert (shp.left, shp.width) == (0, 100 * 6350)

    # Линия через вырез: левый хвост (x<20) отрезан.
    slide, n, bad = one("M-10 90L110 90")
    assert (n, bad) == (1, {})
    shp = slide.shapes[0]
    assert (shp.left, shp.width) == (20 * 6350, 80 * 6350)

    # Целиком в вырезе — фигуры нет вовсе.
    slide, n, bad = one("M5 95L15 95")
    assert (n, bad) == (0, {})

    # Ломаная, у которой вырезана середина: одна фигура, два куска (два M).
    slide, n, bad = one("M10 10L10 95L30 95")
    assert (n, bad) == (1, {})
    xml = slide.shapes[0]._element.xml
    assert xml.count("<a:moveTo") == 2

    # Кривая с точкой за многоугольником не режется, а честно попадает
    # в «не перенесено»: молчаливое враньё хуже заметного пропуска.
    slide, n, bad = one("M50 50C200 50 200 60 50 60")
    assert n == 1
    assert bad == {"clip:curve": 1}
