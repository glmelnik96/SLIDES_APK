"""Инлайн-SVG графиков -> нативные фигуры PPTX.

Почему вообще можно: внутри <svg> координаты посчитал не браузер, а Jinja —
в разметке лежат готовые числа в системе viewBox (см. блоки «Геометрия» в
шапках bar-chart.html, stacked-bar.html, kpi-rings.html). Значит график
разбирается из HTML через lxml точно, без калибровки и без браузера, а от
калибровки нужна ровно одна величина — CSS-рамка самого <svg>.

Почему фигурами, а не нативным графиком PowerPoint: требование «максимальная
точность» и требование «всё редактируемо» здесь не конфликтуют только у фигур.
Нативный chart в PPTX принесёт свой движок раскладки — свои отступы, свою
легенду, свои подписи — и картинка поедет. Прямоугольник остаётся
прямоугольником: его можно таскать, перекрашивать и удалять, а выглядит он
ровно как в деке. Цена решения — связь «бар↔число» теряется: правка цифры в
подписи не пересчитает длину бара.

Дуги. Кольца (donut-chart, kpi-rings) нарисованы не дугами, а обводкой
окружности с stroke-dasharray/offset: видимый кусок штриха и есть сегмент. В
PPTX это blockArc — залитый сектор кольца с внутренним радиусом, то есть
геометрически то же самое, что обводка толщиной stroke-width. Отсчёт углов
совпадает (от 3 часов по часовой), а rotate(-90) из разметки переносится
сдвигом начального угла.

Кривые. Брендовая декорация (логотип cloud.ru, стрелка-уголок, рамки секции и
контактов, арт таймлайна и обложки) нарисована <path>. Напрашивалось растрировать
такие <svg> целиком — но это противоречит требованию «редактируемы ВСЕ объекты»
и означало бы тридцать с лишним PNG в репозитории плюс браузер для их съёмки.
Этого не нужно: у DrawingML есть custGeom с a:cubicBezTo, то есть ровно те же
кубические кривые Безье, что и в SVG, с теми же контрольными точками. Фигура
получается точной до округления в EMU и при этом настоящей фигурой — её можно
двигать, перекрашивать и править по точкам. Поэтому path переносится нативно, а
растрирования в конвертере нет вовсе.

Чего это стоило проверить: в каталоге шаблонов в путях встречаются только M, L,
H, V, C и Z, все в абсолютных координатах. Парсер понимает больше (относительные
команды, S, Q, T — последние две переводятся в кубические точно, по формуле
подъёма степени), и это не задел «на будущее», а защита: если шаблон однажды
нарисуют иначе, фигура останется верной, а неподдержанная команда попадёт в
статистику с именем, вместо того чтобы молча оборвать контур.

Дуги в путях (команда A) понадобились не в шаблонах, а в деках: облака
таймлайна в сгенерированных презентациях нарисованы ОДНИМИ дугами, и пока
разбор на них обрывался, от фигуры оставалась одна точка — двенадцать путей
на деку исчезали целиком. Эллипс кривой Безье не описывается точно, поэтому
дуга режется на куски по 90° (см. _arc): на таком куске расхождение порядка
0.0003 радиуса, то есть сотые доли пикселя — меньше округления в EMU.

Правило заливки у составного контура не переносится: в SVG по умолчанию nonzero,
в DrawingML его вообще нельзя задать. На нашей графике это ничего не меняет —
внутренние контуры букв логотипа и вложены, и намотаны в обратную сторону,
поэтому nonzero и even-odd дают одинаковые дырки. Пара контуров одного
направления, наложенных друг на друга, разошлась бы — в каталоге такой нет.
"""
from __future__ import annotations

import io
import math
import re
from functools import lru_cache

from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Emu, Pt

from .pptxshell import set_alpha

EMU_PX = 6350
PT_PX = 0.5

_NUM = r"-?[\d.]+(?:e-?\d+)?"
_VAR_RE = re.compile(r"var\(\s*(--[a-z0-9-]+)\s*(?:,\s*([^)]+))?\)", re.I)
_ROTATE_RE = re.compile(rf"rotate\(\s*({_NUM})(?:[,\s]+({_NUM})[,\s]+({_NUM}))?\s*\)")
_TRANSLATE_RE = re.compile(rf"translate\(\s*({_NUM})(?:[,\s]+({_NUM}))?\s*\)")
_HEX_RE = re.compile(r"^#([0-9a-f]{3}|[0-9a-f]{6})$", re.I)
_RGB_RE = re.compile(rf"rgba?\(\s*({_NUM})[,\s]+({_NUM})[,\s]+({_NUM})"
                     rf"(?:[,/\s]+({_NUM}))?\s*\)")

# Примитивы, которые умеем переносить. Список справочный: раскладкой управляют
# методы _do_<тег>, и всё, чего среди них нет, попадает в unsupported со своим
# именем — то есть не теряется молча, а видно в статистике конвертации.
SUPPORTED = frozenset({"rect", "circle", "line", "polyline", "polygon", "path",
                       "text", "image", "g"})

# Теги-ОПРЕДЕЛЕНИЯ: сами ничего не рисуют, на них только ссылаются по id. В
# unsupported им не место — иначе статистика ругалась бы на clipPath обложки,
# который мы как раз и переносим (через геометрию картинки), а заодно
# «непереносимых тегов» стало бы четыре там, где их ноль.
# Имена в нижнем регистре: деку разбирает HTML-парсер lxml, и clipPath в дереве
# становится clippath (та же причина, что у viewBox в attr()).
DEFS = frozenset({"clippath", "defs", "lineargradient", "radialgradient",
                  "mask", "pattern", "symbol", "marker", "filter",
                  "title", "desc", "metadata", "style"})

# Сколько чисел забирает команда пути. Флаги дуги (large/sweep) здесь обычные
# аргументы: SVG разрешает писать их слитно с соседями («a5 5 0 011 1»), и
# такую запись наш разборщик чисел не разложит — но во всех деках и шаблонах
# проекта все 60 дуг записаны через пробел, а слитная форма теперь не теряется
# молча: аргументы не сойдутся, и команда попадёт в статистику.
_PATH_ARGS = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2,
              "A": 7}
# Буква ловится ЛЮБАЯ, а не только из списка команд. Иначе лишний символ в
# разметке просто выпал бы из разбора, а его числа дочитались бы как аргументы
# предыдущей команды — контур поехал бы молча. Теперь такая буква доходит до
# _PATH_ARGS, не находится там и честно обрывает разбор.
# Порядок ветвей важен: буква пробуется первой, но на «1e-5» срабатывает ветвь
# числа и съедает запись целиком, поэтому экспонента не распадается на 1, e, -5.
_PATH_RE = re.compile(r"([A-Za-z])|(-?[\d.]+(?:[eE][-+]?\d+)?)")


def attr(el, name: str) -> str | None:
    """Атрибут без оглядки на регистр.

    lxml.html разбирает деку как HTML, а HTML-парсер приводит имена атрибутов к
    нижнему регистру: viewBox в разметке становится viewbox в дереве. Читать
    строго по camelCase означало бы не найти ни одного viewBox.
    """
    v = el.get(name)
    if v is None:
        v = el.get(name.lower())
    return v


def _f(el, name: str, default: float = 0.0) -> float:
    try:
        return float(attr(el, name))
    except (TypeError, ValueError):
        return default


def _affine(px: float, py: float, tx: float, ty: float,
            rot: tuple[float, float, float] | None) -> tuple[float, float]:
    """Применить translate/rotate из <g> к точке в пользовательских координатах.

    Для кривых это не приближение, а точный перенос: кривая Безье аффинно
    инвариантна, то есть повернуть её контрольные точки и повернуть саму кривую
    — буквально одно и то же. Поэтому path и clipPath считаются здесь, в
    координатах, а не поворотом рамки готовой фигуры, как у кругов.
    """
    px, py = px + tx, py + ty
    if not rot:
        return px, py
    # rotate(a cx cy) в SVG задаёт центр в системе ДО translate, поэтому к нему
    # прибавляется тот же сдвиг, что и к точке.
    ang, rcx, rcy = math.radians(rot[0]), rot[1] + tx, rot[2] + ty
    dx, dy = px - rcx, py - rcy
    return (rcx + dx * math.cos(ang) - dy * math.sin(ang),
            rcy + dx * math.sin(ang) + dy * math.cos(ang))


def _arc(x1: float, y1: float, rx: float, ry: float, deg: float,
         large: bool, sweep: bool, x2: float, y2: float) -> list[tuple]:
    """Эллиптическая дуга SVG (команда A) -> кубические кривые. -> [("C",...)].

    Перевод точный настолько, насколько вообще возможно: кривой Безье эллипс
    не описывается ни одной, ни тысячей — это разные классы кривых. Зато на
    куске в 90° кубическая Безье повторяет эллипс с ошибкой около 0.0003
    радиуса, а дальше ошибка растёт быстро. Поэтому дуга режется на куски не
    длиннее четверти оборота: при радиусах нашей графики (десятки пикселей)
    это сотые доли пикселя — меньше, чем округление координаты в EMU.

    Считается по приложению F.6 спецификации SVG: endpoint-параметризация
    (куда вести) переводится в центровую (где центр и какие углы), потому что
    Безье строится от углов. Отсюда и возня с радиусами: SVG разрешает задать
    радиусы, которых не хватает на хорду, и велит увеличить их пропорционально,
    а не считать такую дугу ошибкой.
    """
    if (x1, y1) == (x2, y2):
        return []                     # дуга в ту же точку: команда пропускается
    rx, ry = abs(rx), abs(ry)
    if not rx or not ry:
        return [("L", x2, y2)]        # вырожденный эллипс — спецификация велит прямую

    phi = math.radians(deg % 360.0)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    # Система самого эллипса: середина хорды в начало координат, наклон снят.
    hx, hy = (x1 - x2) / 2, (y1 - y2) / 2
    ux = cos_p * hx + sin_p * hy
    uy = -sin_p * hx + cos_p * hy

    big = (ux / rx) ** 2 + (uy / ry) ** 2
    if big > 1:
        rx, ry = rx * math.sqrt(big), ry * math.sqrt(big)

    den = (rx * uy) ** 2 + (ry * ux) ** 2
    num = (rx * ry) ** 2 - den
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    # Из четырёх эллипсов, проходящих через обе точки, нужный выбирают флаги:
    # «большая дуга» и «по часовой». Совпали — центр по другую сторону хорды.
    if large == sweep:
        coef = -coef
    cxu, cyu = coef * rx * uy / ry, -coef * ry * ux / rx
    cx = cos_p * cxu - sin_p * cyu + (x1 + x2) / 2
    cy = sin_p * cxu + cos_p * cyu + (y1 + y2) / 2

    start = math.atan2((uy - cyu) / ry, (ux - cxu) / rx)
    end = math.atan2((-uy - cyu) / ry, (-ux - cxu) / rx)
    span = end - start
    if sweep and span < 0:
        span += 2 * math.pi
    elif not sweep and span > 0:
        span -= 2 * math.pi

    parts = max(1, math.ceil(abs(span) / (math.pi / 2) - 1e-9))
    step = span / parts
    alpha = 4 / 3 * math.tan(step / 4)

    def at(t: float) -> tuple[float, float]:
        c, s = math.cos(t), math.sin(t)
        return (cx + rx * cos_p * c - ry * sin_p * s,
                cy + rx * sin_p * c + ry * cos_p * s)

    def tangent(t: float) -> tuple[float, float]:
        c, s = math.cos(t), math.sin(t)
        return (-rx * cos_p * s - ry * sin_p * c,
                -rx * sin_p * s + ry * cos_p * c)

    out = []
    for j in range(parts):
        ta, tb = start + j * step, start + (j + 1) * step
        pa, pb = at(ta), at(tb)
        da, db = tangent(ta), tangent(tb)
        out.append(("C", pa[0] + alpha * da[0], pa[1] + alpha * da[1],
                    pb[0] - alpha * db[0], pb[1] - alpha * db[1], *pb))
    return out


def parse_path(d: str | None) -> tuple[list[tuple], list[str]]:
    """Атрибут d -> ([сегменты], [команды, которых не умеем]).

    Сегменты приведены к трём видам, и только к ним: ("M", x, y), ("L", x, y),
    ("C", x1, y1, x2, y2, x, y) и ("Z",). Всё остальное разворачивается здесь, а
    не в сборке фигуры, чтобы в XML уходил ровно один набор команд:

    * относительные команды складываются с текущей точкой;
    * H и V достраивают пропущенную координату из текущей точки;
    * S и T восстанавливают первую контрольную точку отражением предыдущей — но
      только если предыдущая команда была того же рода, иначе по спецификации
      она совпадает с текущей точкой;
    * Q переводится в кубическую ТОЧНО, подъёмом степени
      (c1 = p0 + 2/3(q - p0), c2 = p1 + 2/3(q - p1)), а не приближением: у
      квадратичной и кубической Безье это одна и та же кривая.

    Повторение без буквы учитывается (SVG разрешает `C ... ... ...`), и после M
    лишние пары — это неявные L, как требует спецификация. Пропустить это
    правило особенно обидно: ломаная не оборвётся, а молча соединит не те точки.

    На неизвестной команде разбор ОБРЫВАЕТСЯ, а не пропускает её: число
    аргументов неизвестно, значит дальше поедет вся нумерация, и вместо явной
    потери получилась бы фигура из случайных точек.
    """
    out: list[tuple] = []
    bad: list[str] = []
    items: list = []
    for letter, num in _PATH_RE.findall(d or ""):
        items.append(letter or float(num))

    x = y = 0.0                      # текущая точка
    sx = sy = 0.0                    # начало подпути: сюда возвращает Z
    cub: tuple[float, float] | None = None   # 2-я контрольная точка последней C
    quad: tuple[float, float] | None = None  # контрольная точка последней Q
    cmd = ""
    i, n = 0, len(items)
    while i < n:
        if isinstance(items[i], str):
            cmd = items[i]
            i += 1
            if cmd in "Zz":
                out.append(("Z",))
                x, y = sx, sy
                cub = quad = None
                continue
        elif not cmd:
            break                    # числа до первой команды: разметка битая
        up = cmd.upper()
        need = _PATH_ARGS.get(up)
        if need is None:
            bad.append(cmd)
            break
        if i + need > n or any(isinstance(v, str) for v in items[i:i + need]):
            # Аргументы оборвались — дальше не гадаем, но и не молчим: в этом
            # месте обрывается ВЕСЬ остаток пути, и без записи в bad фигура
            # просто приехала бы короче, чем нарисована в разметке.
            bad.append(cmd)
            break
        a = [float(v) for v in items[i:i + need]]
        i += need
        rel = cmd.islower()

        if up == "M":
            x, y = (x + a[0], y + a[1]) if rel else (a[0], a[1])
            sx, sy = x, y
            out.append(("M", x, y))
            cmd = "l" if rel else "L"        # следующая пара — неявный L
            cub = quad = None
        elif up in ("L", "H", "V"):
            if up == "L":
                x, y = (x + a[0], y + a[1]) if rel else (a[0], a[1])
            elif up == "H":
                x = x + a[0] if rel else a[0]
            else:
                y = y + a[0] if rel else a[0]
            out.append(("L", x, y))
            cub = quad = None
        elif up in ("C", "S"):
            if up == "C":
                p = [(a[0], a[1]), (a[2], a[3]), (a[4], a[5])]
                if rel:
                    p = [(x + dx, y + dy) for dx, dy in p]
            else:
                c1 = (2 * x - cub[0], 2 * y - cub[1]) if cub else (x, y)
                p = [(a[0], a[1]), (a[2], a[3])]
                if rel:
                    p = [(x + dx, y + dy) for dx, dy in p]
                p = [c1, *p]
            out.append(("C", *p[0], *p[1], *p[2]))
            cub, quad = p[1], None
            x, y = p[2]
        elif up in ("Q", "T"):
            if up == "Q":
                q = (a[0], a[1]) if not rel else (x + a[0], y + a[1])
                end = (a[2], a[3]) if not rel else (x + a[2], y + a[3])
            else:
                q = (2 * x - quad[0], 2 * y - quad[1]) if quad else (x, y)
                end = (a[0], a[1]) if not rel else (x + a[0], y + a[1])
            c1 = (x + 2 / 3 * (q[0] - x), y + 2 / 3 * (q[1] - y))
            c2 = (end[0] + 2 / 3 * (q[0] - end[0]),
                  end[1] + 2 / 3 * (q[1] - end[1]))
            out.append(("C", *c1, *c2, *end))
            quad, cub = q, c2
            x, y = end
        else:                                 # A
            end = (a[5], a[6]) if not rel else (x + a[5], y + a[6])
            out.extend(_arc(x, y, a[0], a[1], a[2],
                            a[3] != 0, a[4] != 0, *end))
            cub = quad = None
            x, y = end
    return out, bad


def _scale(mode: str, rx: float, ry: float) -> tuple[float, float]:
    """Масштаб картинки по осям для одного значения object-fit. -> (kx, ky)."""
    if mode == "cover":
        k = max(rx, ry)
    elif mode == "contain":
        k = min(rx, ry)
    elif mode == "none":
        k = 1.0                              # натуральный размер, рамка не влияет
    elif mode == "scale-down":
        k = min(1.0, rx, ry)                 # contain, но крупнее натурального не тянем
    else:
        return rx, ry                        # fill: по каждой оси свой, пропорции не держим
    return k, k


def face_for(family: str, weight: str | float) -> tuple[str, bool]:
    """Начертание по CSS-весу. -> (имя гарнитуры для PPTX, флаг Bold).

    Жирность в PPTX — один флаг b="1", и это вес 700. А вся «жирность» деки —
    вес 600: у SB Sans это отдельное начертание, и зовут его Semibold (name
    table шрифта: family «SB Sans Display», subfamily «Semibold»). Отправить
    600 флагом нельзя — на машине с полным семейством PowerPoint честно возьмёт
    соседний, более жирный Bold, и вся дека выйдет жирнее макета. Поэтому
    полужирный уезжает ПОЛНЫМ именем начертания «SB Sans Display Semibold» без
    флага: такое имя находят и macOS, и Windows, у обоих оно в шрифте есть.

    Живёт здесь, а не в convert: нужна обеим половинам (тексту слайда и тексту
    внутри svg), а svgshapes в convert уже импортируется — в обратную сторону
    был бы цикл.
    """
    if isinstance(weight, str):
        try:
            w = float(weight)
        except ValueError:
            w = 700.0 if weight.strip() == "bold" else 400.0
    else:
        w = float(weight)
    if 500 <= w < 700:
        return f"{family} Semibold", False
    return family, w >= 700


def fit_image(src: tuple[float, float], box: tuple[float, float, float, float],
              mode: str = "fill",
              clip: tuple[float, float, float, float] | None = None):
    """Куда встанет картинка и что от неё видно. -> (рамка, обрезка) | None.

    Обрезка — (слева, справа, сверху, снизу) в долях ИСХОДНОЙ картинки: именно
    так устроен и a:srcRect в PPTX.

    object-fit в HTML и preserveAspectRatio в SVG — одно и то же под двумя
    именами (cover = slice, contain = meet, fill = none), поэтому здесь одна
    функция на оба случая, а вызывающий лишь переводит своё написание в общее.

    Считается так. Масштаб даёт прямоугольник R, который картинка занимает
    ЦЕЛИКОМ: R = (ux0, uy0, iw*kx, ih*ky), а ux0/uy0 центрируют остаток (и
    object-position, и xMidYMid по умолчанию центрируют). Видно из R пересечение
    с рамкой обрезки: это рамка clip-path, а без него — сама рамка картинки.
    Рамка фигуры ставится по ЭТОМУ пересечению, а обрезка — прообраз пересечения
    внутри R.

    Почему именно пересечение, а не рамка: при cover R больше рамки, пересечение
    равно рамке, обрезка положительная — лишнее уходит за кадр, как и просили.
    При contain наоборот, R меньше рамки, пересечение равно R, обрезка нулевая, и
    картинка встаёт пропорционально, с полями по краям. Если и там и там ставить
    фигуру по рамке, то при contain прообраз уйдёт в минус, а обрезать «меньше
    нуля» нельзя: картинку растянет на всю рамку, то есть contain молча
    превратится в cover с перевранными пропорциями.
    """
    iw, ih = src
    bx, by, bw, bh = box
    if not (iw > 0 and ih > 0 and bw > 0 and bh > 0):
        return None
    kx, ky = _scale(mode, bw / iw, bh / ih)
    sw, sh = iw * kx, ih * ky
    ux0, uy0 = bx - (sw - bw) / 2, by - (sh - bh) / 2

    cx0, cy0, cw, ch = clip if clip else (bx, by, bw, bh)
    vx0, vy0 = max(ux0, cx0), max(uy0, cy0)
    vx1, vy1 = min(ux0 + sw, cx0 + cw), min(uy0 + sh, cy0 + ch)
    if vx1 <= vx0 or vy1 <= vy0:
        return None                 # картинка и контур не пересеклись: рисовать нечего

    # Клампа сверху нет намеренно: пересечение по построению лежит внутри R,
    # поэтому доли и так в [0, 1), а max(0.0, ...) снимает лишь знаковый мусор
    # последнего разряда. Кламп как раз и прятал растяжение при contain.
    def frac(lo: float, hi: float, u0: float, span: float) -> tuple[float, float]:
        return max(0.0, (lo - u0) / span), max(0.0, 1.0 - (hi - u0) / span)

    left, right = frac(vx0, vx1, ux0, sw)
    top, bottom = frac(vy0, vy1, uy0, sh)
    return (vx0, vy0, vx1 - vx0, vy1 - vy0), (left, right, top, bottom)


def set_crop(pic, crop: tuple[float, float, float, float]) -> None:
    """Обрезка из fit_image -> a:srcRect. Что формат не выражает — не пишем.

    Порог не «на всякий случай», а ровно по разрядности формата: a:srcRect хранит
    доли в тысячных процента, то есть шаг 1e-5, и всё мельче в файле станет
    нулём само. Проверять же на != 0 нельзя: при contain нижняя доля считается
    как 1.0 - 0.9999999999999999 и выходит 1.1e-16 — ноль по смыслу, но «истина»
    для if. Из-за него в файл попадал <a:srcRect b="0"/>: безвредный, но
    сбивающий с толку элемент там, где обрезки нет вовсе.
    """
    for name, value in zip(("crop_left", "crop_right", "crop_top",
                            "crop_bottom"), crop):
        if round(value * 100000):
            setattr(pic, name, value)


def resolve_color(value: str | None, cssvars: dict) -> tuple[int, int, int] | None:
    """'var(--chart-2)' / '#26D07C' / 'rgb(...)' -> (r,g,b). None для none/прозрачного."""
    if not value:
        return None
    v = value.strip()
    for _ in range(4):                       # var() может ссылаться на var()
        m = _VAR_RE.match(v)
        if not m:
            break
        repl = cssvars.get(m.group(1))
        if repl is None:
            repl = (m.group(2) or "").strip()
        v = repl.strip()
    if not v or v in ("none", "transparent", "currentColor"):
        return None
    m = _HEX_RE.match(v)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))      # type: ignore
    m = _RGB_RE.match(v)
    if m:
        if m.group(4) is not None and float(m.group(4)) <= 0.01:
            return None
        return tuple(int(round(float(m.group(i)))) for i in (1, 2, 3))  # type: ignore
    return None


# --------------------------------------------- таблица стилей для внутренностей
# Внутрь <svg> таблица геометрии не заглядывает (браузер отдаёт одну рамку на
# весь блок), поэтому всё, что контурам назначает deck.css ПО КЛАССУ, для
# конвертера не существовало. А назначает он немало: лайнарт таймлайна
# (.tl-art-clouds path / .tl-art-arrows path) целиком обведён из CSS — в
# разметке у путей нет ни stroke, ни fill. Такие фигуры выходили «нарисованными
# невидимыми»: и заливка, и обводка noFill (пользователь: «на 72 и 74 нет
# иллюстрации»).
#
# Разбираем ровно ту грамматику, которой пользуется наша закрытая пара
# deck.css/motion.css для svg-внутренностей: селекторы из простых звеньев
# «тег», «.класс», «тег.класс», соединённых пробелом (потомок). Всё сложнее —
# комбинаторы, псевдоклассы, @-блоки, '*' — пропускается: сегодня такие правила
# до контуров не дотягиваются, а молча исполнять их «примерно» хуже, чем честно
# не исполнять. Обе таблицы стилей входят в sources_digest, так что новое
# правило без поддержки не проскочит незамеченным мимо пересборки и сверки.

_RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_SIMPLE_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9-]*)?((?:\.[\w-]+)*)")


def _simple_selector(tok: str) -> tuple[str | None, tuple[str, ...]] | None:
    """'path' / '.cls' / 'g.cls' -> (тег|None, классы). Сложнее -> None."""
    m = _SIMPLE_RE.fullmatch(tok)
    if not m or not (m.group(1) or m.group(2)):
        return None
    return m.group(1), tuple(c for c in (m.group(2) or "").split(".") if c)


def _match_token(tok: tuple[str | None, tuple[str, ...]], el) -> bool:
    tag, classes = tok
    if tag is not None and el.tag != tag:
        return False
    if classes:
        have = (el.get("class") or "").split()
        return all(c in have for c in classes)
    return True


@lru_cache(maxsize=1)
def _sheet_rules() -> tuple:
    """Поддержанные правила deck.css+motion.css. -> ((предки, лист, свойства),)

    Комментарии выбрасываются ДО разбора: внутри них встречаются скобки.
    Вложенные блоки (@keyframes, @media) наивному разбору не страшны:
    [^{}]+\\{[^{}]*\\} находит только ВНУТРЕННИЕ пары, и их селекторы («0%»,
    «body») отсеиваются грамматикой или фильтром листа. Значение с !important
    берётся как обычное: каскада, где важность решала бы исход, здесь нет.
    """
    from ..assembler import _read_pkg

    css = _read_pkg("engine/deck.css") + "\n" + _read_pkg("engine/motion.css")
    css = re.sub(r"/\*.*?\*/", " ", css, flags=re.S)
    rules = []
    for m in _RULE_RE.finditer(css):
        decls: dict[str, str] = {}
        for part in m.group(2).split(";"):
            name, _, val = part.partition(":")
            val = val.replace("!important", "").strip()
            if val:
                decls[name.strip().lower()] = val
        if not decls:
            continue
        for sel in m.group(1).split(","):
            toks = [_simple_selector(t) for t in sel.split()]
            if not toks or any(t is None for t in toks):
                continue
            rules.append((tuple(toks[:-1]), toks[-1], decls))
    return tuple(rules)


def _sheet_value(el, name: str) -> str | None:
    """Значение свойства из таблицы стилей для элемента. Позднее правило
    побеждает — у наших правил нет конфликтов, где решала бы специфичность."""
    got = None
    for ancestors, leaf, decls in _sheet_rules():
        if name not in decls or not _match_token(leaf, el):
            continue
        node, ok = el.getparent(), True
        for tok in reversed(ancestors):
            while node is not None and not _match_token(tok, node):
                node = node.getparent()
            if node is None:
                ok = False
                break
            node = node.getparent()
        if ok:
            got = decls[name]
    return got


def style_value(el, name: str) -> str | None:
    """Свойство элемента: инлайн-style, атрибут, потом таблица стилей.

    Порядок «атрибут раньше таблицы» — сознательная ИНВЕРСИЯ каскада CSS
    (там презентационный атрибут проигрывает любому правилу). Причина —
    анимации: motion.css пишет `.arc-draw{stroke-dashoffset:var(--draw-to,0)}`,
    а var(--draw-to) живёт инлайн-переменной, которую конвертер не разрешает;
    по-честному её значение и так продублировано атрибутом (финал анимации ==
    атрибут, это сверено по готовым пончикам). Инверсия ничего не ломает по
    построению: у всех откалиброванных контуров свойства в атрибутах, и они
    остаются как были; таблица добирает только то, чего в разметке нет вовсе.
    """
    inline = attr(el, "style") or ""
    m = re.search(rf"(?:^|;)\s*{name}\s*:\s*([^;]+)", inline)
    if m:
        return m.group(1).strip()
    v = attr(el, name)
    return v if v is not None else _sheet_value(el, name)


# ------------------------------------------------------- обрезка по clip-path
# Браузер режет лайнарт таймлайна по силуэту панелей: у .tl-art стоит
# clip-path:polygon(...), и хвосты стрелок, уходящие за панели (и даже за
# нижний край слайда), в html не видны. Без обрезки они легли бы диагоналями
# прямо по строкам таймлайна — вернуть обводку и не вернуть обрезку значило бы
# поменять одну поломку на другую.

def parse_clip_polygon(value: str | None, box: dict) -> list[tuple[float, float]] | None:
    """'polygon(0% 0%, 10px 100%, ...)' + рамка элемента -> точки в пикселях
    слайда. Не polygon / не px / не %% -> None (рисуем без обрезки)."""
    if not value:
        return None
    m = re.fullmatch(r"\s*polygon\(([^)]*)\)\s*", value)
    if not m:
        return None
    pts: list[tuple[float, float]] = []
    for pair in m.group(1).split(","):
        nums = pair.split()
        if len(nums) != 2:
            return None
        xy = []
        for n, (org, size) in zip(nums, ((box["x"], box["w"]),
                                         (box["y"], box["h"]))):
            if n.endswith("%"):
                xy.append(org + float(n[:-1]) / 100.0 * size)
            elif n.endswith("px"):
                xy.append(org + float(n[:-2]))
            elif n == "0":
                xy.append(org)
            else:
                return None
        pts.append((xy[0], xy[1]))
    return pts if len(pts) >= 3 else None


def _pt_inside(x: float, y: float, poly: list[tuple[float, float]]) -> bool:
    """Чёт-нечет лучом вправо; точно на границе — считается внутри по
    соседству (середины интервалов на границу не попадают)."""
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) / (y2 - y1) * (x2 - x1):
            inside = not inside
    return inside


def _clip_polyline(points: list[tuple[float, float]],
                   poly: list[tuple[float, float]],
                   ) -> list[list[tuple[float, float]]]:
    """Куски ломаной, лежащие внутри многоугольника (и невыпуклого тоже).

    По каждому ребру ломаной: параметры пересечений со всеми сторонами
    многоугольника делят ребро на интервалы, середина интервала решает,
    внутри он или нет. Смежные «внутренние» куски соседних рёбер склеиваются,
    чтобы стрелка оставалась одной линией, а не крошевом двухточечных фигур.
    """
    pieces: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        ts = [0.0, 1.0]
        for (px, py), (qx, qy) in zip(poly, poly[1:] + poly[:1]):
            den = (bx - ax) * (qy - py) - (by - ay) * (qx - px)
            if den == 0:
                continue                      # параллельны: решат другие рёбра
            t = ((px - ax) * (qy - py) - (py - ay) * (qx - px)) / den
            u = ((px - ax) * (by - ay) - (py - ay) * (bx - ax)) / den
            if 0.0 < t < 1.0 and 0.0 <= u <= 1.0:
                ts.append(t)
        ts.sort()
        for t0, t1 in zip(ts, ts[1:]):
            if t1 - t0 < 1e-9:
                continue
            tm = (t0 + t1) / 2
            if not _pt_inside(ax + (bx - ax) * tm, ay + (by - ay) * tm, poly):
                if cur:
                    pieces.append(cur)
                    cur = []
                continue
            p0 = (ax + (bx - ax) * t0, ay + (by - ay) * t0)
            p1 = (ax + (bx - ax) * t1, ay + (by - ay) * t1)
            if not cur:
                cur = [p0, p1]
            elif cur[-1] == p0:
                cur.append(p1)
            else:
                pieces.append(cur)
                cur = [p0, p1]
    if cur:
        pieces.append(cur)
    return pieces


class SvgPlacer:
    """Переносит содержимое одного <svg> на слайд.

    box — CSS-рамка <svg> в слайдовых пикселях (из geometry.json), viewBox — из
    разметки. Масштаб считается как у preserveAspectRatio="xMidYMid meet"
    (значение по умолчанию): единый коэффициент по меньшей стороне плюс
    центрирование остатка.
    """

    def __init__(self, slide, svg_el, box: dict, cssvars: dict,
                 color: str | None = None,
                 clip: list[tuple[float, float]] | None = None):
        self.slide = slide
        self.cssvars = cssvars
        self.color = color          # вычисленный CSS-color контейнера <svg>
        self.root = svg_el          # нужен, чтобы найти clipPath по url(#id)
        vb = (attr(svg_el, "viewBox") or "").replace(",", " ").split()
        self.ok = len(vb) == 4
        if not self.ok:
            return
        vx, vy, vw, vh = (float(n) for n in vb)
        self.vx, self.vy = vx, vy
        self.k = min(box["w"] / vw, box["h"] / vh) if vw and vh else 1.0
        self.ox = box["x"] + (box["w"] - vw * self.k) / 2
        self.oy = box["y"] + (box["h"] - vh * self.k) / 2
        # clip-path контейнера приходит в пикселях слайда; хранится в
        # пользовательских координатах, потому что резать сегменты удобнее
        # ДО уложения в рамку фигуры.
        self.clip = None
        if clip and self.k:
            self.clip = [((cx - self.ox) / self.k + vx,
                          (cy - self.oy) / self.k + vy) for cx, cy in clip]
        self.unsupported: dict[str, int] = {}
        self.count = 0

    # ------------------------------------------------------ система координат
    def X(self, ux: float) -> int:
        return round((self.ox + (ux - self.vx) * self.k) * EMU_PX)

    def Y(self, uy: float) -> int:
        return round((self.oy + (uy - self.vy) * self.k) * EMU_PX)

    def L(self, u: float) -> int:
        return round(u * self.k * EMU_PX)

    def pt(self, u: float) -> Pt:
        return Pt(u * self.k * PT_PX)

    # ------------------------------------------------------------- примитивы
    def place(self, el, tx: float = 0.0, ty: float = 0.0,
              rot: tuple[float, float, float] | None = None) -> None:
        """Разложить элемент (и его детей) в фигуры слайда."""
        tag = el.tag
        if tag == "g":
            gt = attr(el, "transform") or ""
            mt = _TRANSLATE_RE.search(gt)
            if mt:
                tx += float(mt.group(1))
                ty += float(mt.group(2) or 0.0)
            mr = _ROTATE_RE.search(gt)
            if mr:
                rot = (float(mr.group(1)), float(mr.group(2) or 0.0),
                       float(mr.group(3) or 0.0))
            for ch in el:
                if isinstance(ch.tag, str):
                    self.place(ch, tx, ty, rot)
            return
        if tag in DEFS:
            return                 # определение, а не изображение: рисовать нечего
        if self.clip is not None and tag != "path":
            # Обрезка реализована для контуров: в единственном обрезаемом svg
            # (лайнарт таймлайна) других примитивов нет. Появятся — фигура
            # нарисуется без обрезки, а статистика конвертации это покажет.
            self.unsupported[f"clip:{tag}"] = \
                self.unsupported.get(f"clip:{tag}", 0) + 1
        handler = getattr(self, f"_do_{tag}", None)
        if handler is None:
            self.unsupported[str(tag)] = self.unsupported.get(str(tag), 0) + 1
            return
        # Считаем по факту попавшего на слайд, а не по числу вызовов обработчика:
        # обработчик может и ничего не нарисовать (нечитаемый href, контур мимо
        # картинки), а строка статистики «блоков -> фигур» нужна как раз для того,
        # чтобы такую потерю было видно. Один <path> может дать и больше одной
        # фигуры, поэтому именно разность, а не инкремент.
        before = len(self.slide.shapes)
        handler(el, tx, ty, rot)
        self.count += len(self.slide.shapes) - before

    def _style(self, el, name: str) -> str | None:
        """Значение свойства: инлайн-style, атрибут, потом deck.css/motion.css
        (лайнарт таймлайна обведён целиком из таблицы стилей — см. style_value)."""
        return style_value(el, name)

    def _color_value(self, el, name: str) -> tuple[int, int, int] | None:
        """Цвет свойства (fill/stroke) с наследованием currentColor.

        currentColor в SVG — это CSS-свойство color элемента. В разметке оно
        почти никогда не написано: ищем явный color на самом элементе и его
        предках до корня <svg>, а если его нет — берём вычисленный браузером
        color контейнера <svg> из таблицы геометрии (self.color). Так
        покрашены все брендовые глифы: стрелка-уголок (_arrow.html), рамки
        contacts и section-frame, линейная графика quote — у их контуров
        только stroke="currentColor", а цвет задаёт класс контейнера в
        deck.css. Терять его — терять сам глиф: контур без заливки и без
        обводки невидим.
        """
        v = self._style(el, name)
        if v and v.strip().lower() == "currentcolor":
            v = self.color
            node = el
            while node is not None:
                own = self._style(node, "color")
                if own and own.strip().lower() != "currentcolor":
                    v = own
                    break
                node = None if node is self.root else node.getparent()
        return resolve_color(v, self.cssvars)

    def _shape(self, kind, x, y, w, h):
        shp = self.slide.shapes.add_shape(kind, Emu(self.X(x)), Emu(self.Y(y)),
                                          Emu(self.L(w)), Emu(self.L(h)))
        shp.shadow.inherit = False
        shp.line.fill.background()
        shp.fill.background()
        return shp

    def _paint(self, shp, el) -> None:
        # opacity элемента умножается в обе альфы: у DrawingML нет групповой
        # прозрачности фигуры, а у наших контуров заливка и обводка не
        # перекрываются (лайнарт — чистые штрихи), так что произведение
        # совпадает с тем, что показывает браузер.
        op = _opacity(self._style(el, "opacity"))
        fill = self._color_value(el, "fill")
        if fill:
            shp.fill.solid()
            shp.fill.fore_color.rgb = _rgb(fill)
            # fill-opacity и stroke-opacity -> <a:alpha> у цвета. Делает
            # pptxshell.set_alpha, а не fill.transparency: такого свойства у
            # python-pptx нет, присваивание молча терялось (подробно там же).
            set_alpha(shp.fill, op * _opacity(self._style(el, "fill-opacity")))
        stroke = self._color_value(el, "stroke")
        if stroke:
            shp.line.color.rgb = _rgb(stroke)
            shp.line.width = self.pt(_num(self._style(el, "stroke-width"), 1.0))
            set_alpha(shp.line.fill,
                      op * _opacity(self._style(el, "stroke-opacity")))

    def _do_rect(self, el, tx, ty, rot):
        rx = _f(el, "rx")
        w, h = _f(el, "width"), _f(el, "height")
        kind = MSO_SHAPE.ROUNDED_RECTANGLE if rx > 0 else MSO_SHAPE.RECTANGLE
        shp = self._shape(kind, _f(el, "x") + tx, _f(el, "y") + ty, w, h)
        if rx > 0 and min(w, h):
            shp.adjustments[0] = min(0.5, rx / min(w, h))
        self._paint(shp, el)

    def _do_line(self, el, tx, ty, rot):
        from pptx.enum.shapes import MSO_CONNECTOR

        x1, y1 = _f(el, "x1") + tx, _f(el, "y1") + ty
        x2, y2 = _f(el, "x2") + tx, _f(el, "y2") + ty
        shp = self.slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT, Emu(self.X(x1)), Emu(self.Y(y1)),
            Emu(self.X(x2)), Emu(self.Y(y2)))
        stroke = self._color_value(el, "stroke")
        if stroke:
            shp.line.color.rgb = _rgb(stroke)
            shp.line.width = self.pt(_num(self._style(el, "stroke-width"), 1.0))
            set_alpha(shp.line.fill,
                      _opacity(self._style(el, "opacity"))
                      * _opacity(self._style(el, "stroke-opacity")))

    def _do_circle(self, el, tx, ty, rot):
        """Окружность: сплошное кольцо, залитый круг или сегмент кольца.

        Обводка с stroke-dasharray — это дуга: видимая часть штриха. Переносим
        её в blockArc (сектор кольца), потому что обводка толщиной stroke-width
        геометрически и есть сектор с внутренним радиусом R-sw/2.
        """
        cx, cy = _f(el, "cx") + tx, _f(el, "cy") + ty
        r = _f(el, "r")
        sw = _num(self._style(el, "stroke-width"), 0.0)
        stroke = self._color_value(el, "stroke")
        fill = self._color_value(el, "fill")
        dash = self._style(el, "stroke-dasharray")

        if not stroke and fill:                              # залитый круг
            shp = self._shape(MSO_SHAPE.OVAL, cx - r, cy - r, 2 * r, 2 * r)
            self._paint(shp, el)
            return

        ro = r + sw / 2                                      # внешний радиус
        inner = max(0.0, (r - sw / 2)) / ro if ro else 0.0
        circumference = 2 * math.pi * r

        if dash and circumference:
            parts = [p for p in re.split(r"[,\s]+", dash.strip()) if p]
            visible = _num(parts[0], 0.0)
            if visible <= 0:
                return
            offset = -_num(self._style(el, "stroke-dashoffset"), 0.0)
            start = offset / circumference * 360.0
            sweep = min(visible / circumference * 360.0, 359.9)
            if rot:
                start += rot[0]
            shp = self._shape(MSO_SHAPE.BLOCK_ARC, cx - ro, cy - ro, 2 * ro, 2 * ro)
            # blockArc: adj1/adj2 — углы начала и конца дуги в 1/60000 градуса
            # (как все углы DrawingML), от 3 часов по часовой, с пином
            # 0..21599999 — отрицательный угол обнулился бы, поэтому
            # нормализуем в [0, 360). python-pptx пишет val = x*100000,
            # отсюда множитель 0.6 (= 60000/100000). Разворот через ноль
            # PowerPoint считает сам: swAng = adj2-adj1 (+оборот, если < 0).
            # adj3 — ТОЛЩИНА кольца долей короткой стороны (dr = ss*adj3/1e5),
            # а не внутренний радиус; ss здесь диаметр, поэтому (1-inner)/2 —
            # та же формула, что у DONUT ниже. Доли оборота и радиус вместо
            # градусов и толщины рисовали вместо долей щепки ~1° у трёх часов
            # («пончик без долей» на donut-chart и kpi-rings).
            shp.adjustments[0] = (start % 360.0) * 0.6
            shp.adjustments[1] = ((start + sweep) % 360.0) * 0.6
            shp.adjustments[2] = max(0.0, min(0.5, (1 - inner) / 2))
            if stroke:
                shp.fill.solid()
                shp.fill.fore_color.rgb = _rgb(stroke)
            return

        if stroke:                                           # сплошное кольцо
            shp = self._shape(MSO_SHAPE.DONUT, cx - ro, cy - ro, 2 * ro, 2 * ro)
            shp.adjustments[0] = max(0.0, min(0.5, (1 - inner) / 2))
            shp.fill.solid()
            shp.fill.fore_color.rgb = _rgb(stroke)

    def _points(self, el) -> list[tuple[float, float]]:
        nums = [float(n) for n in re.findall(_NUM, attr(el, "points") or "")]
        return list(zip(nums[0::2], nums[1::2]))

    def _do_polyline(self, el, tx, ty, rot, close: bool = False):
        pts = [(x + tx, y + ty) for x, y in self._points(el)]
        if len(pts) < 2:
            return
        ff = self.slide.shapes.build_freeform(self.X(pts[0][0]), self.Y(pts[0][1]),
                                              scale=1.0)
        ff.add_line_segments([(self.X(x), self.Y(y)) for x, y in pts[1:]],
                             close=close)
        shp = ff.convert_to_shape()
        shp.shadow.inherit = False
        shp.fill.background()
        shp.line.fill.background()
        self._paint(shp, el)

    def _do_polygon(self, el, tx, ty, rot):
        self._do_polyline(el, tx, ty, rot, close=True)

    def _do_path(self, el, tx, ty, rot):
        """<path> -> фигура произвольной формы с настоящими кривыми Безье.

        Перенос и поворот применяются к КОНТРОЛЬНЫМ ТОЧКАМ, в пользовательских
        координатах. Это не приближение: кривая Безье аффинно инвариантна —
        повернуть её контрольные точки и повернуть саму кривую даёт в точности
        одно и то же. Поэтому rotate() из <g> здесь обрабатывается точно, а не
        поворотом рамки фигуры, как у кругов.

        Рамка фигуры берётся по оболочке контрольных точек, а не по настоящим
        границам кривой. Оболочка шире (кубическая кривая лежит внутри
        четырёхугольника своих точек), и это сознательно: точки задаются
        ОТНОСИТЕЛЬНО объявленной рамки, поэтому любая рамка, в которую они все
        попадают неотрицательными, даёт одинаково точную кривую. Считать
        настоящий экстремум значило бы решать квадратное уравнение на каждой
        координате ради рамки, которую всё равно не видно.
        """
        segs, bad = parse_path(attr(el, "d"))
        for c in bad:
            key = f"path:{c}"
            self.unsupported[key] = self.unsupported.get(key, 0) + 1
        if self.clip is not None and segs:
            segs = self._clip_path_segs(segs, tx, ty, rot)
            tx = ty = 0.0        # координаты уже конечные: перенос и поворот
            rot = None           # применены до обрезки, второй раз нельзя
        pts = [_affine(px, py, tx, ty, rot) for seg in segs
               for px, py in zip(seg[1::2], seg[2::2])]
        if not pts:
            return
        x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
        # Рамка нулевой ширины (прямая линия) для a:path недопустима: PowerPoint
        # считает её вырожденной и фигуру не рисует. Один EMU — наименьшее, что
        # вообще различимо в формате, и на вид это ноль.
        w = max(self.L(max(p[0] for p in pts) - x0), 1)
        h = max(self.L(max(p[1] for p in pts) - y0), 1)

        # Незамкнутый контур не заливаем: у брендовой стрелки залитая «дырка»
        # между двумя штрихами была бы сплошным треугольником.
        has_fill = self._color_value(el, "fill") is not None
        # x0/y0 уже с перенесённым и повёрнутым началом, поэтому в _shape они
        # уходят как есть: tx/ty второй раз прибавлять нельзя.
        shp = self._shape(MSO_SHAPE.RECTANGLE, x0, y0, 0, 0)
        shp.width, shp.height = Emu(w), Emu(h)
        set_custgeom(shp, w, h, self._body(segs, x0, y0, tx, ty, rot),
                      "norm" if has_fill else "none")
        self._paint(shp, el)

    def _clip_path_segs(self, segs: list[tuple], tx: float, ty: float,
                        rot) -> list[tuple]:
        """Сегменты пути после обрезки контейнера, в КОНЕЧНЫХ координатах
        (перенос и поворот уже применены к точкам — наружу их отдавать нельзя).

        Режутся только ломаные — подпути из одних M/L: у лайнарта за
        многоугольник выходят ровно прямые хвосты стрелок. Подпуть с кривыми
        или Z остаётся целиком; если его точки при этом высовываются за
        многоугольник — считаем в unsupported, чтобы расхождение с браузером
        было видно в статистике, а не молчало.
        """
        out: list[tuple] = []
        sub: list[tuple] = []

        def flush() -> None:
            if not sub:
                return
            if all(s[0] in ("M", "L") for s in sub):
                points = [(s[1], s[2]) for s in sub]
                for piece in _clip_polyline(points, self.clip):
                    out.append(("M",) + piece[0])
                    out.extend(("L",) + p for p in piece[1:])
            else:
                if any(not _pt_inside(px, py, self.clip) for s in sub
                       for px, py in zip(s[1::2], s[2::2])):
                    self.unsupported["clip:curve"] = (
                        self.unsupported.get("clip:curve", 0) + 1)
                out.extend(sub)
            sub.clear()

        for seg in segs:
            if seg[0] == "M":
                flush()
            sub.append((seg[0],) + tuple(
                c for px, py in zip(seg[1::2], seg[2::2])
                for c in _affine(px, py, tx, ty, rot)))
        flush()
        return out

    def _body(self, segs: list[tuple], x0: float, y0: float,
              tx: float, ty: float, rot) -> str:
        """Сегменты пути -> содержимое a:path, в координатах рамки фигуры."""
        out: list[str] = []
        for seg in segs:
            if seg[0] == "Z":
                out.append("<a:close/>")
                continue
            inner = "".join(
                f'<a:pt x="{self.L(px - x0)}" y="{self.L(py - y0)}"/>'
                for px, py in (_affine(ux, uy, tx, ty, rot)
                               for ux, uy in zip(seg[1::2], seg[2::2])))
            tag = {"M": "moveTo", "L": "lnTo", "C": "cubicBezTo"}[seg[0]]
            out.append(f"<a:{tag}>{inner}</a:{tag}>")
        return "".join(out)

    # Вынесено из _do_image отдельной функцией не ради красоты: ровно та же
    # задача стоит у HTML-<img> с object-fit (convert._picture), и формула должна
    # быть одна. Пока её не было, картинка в <img> просто растягивалась на
    # замеренную рамку, и при своей картинке пользователя пропорции врали.
    def _clip_path(self, el, tx: float, ty: float,
                   rot) -> tuple[list[tuple], float, float, float, float] | None:
        """clip-path="url(#id)" -> (сегменты, x0, y0, w, h) в пользовательских ед.

        Ищется по id внутри ТОГО ЖЕ <svg>, а не по всему документу: id обложки
        собран с номером слайда (cover-image-clip-{{ index }}) именно потому, что
        в деке таких блоков несколько, и поиск по документу нашёл бы чужой.
        """
        m = re.match(r"url\(\s*#([^)\s]+)\s*\)",
                     (self._style(el, "clip-path") or "").strip())
        if not m:
            return None
        for node in self.root.iter():
            if (isinstance(node.tag, str) and node.tag == "clippath"
                    and node.get("id") == m.group(1)):
                segs: list[tuple] = []
                for sub in node.iter():
                    if isinstance(sub.tag, str) and sub.tag == "path":
                        segs += parse_path(attr(sub, "d"))[0]
                pts = [(px, py) for s in segs
                       for px, py in zip(s[1::2], s[2::2])]
                if not pts:
                    return None
                pts = [_affine(px, py, tx, ty, rot) for px, py in pts]
                x0, y0 = min(p[0] for p in pts), min(p[1] for p in pts)
                return (segs, x0, y0,
                        max(p[0] for p in pts) - x0, max(p[1] for p in pts) - y0)
        return None

    def _do_image(self, el, tx, ty, rot):
        """<image> -> картинка, обрезанная по clip-path. Без растеризации.

        Обложка (cover-image) — зелёная фигура Union с фотографией внутри. Можно
        было запечь всё это в один PNG, но тогда ни фигуру, ни фотографию уже не
        поменять, а требование «редактируемы ВСЕ объекты» касается и обложки.
        Здесь же выходит настоящая картинка: её можно заменить, сдвинуть,
        перекадрировать, а обрезана она тем же контуром, что и в SVG, потому что
        у p:pic те же spPr, что у фигуры, и custGeom ей задаётся так же.

        Геометрию (куда встанет и что от неё видно) считает fit_image: там же
        объяснено, почему рамка берётся по пересечению. Здесь только перевод
        написания — preserveAspectRatio="slice" это CSS cover, "meet" — contain,
        "none" — fill, — и рамка обрезки из clip-path.
        """
        from .convert import _img_bytes      # внизу цикла импорта: convert->svgshapes

        raw = _img_bytes(attr(el, "href") or attr(el, "xlink:href"))
        if not raw:
            self.unsupported["image:href"] = self.unsupported.get(
                "image:href", 0) + 1
            return
        try:
            from PIL import Image

            with Image.open(io.BytesIO(raw)) as im:
                iw, ih = im.size
        except Exception:
            self.unsupported["image:size"] = self.unsupported.get(
                "image:size", 0) + 1
            return
        box = (_f(el, "x") + tx, _f(el, "y") + ty,
               _f(el, "width"), _f(el, "height"))

        par = (attr(el, "preserveAspectRatio") or "xMidYMid meet").split()
        mode = ("fill" if "none" in par else
                "cover" if "slice" in par else "contain")
        clip = self._clip_path(el, tx, ty, rot)
        fit = fit_image((iw, ih), box, mode,
                        clip[1:] if clip else None)
        if fit is None:
            return
        (vx, vy, vw, vh), crop = fit

        w, h = max(self.L(vw), 1), max(self.L(vh), 1)
        pic = self.slide.shapes.add_picture(
            io.BytesIO(raw), Emu(self.X(vx)), Emu(self.Y(vy)), Emu(w), Emu(h))
        set_crop(pic, crop)
        if clip:
            set_custgeom(pic, w, h, self._body(clip[0], vx, vy, tx, ty, rot),
                          "norm")

    def _do_text(self, el, tx, ty, rot):
        """Подпись графика. SVG якорит текст по точке, PPTX — по рамке.

        Поэтому строим рамку вокруг точки якоря и задаём выключку/прижим так,
        чтобы центр или край строки встал ровно на эту точку: text-anchor
        отвечает за горизонталь, dominant-baseline — за вертикаль.
        """
        x, y = _f(el, "x") + tx, _f(el, "y") + ty
        tspans = [c for c in el if isinstance(c.tag, str) and c.tag == "tspan"]
        if tspans:
            lines = [(c.text or "").strip() for c in tspans]
            x = _f(tspans[0], "x", x) + tx
        else:
            lines = [re.sub(r"\s+", " ", "".join(el.itertext())).strip()]
        lines = [ln for ln in lines if ln]
        if not lines:
            return

        size = _num(attr(el, "font-size"), 28.0)
        anchor = attr(el, "text-anchor") or "start"
        # Рамка шириной с полный viewBox: текст точно не переносится, а
        # выключка сама прижмёт строку к нужному краю.
        half = 900.0
        left = {"middle": x - half, "end": x - 2 * half}.get(anchor, x)
        width = 2 * half if anchor == "middle" else (2 * half if anchor == "end" else 2 * half)
        line_h = size * 1.2 * max(1, len(lines))
        top = y - line_h / 2 if (attr(el, "dominant-baseline") or "") in (
            "middle", "central") else y - size
        box = self.slide.shapes.add_textbox(
            Emu(self.X(left)), Emu(self.Y(top)), Emu(self.L(width)),
            Emu(self.L(line_h)))
        tf = box.text_frame
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.word_wrap = False
        tf.auto_size = MSO_AUTO_SIZE.NONE
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        align = {"middle": PP_ALIGN.CENTER, "end": PP_ALIGN.RIGHT}.get(
            anchor, PP_ALIGN.LEFT)
        color = self._color_value(el, "fill")
        family = self.cssvars.get("--font-display", "SB Sans Display")
        family = family.split(",")[0].strip().strip('"\'')
        face, bold = face_for(family, attr(el, "font-weight") or "400")
        for i, text in enumerate(lines):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            run = p.add_run()
            run.text = text
            run.font.size = self.pt(size)
            run.font.bold = bold
            run.font.name = face
            if color:
                run.font.color.rgb = _rgb(color)


def set_custgeom(shp, w: int, h: int, body: str, fill: str) -> None:
    """Заменить у фигуры готовую форму (a:prstGeom) на собственную (a:custGeom).

    Почему не через build_freeform, которым сделаны polyline/polygon: у него
    есть только отрезки (add_line_segments), а нам нужны кривые. Поэтому фигура
    создаётся прямоугольником — ради готовых spPr, заливки и обводки, — а форма
    у неё подменяется на месте. Именно подменяется, с сохранением позиции в
    a:spPr: порядок элементов там задан схемой (xfrm, форма, заливка, обводка),
    и дописать custGeom в конец значило бы получить файл, который PowerPoint
    считает повреждённым.

    w/h — размер локальной системы координат пути в EMU. Он совпадает с
    размером рамки фигуры, поэтому масштабирование фигуры мышью просто тянет
    кривую, не ломая её пропорций относительно рамки.
    """
    from pptx.oxml import parse_xml
    from pptx.oxml.ns import nsdecls, qn

    geom = parse_xml(
        f"<a:custGeom {nsdecls('a')}><a:avLst/><a:gdLst/><a:ahLst/>"
        f'<a:cxnLst/><a:rect l="0" t="0" r="{w}" b="{h}"/><a:pathLst>'
        f'<a:path w="{w}" h="{h}" fill="{fill}">{body}</a:path>'
        f"</a:pathLst></a:custGeom>")
    spPr = shp._element.spPr
    old = spPr.find(qn("a:prstGeom"))
    spPr.replace(old, geom)


def _num(v, default: float) -> float:
    try:
        return float(str(v).strip().replace("px", ""))
    except (TypeError, ValueError):
        return default


def _opacity(v) -> float:
    """'0.14' -> 0.14. Нет значения или мусор -> 1.0 (непрозрачно)."""
    return min(1.0, max(0.0, _num(v, 1.0))) if v else 1.0


def _rgb(t: tuple[int, int, int]):
    from pptx.dml.color import RGBColor

    return RGBColor(*t)


def place_svg(slide, svg_el, box: dict, cssvars: dict,
              color: str | None = None,
              clip: list[tuple[float, float]] | None = None) -> tuple[int, dict]:
    """Разложить <svg> в фигуры. -> (сколько фигур, {тег: сколько не смогли}).

    color — вычисленный браузером CSS-color контейнера <svg> (из таблицы
    геометрии): им разрешается stroke/fill="currentColor" внутри.
    clip — многоугольник clip-path РОДИТЕЛЯ svg в пикселях слайда (см.
    parse_clip_polygon): им браузер режет хвосты лайнарта по силуэту панелей.
    """
    placer = SvgPlacer(slide, svg_el, box, cssvars, color, clip)
    if not placer.ok:
        return 0, {"__noviewbox__": 1}
    for child in svg_el:
        if isinstance(child.tag, str):
            placer.place(child)
    return placer.count, placer.unsupported
