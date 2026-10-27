"""Поправка таблицы геометрии под НАСТОЯЩИЙ текст: перенос строк и его следствия.

Таблица снята на синтетической рыбе. Всё, что зависит только от дерева секции,
в ней точно; остаётся одно — число строк. Если у реального слайда заголовок
.g22-name занимает две строки вместо одной, то сам он стал выше на 34.5px, а
.g22-text под ним на столько же уехал вниз. Таблица об этом не знает, и без
поправки конвертер положил бы текст на старое место.

Считать это стало можно, потому что textfit измеряет строку тем же HarfBuzz'ом,
что и Chrome: на 41546 текстовых листьях эталонной деки предсказанное число
строк совпало с браузерным во всех случаях, худшая ошибка по ширине — 0.014px.
То есть исходные данные для поправки точные, и вопрос только в том, как
распространить изменение высоты по дереву.

Правило распространения. Изменилась высота листа -> все СЛЕДУЮЩИЕ за ним
элементы потока сдвигаются на ту же величину, а дальше всё зависит от родителя:

  * высота родителя определяется содержимым -> он тоже вырос, и сдвиг идёт
    выше;
  * высота родителя задана раскладкой -> он поглощает изменение, и выше ничего
    не двигается.

Отличить одно от другого можно по самой таблице, не вводя новых полей: у
контейнера с высотой по содержимому h равна сумме высот детей с зазорами и
padding'ом, а у растянутого — больше. В .g22-cell дети занимают 174.5px при
h=659 (flex:1 1 0 растягивает плашку на ряд), поэтому перенос внутри плашки
наружу не выходит — и это ровно то, что показывает замер.

Почему не «пересчитать раскладку целиком». Полный пересчёт — это CSS-движок:
flex-базисы, min-height, растяжение рядов, и всё это в питоне, без браузера. А
нужно не это: нужна ПОПРАВКА к уже измеренной точной раскладке, причём только
по одной оси и только от одной причины. Поправка проверяема на тех же данных,
на которых снята таблица: каждое дерево измерено дважды — длинным текстом и
коротким, значит можно взять таблицу длинного варианта, подставить КОРОТКИЙ
текст, прогнать поправку и сверить с измеренным коротким вариантом. Именно это
делает calibrate --check-reflow, и пока он не сходится, поправке верить нельзя.
"""
from __future__ import annotations

# Высота считается «по содержимому», если сумма детей с зазорами совпала с
# высотой родителя с такой точностью. 1px, а не 0: браузер округляет рамки до
# 1/64px, и у колонки из шести детей ошибки округления складываются.
_SUM_EPS = 1.0


def children_of(by_path: dict[str, dict]) -> dict[str, list[str]]:
    """{путь: [пути детей по порядку]} по плоскому списку путей таблицы."""
    kids: dict[str, list[str]] = {}
    for path in by_path:
        if "::" in path:
            # Синтетические строки псевдоэлементов (::b/::a): они вне потока
            # (всегда position:absolute — см. domkey.JS_COLLECT) и вне
            # донорского выравнивания, а int() на "1::b" просто упал бы.
            continue
        kids.setdefault(path.rpartition(".")[0], []).append(path)
    for group in kids.values():
        group.sort(key=lambda p: int(p.rpartition(".")[2]))
    return kids


def _px(style: dict, name: str) -> float:
    css = style.get(name)
    if not css:
        return 0.0
    try:
        return float(css.strip().removesuffix("px"))
    except ValueError:
        return 0.0                         # normal / auto


def _gap(style: dict, axis: str) -> float:
    """Зазор между детьми по оси: 'h' — вертикальный, 'w' — горизонтальный.

    Оси разные: в колонке дети разделены row-gap, в ряду — column-gap, и у
    .sr-rows задан только первый. Shorthand тут не годится — computed gap
    приходит как "56px normal", и это не число.
    """
    return _px(style, "rowGap" if axis == "h" else "columnGap")


def _inset_h(style: dict) -> float:
    """Сколько по горизонтали отнимает рамка элемента от его border-box.

    Браузер отдаёт getBoundingClientRect, то есть border-box, а текст живёт в
    content-box: padding И border. Рамка в 1px выглядит мелочью, но у .fg-cell
    она есть, и полоса переноса без неё на 2px шире настоящей — этого хватает,
    чтобы слово перескочило на другую строку.
    """
    return (_px(style, "paddingLeft") + _px(style, "paddingRight")
            + _px(style, "borderLeftWidth") + _px(style, "borderRightWidth"))


def _inset_v(style: dict) -> float:
    return (_px(style, "paddingTop") + _px(style, "paddingBottom")
            + _px(style, "borderTopWidth") + _px(style, "borderBottomWidth"))


def _margin_h(style: dict) -> float:
    """Поля элемента по горизонтали. Место в ряду они занимают наравне с рамкой."""
    return _px(style, "marginLeft") + _px(style, "marginRight")


def _margin_v(style: dict) -> float:
    """Поля по вертикали.

    Схлопывание соседних полей не моделируем: во flex-контейнерах его нет
    вовсе, а в наших блочных колонках поле задано только с одной стороны
    (.kpi-desc margin-top:41px при нулевом margin-bottom у соседа), так что
    схлопываться нечему. Если появится пара встречных полей, это увидит
    check_reflow: сумма детей разойдётся с замером ровно на меньшее из них.
    """
    return _px(style, "marginTop") + _px(style, "marginBottom")


def text_height(row: dict, style: dict, lines: int, lh: float) -> float:
    """Высота текстового листа при заданном числе строк, с его рамкой."""
    return lines * lh + _inset_v(style)


def wrap_width(row: dict, style: dict) -> float:
    """Ширина, в которой переносится текст этого элемента.

    У подогнанной под текст рамки это доступная полоса (`aw`), а не замеренная
    ширина: замеренная равна рыбе, и переносить в ней реальный текст значило бы
    мерить в чужой колонке. У растянутой — своя ширина.

    Полоса ещё и урезается пределами самого элемента: max-width сужает место
    для переноса, min-width наоборот расширяет (у .tcc-tab 299px при полосе
    869 — текст переносится в 299 минус отступы, а не в 869).

    У растянутого блока (`wp`) полоса — своя ширина, несмотря на `aw`: его
    ширину ко второму проходу уже поставил родитель, и она точнее полосы,
    посчитанной подъёмом (у .tcc-heading 247 против 817).
    """
    if row.get("aw") is not None and not row.get("wp"):
        outer = _clamp_w(style, float("inf"), row["aw"])
    else:
        outer = row["w"]
    return max(outer - _inset_h(style), 0.0)


def _flex_row(style: dict) -> bool:
    """Flex-контейнер с горизонтальной осью.

    Только flex, не grid: в ряду flex место делят сами дети, поэтому сужение
    одного двигает остальных. В grid'е дорожки заданы шаблоном (у .sr-row это
    repeat(n,1fr)), и ширина ребёнка на положение соседей не влияет вовсе.
    """
    return (style.get("flexDirection") == "row"
            and (style.get("display") or "").endswith("flex"))


def _stacks(style: dict, n: int) -> bool:
    """Дети складываются по вертикали, а не стоят в один ряд.

    От этого зависит и высота контейнера (сумма против максимума), и то,
    поедет ли сосед вниз, когда элемент вырос.

    Grid считается рядом, пока детей не больше, чем колонок. Число колонок —
    замер, а не разбор нашего CSS: в computed gridTemplateColumns браузер
    отдаёт их уже разрешёнными в пиксели. Для .sr-row это ровно один ряд
    (колонок min(кол-во,4) при не более четырёх метриках), и его высота равна
    самой высокой ячейке; просуммировав ячейки, поправка решала, что высота
    задана раскладкой, и останавливалась на ряд ниже, чем нужно.
    """
    display = style.get("display") or ""
    if display.endswith("flex"):
        return style.get("flexDirection") != "row"
    if display.endswith("grid"):
        cols = len((style.get("gridTemplateColumns") or "none").split())
        return not (0 < cols and n <= cols)
    return True


def in_flow(path: str, by_path: dict[str, dict], styles: list[dict]) -> bool:
    """Элемент участвует в потоке родителя.

    absolute/fixed привязаны к содержащему блоку, а не к предыдущему соседу:
    в service-table .svc-desc и .svc-rows заданы absolute, и когда .svc-name
    вырос на две строки, НИ ОДИН из них не сдвинулся — замер показывает их на
    том же y в обоих вариантах. Поправка, которая тянет их за соседом, портит
    верную геометрию, поэтому поток проверяется, а не предполагается.
    """
    pos = styles[by_path[path]["s"]].get("position") or "static"
    return pos not in ("absolute", "fixed")


def inner_height(path: str, by_path: dict[str, dict],
                 kids: dict[str, list[str]], styles: list[dict]) -> float:
    """Высота, которую контейнер имел бы ровно по своему содержимому.

    В колонке дети складываются с зазорами, в ряду берётся самый высокий. Плюс
    собственный padding. Дети вне потока не считаются: на высоту родителя они
    не влияют.

    Флаг `off` — то же самое, но не от CSS, а от нашей правки: так помечен
    элемент, которого на реальном слайде нет вовсе (см. drop). Проверять его
    надо именно здесь, а не в in_flow: in_flow отвечает на вопрос про position,
    и этот смысл нужен _propagate'у в неизменном виде. Зазор при этом считается
    сам — group уже без выброшенного, значит и разделителей на один меньше.
    """
    style = styles[by_path[path]["s"]]
    group = [c for c in kids.get(path, []) if in_flow(c, by_path, styles)
             and not by_path[c].get("off")]

    def box(c: str) -> float:
        return by_path[c]["h"] + _margin_v(styles[by_path[c]["s"]])

    if _stacks(style, len(group)):
        inner = (sum(box(c) for c in group)
                 + _gap(style, "h") * max(len(group) - 1, 0))
    else:
        inner = max((box(c) for c in group), default=0.0)
    return inner + _inset_v(style)


def elastic_paths(by_path: dict[str, dict], kids: dict[str, list[str]],
                  styles: list[dict]) -> set[str]:
    """Контейнеры, чья высота определяется содержимым, а не раскладкой.

    Считается ОДИН раз по неиспорченной таблице — до любых поправок. Так и
    надо: «высота по содержимому» — свойство ИЗМЕРЕННОЙ раскладки, а если
    проверять его по ходу правок, то у родителя уже исправленного ребёнка сумма
    перестанет сходиться, и признак перевернётся на противоположный. Ровно на
    этом поправка сначала и ломалась: распространение останавливалось там, где
    должно было идти выше.

    Признак — сумма: у колонки по содержимому h равна сумме детей с зазорами, у
    растянутой (flex:1 1 0) больше. Это не разбор классов, а то же свойство,
    которым .g22-cell (659 при детях на 174.5) отличается от .g22-name (34.5
    ровно по строке).

    Но у суммы есть случай, когда она сходится ПО ПОСТРОЕНИЮ и потому не
    доказывает ничего: если свободное место поглощает сам элемент или его
    ребёнок, разница обнуляется при любой высоте родителя. Именно это и вышло в
    two-col-cards, причём трижды подряд:

      .tcc-body  flex:1          36 + 140 + 412 + 72 = 660 — ровно своя высота
      .tcc-card  ребёнок flex:1  60 + 660 = 720 — ровно своя высота
      .tcc-grid  height:720px    ряд: max(720, 720) = 720

    412px — это `margin-top:auto` у стрелки: getComputedStyle отдаёт margin
    РАЗРЕШЁННЫМ, то есть «поглоти остаток» приходит готовым числом и от
    настоящего поля неотличимо. Поэтому автополе здесь не ищется, а
    отсекается по первопричине: высоту тела задаёт его собственный flex:1.

    Цена ошибки была не теоретическая: донорская починка сжимала карточку на
    192px, потому что убыль шла вверх через всю эту цепочку, хотя CSS держит её
    жёстко (height:720px → height:100% → flex:1).

    Обратная сторона — колонка fit-content с растущим ребёнком тоже перестанет
    считаться упругой, и поправка там остановится раньше. В каталоге таких нет:
    --check-reflow на 94212 рамках показывает 0 и до, и после этой правки.
    """
    def imposed(p: str) -> bool:
        if _flex_grow(styles[by_path[p]["s"]]) > 0:
            return True                       # остаток поглощает сам элемент
        return any(_flex_grow(styles[by_path[c]["s"]]) > 0
                   for c in kids.get(p, ())
                   if in_flow(c, by_path, styles))

    return {p for p in by_path if kids.get(p) and not imposed(p)
            and abs(by_path[p]["h"]
                    - inner_height(p, by_path, kids, styles)) <= _SUM_EPS}


def _inner_width(path: str, by_path: dict[str, dict],
                 kids: dict[str, list[str]], styles: list[dict]) -> float:
    """Ширина контейнера ровно по содержимому: в ряду сумма, в колонке максимум."""
    style = styles[by_path[path]["s"]]
    group = [c for c in kids.get(path, []) if in_flow(c, by_path, styles)]

    def box(c: str) -> float:
        return by_path[c]["w"] + _margin_h(styles[by_path[c]["s"]])

    if _stacks(style, len(group)):
        inner = max((box(c) for c in group), default=0.0)
    else:
        inner = (sum(box(c) for c in group)
                 + _gap(style, "w") * max(len(group) - 1, 0))
    return inner + _inset_h(style)


def _clamp_w(style: dict, want: float, cap: float) -> float:
    """Ширина рамки с учётом min-width/max-width. Всё в border-box.

    `want` — ширина по содержимому (border-box), `cap` — доступная полоса.
    Порядок именно такой, как в CSS: сначала максимум, потом минимум, и минимум
    сильнее. У .tcc-tab это и видно: fit-content по заголовку даёт 186 px, но
    min-width:299px поднимает рамку до 299.

    box-sizing учитывается, потому что предел может быть задан content-box'у:
    тогда к нему надо прибавить padding и border, иначе предел окажется уже,
    чем браузерный, ровно на эту сумму.
    """
    edge = 0.0 if style.get("boxSizing") == "border-box" else _inset_h(style)
    lo, hi = _px(style, "minWidth"), _px(style, "maxWidth")
    out = min(want, cap)
    if hi > 0:
        out = min(out, hi + edge)
    if lo > 0:
        out = max(out, lo + edge)
    return out


def _flex_grow(style: dict) -> float:
    try:
        return float(style.get("flexGrow") or 0)
    except ValueError:
        return 0.0


def _spread(just: str, free: float, n: int) -> tuple[float, float]:
    """Как ряд делит свободное место. -> (отступ слева, добавка к зазору).

    Это весь justify-content в пяти строках. Считать его приходится, потому что
    от него зависит, куда поедут соседи сузившейся рамки: при flex-start вправо
    от неё всё сдвигается на всю дельту, при center — на половину, и в обе
    стороны.
    """
    if just == "center":
        return free / 2, 0.0
    if just in ("flex-end", "end", "right"):
        return free, 0.0
    if just == "space-between":
        return (0.0, free / (n - 1)) if n > 1 else (0.0, 0.0)
    if just == "space-around":
        return (free / n / 2, free / n) if n else (0.0, 0.0)
    if just == "space-evenly":
        return free / (n + 1), free / (n + 1)
    return 0.0, 0.0                        # flex-start / start / left / normal


def _row_offsets(parent: str, group: list[str], by_path: dict[str, dict],
                 styles: list[dict], widths: dict[str, float],
                 own: float | None = None) -> tuple[dict[str, float],
                                                    dict[str, float]]:
    """Ряд при заданных ширинах. -> (левые края от content-box, ширины).

    Модель используется только на РАЗНОСТЬ двух состояний (до и после смены
    ширины), и это не мелочь, а то, что делает её надёжной: всё постоянное —
    поля, зазоры, ширина самого родителя — из разности выпадает, и ошибиться
    модель может лишь в том, как ряд делит ИЗМЕНЕНИЕ свободного места.

    Растяжение (flex-grow) учитывается так же, как его видит браузер: замеренные
    ширины УЖЕ содержат разобранное место, поэтому свободного в исходном
    состоянии ноль, а появившееся после сужения соседа забирают растягивающиеся
    дети пропорционально flex-grow. Без этого сузившаяся рамка «утаскивала» бы
    за собой тех, кто на деле стоит на месте.

    Ширины возвращаются вместе с краями по той же причине: у растягивающегося
    соседа меняется не только положение, но и рамка, и без неё фигура в PPTX
    получила бы старую ширину при новом x.

    `own` — ширина самого ряда, если надо посчитать его не в той, что сейчас
    записана. Нужно, когда меняется рамка САМОГО ряда и дети переезжают внутри
    него (см. _rejustify), а не когда меняется ребёнок.
    """
    pstyle = styles[by_path[parent]["s"]]
    gap = _gap(pstyle, "w")
    inner = (by_path[parent]["w"] if own is None else own) - _inset_h(pstyle)
    take = {c: widths[c] for c in group}
    free = (inner - sum(take[c] + _margin_h(styles[by_path[c]["s"]])
                        for c in group)
            - gap * max(len(group) - 1, 0))
    grow = {c: _flex_grow(styles[by_path[c]["s"]]) for c in group}
    total = sum(grow.values())
    if total > 0 and free > 0:
        for c in group:
            take[c] += free * grow[c] / total
        free = 0.0
    lead, extra = _spread((pstyle.get("justifyContent") or "normal").split()[-1],
                          free, len(group))
    out, run = {}, lead
    for k, c in enumerate(group):
        style = styles[by_path[c]["s"]]
        if k:
            run += gap + extra
        run += _px(style, "marginLeft")
        out[c] = run
        run += take[c] + _px(style, "marginRight")
    return out, take


def _spread_row(path: str, delta: float, by_path: dict[str, dict],
                kids: dict[str, list[str]], styles: list[dict]) -> None:
    """Развести соседей по ряду после того, как `path` сменил ширину на delta.

    Зеркало вертикального _propagate, но наверх не поднимается: ширину
    контейнера пересчитывает сам обход apply(), он идёт снизу вверх и доходит до
    родителя уже с исправленными детьми.

    Сам `path` не двигаем: его новое x уже поставлено по выключке `al`, а она
    снята с двух замеров браузера — то есть известна, а не выведена. Модель ряда
    отвечает только за тех, кого замер подвинуть не может.

    Содержимое растянувшегося соседа внутрь не пересчитывается: обход apply()
    идёт снизу вверх и его детей уже прошёл. Это осознанная граница — своя
    рамка у соседа становится верной, а его внутренняя раскладка остаётся от
    замера. Если такой случай появится в каталоге, его увидит check_reflow:
    расхождение будет не у соседа, а у его детей.
    """
    parent = path.rpartition(".")[0]
    if not parent or parent not in by_path:
        return
    if not _flex_row(styles[by_path[parent]["s"]]):
        return                             # не ряд flex: место делит не контент
    if not in_flow(path, by_path, styles):
        return
    group = [c for c in kids.get(parent, []) if in_flow(c, by_path, styles)]
    if len(group) < 2 or path not in group:
        return
    now = {c: by_path[c]["w"] for c in group}
    was = dict(now, **{path: now[path] - delta})
    before, kept = _row_offsets(parent, group, by_path, styles, was)
    after, took = _row_offsets(parent, group, by_path, styles, now)
    for c in group:
        if c == path:
            continue
        step = after[c] - before[c]
        if abs(step) >= 0.01:
            _shift_x(c, step, by_path, kids)
        wide = took[c] - kept[c]
        if abs(wide) >= 0.01:
            by_path[c]["w"] = round(by_path[c]["w"] + wide, 2)


def row_slack(by_path: dict[str, dict], kids: dict[str, list[str]],
              styles: list[dict]) -> dict[str, float]:
    """{путь ряда: сколько в нём было СВОБОДНОГО места}, по замеру.

    Считается ОДИН раз по неиспорченной таблице, как и elastic_paths, и по той
    же причине: «сколько в ряду свободного места» — свойство ИЗМЕРЕННОЙ
    раскладки. У ряда, обнимающего детей, он нулевой; у растянутого равен тому,
    что делит justify-content.
    """
    return {p: round(by_path[p]["w"]
                     - _inner_width(p, by_path, kids, styles), 2)
            for p in by_path
            if kids.get(p) and _flex_row(styles[by_path[p]["s"]])}


def _rejustify(path: str, by_path: dict[str, dict],
               kids: dict[str, list[str]], styles: list[dict],
               slack: dict[str, float]) -> None:
    """Переставить детей ВНУТРИ ряда, у которого сменилась своя ширина.

    Зачем это отдельно от _spread_row. Там ширину менял ребёнок, и место делили
    его соседи; здесь ширину сменил сам ряд, и освободившееся (или пропавшее)
    место делят его дети по justify-content. Величины разные, и путать их
    нельзя.

    Где это видно. В светлой теме .contacts-subtitle-wrap — flex-ряд с
    justify-content:center и min-width:720px. На рыбе он обнимает текст, и
    свободного места в нём нет; на коротком реальном тексте min-width поднимает
    его до 720, и .contacts-subtitle обязан встать по центру этих 720. Выключка
    `al` этого не знает: она снята с двух замеров, где ряд в обоих случаях
    обнимал текст, и честно отвечает «левый край на месте» — промах 293 px.

    Отсчёт идёт от состояния, в котором ряд ровно ОБНИМАЕТ своих текущих детей,
    плюс то свободное место, что было в нём по замеру (slack). Не от прежней
    ширины ряда: прежняя посчитана на рыбе, и разница между ней и новой уже
    учтена — выключкой `al` у каждого ребёнка и моделью ряда у соседей. Здесь
    добавляется ровно одно слагаемое — место, которого в ряду раньше не было, а
    теперь появилось, потому что min/max-width не дал ему обнять детей.
    """
    style = styles[by_path[path]["s"]]
    if not _flex_row(style):
        return                        # не ряд flex: место делит не контент
    group = [c for c in kids.get(path, []) if in_flow(c, by_path, styles)]
    if not group:
        return
    widths = {c: by_path[c]["w"] for c in group}
    hug = _inner_width(path, by_path, kids, styles) + slack.get(path, 0.0)
    before, kept = _row_offsets(path, group, by_path, styles, widths, own=hug)
    after, took = _row_offsets(path, group, by_path, styles, widths,
                               own=by_path[path]["w"])
    for c in group:
        step = after[c] - before[c]
        if abs(step) >= 0.01:
            _shift_x(c, step, by_path, kids)
        wide = took[c] - kept[c]
        if abs(wide) >= 0.01:
            by_path[c]["w"] = round(by_path[c]["w"] + wide, 2)


def _resize(path: str, now: float, by_path: dict[str, dict],
            kids: dict[str, list[str]], styles: list[dict],
            slack: dict[str, float]) -> None:
    """Сменить ширину рамки: подвинуть её саму, её содержимое и её соседей.

    Четыре действия вместе, потому что порядок между ними существенный. Новое x
    считается от СТАРЫХ x и w: запиши ширину первой — относительный сдвиг
    окажется нулевым, и центрированная рамка молча останется прижатой влево.
    Поддерево едет вместе с рамкой (дети разложены от её content-box), и только
    ПОСЛЕ этого дети переставляются внутри неё — иначе сдвиг посчитался бы
    дважды. Соседи по ряду двигаются от изменения ширины, и это снова другая
    величина.
    """
    row = by_path[path]
    now = round(now, 2)
    delta = now - row["w"]
    if abs(delta) < 0.01:
        return
    dx = _fit_x(row["x"], row["w"], now, row.get("al", "l")) - row["x"]
    row["w"] = now
    if abs(dx) >= 0.01:
        _shift_x(path, dx, by_path, kids)
    _rejustify(path, by_path, kids, styles, slack)
    _spread_row(path, delta, by_path, kids, styles)


def _fit_x(x: float, was: float, now: float, al: str) -> float:
    """Левый край рамки, подогнанной под текст, после смены её ширины.

    Считается ОТНОСИТЕЛЬНО замеренной рамки (x, was), а не от полосы. Выключку
    (`al`) калибровка восстановила из пары замеров: у такой рамки CSS
    text-align бессмыслен, потому что рамка равна тексту, а место внутри полосы
    задаёт родительский flex.

    «От замера» важно: полосу калибровке приходилось вычислять подъёмом по
    дереву, и её ошибка попадала прямо в координату. Здесь же при al="l" x не
    меняется совсем, а при "c"/"r" на месте остаётся центр или правый край
    ЗАМЕРЕННОЙ рамки — то есть ошибиться может только ширина, и ровно на
    половину своей ошибки.
    """
    if al == "c":
        return x + (was - now) / 2
    if al == "r":
        return x + was - now
    return x


def apply(rows: list[dict], styles: list[dict],
          text_at: dict[str, str]) -> list[tuple[float, str]]:
    """Привести строки таблицы к РЕАЛЬНОМУ тексту. Правит rows НА МЕСТЕ.

    Три поправки, все от одной причины — в таблице лежит рыба, а не настоящий
    текст:

    * кегль «цифр-героев» stats-row: его выставляет не CSS, а наш deck.js, по
      ширине самого длинного значения в ряду (см. autofit_stats). Считается
      первым: он меняет высоту строки, то есть входные данные для остального;
    * высота и сдвиг соседей по числу строк (см. модуль выше);
    * ширина рамок, подогнанных под текст. У них в таблице записана ширина
      РЫБЫ, и без поправки плашка .ba-delta обвела бы чужую строку, а
      центрированный .quote-text встал бы со сдвигом 623px. Считаем ширину по
      реальному тексту (не больше доступной полосы — так работает fit-content) и
      ставим рамку в полосе по сохранённой выключке.

    text_at — {путь: собственный текст элемента настоящей деки}. Возвращает
    список применённых поправок по высоте [(дельта, путь)]: он нужен проверкам,
    чтобы видеть, что поправка сработала, а не что промахов просто не было.
    """
    from .textfit import width

    by_path = {r["p"]: r for r in rows}
    kids = children_of(by_path)
    # Признак «высота по содержимому» снимается с НЕИСПОРЧЕННОЙ таблицы, до
    # любых поправок (см. elastic_paths), поэтому считается здесь и один раз.
    elastic = elastic_paths(by_path, kids, styles)
    # Свободное место в ряду — по той же причине и в том же месте: это свойство
    # ИЗМЕРЕННОЙ раскладки (см. row_slack). Посчитай его позже, по уже
    # поправленной таблице, и у ряда, который только что растянул min-width,
    # «свободного места» окажется ровно столько, сколько мы сами ему и добавили.
    slack = row_slack(by_path, kids, styles)
    applied = autofit_stats(by_path, kids, styles, text_at, elastic)

    # Снизу вверх: ширина контейнера считается по уже исправленным детям.
    order = sorted(by_path, key=lambda p: (-p.count("."), p))
    for path in order:
        row = by_path[path]
        style = styles[row["s"]]
        st = _style_of(row, styles)
        text = text_at.get(path, "")
        leaf = not kids.get(path)

        if leaf and text.strip() and st.known and st.line_height > 0:
            if not row.get("wp"):
                _rewrap(path, by_path, kids, styles, text_at, elastic, applied)
            if row.get("aw") is not None:
                # fit-content = min(max-content, доступная полоса), причём
                # max-content — ширина текста БЕЗ переносов. Это не описка и не
                # упрощение: по самой длинной уложенной строке браузер рамку НЕ
                # сжимает. Замер прямой — у .fg-text в две строки рамка 538.7
                # (вся полоса), хотя строки 523.9 и 521.5; сжав до 523.9, мы
                # получали и неверную ширину, и сдвиг x на 7px от центровки.
                _resize(path, _clamp_w(style, width(text, st) + _inset_h(style),
                                       row["aw"]),
                        by_path, kids, styles, slack)
        elif not leaf and row.get("aw") is not None:
            _resize(path, _clamp_w(style,
                                   _inner_width(path, by_path, kids, styles),
                                   row["aw"]),
                    by_path, kids, styles, slack)

    # Третий проход, СВЕРХУ ВНИЗ и только для растянутых блоков (`wp`). Порядок
    # обратный не для красоты: первый проход обязан оставить такому блоку его
    # max-content, иначе родитель с width:fit-content посчитает свой предел по
    # уже урезанному ребёнку. Браузер делает ровно так же — сначала размер по
    # содержимому поднимается вверх и там упирается в min/max-width, и только
    # потом готовая ширина родителя спускается растянутым детям.
    for path in reversed(order):
        row = by_path[path]
        if not row.get("wp"):
            continue
        par = path.rpartition(".")[0]
        if par not in by_path:
            continue
        pstyle = styles[by_path[par]["s"]]
        _resize(path, max(by_path[par]["w"] - _inset_h(pstyle)
                          - _margin_h(styles[row["s"]]), 0.0),
                by_path, kids, styles, slack)
        if not kids.get(path):
            _rewrap(path, by_path, kids, styles, text_at, elastic, applied)
    return applied


def _rewrap(path: str, by_path: dict[str, dict], kids: dict[str, list[str]],
            styles: list[dict], text_at: dict[str, str], elastic: set[str],
            applied: list[tuple[float, str]]) -> None:
    """Пересчитать высоту листа по числу строк настоящего текста и разнести её.

    Высота меряется в СТРОКАХ, а не в пикселях: из замеренной рамки вычитаются
    отступы и делится на межстрочный интервал. Если результат не целый —
    значит рамку задаёт не текст (её высота где-то прибита), и трогать нечего.
    """
    from .textfit import wrap

    row = by_path[path]
    style = styles[row["s"]]
    st = _style_of(row, styles)
    text = text_at.get(path, "")
    if not (text.strip() and st.known and st.line_height > 0):
        return
    lines = wrap(text, wrap_width(row, style), st)
    was = (row["h"] - _inset_v(style)) / st.line_height
    if not (abs(was - round(was)) < 0.02 and round(was) >= 1):
        return
    delta = (len(lines) - round(was)) * st.line_height
    if abs(delta) < 0.01:
        return
    row["h"] = round(row["h"] + delta, 2)
    _propagate(path, delta, by_path, kids, styles, elastic)
    applied.append((delta, path))


# Пол читаемости и поправочный коэффициент — не настройка, а КОПИЯ чисел из
# deck.js autofitStats(). Разойдутся — разъедется и геометрия, поэтому стоят
# рядом и с явной ссылкой на источник.
_SR_FLOOR = 0.42
_SR_SLACK = 0.97


def _classes(row: dict) -> set[str]:
    return set((row.get("c") or "").split())


def _style_of(row: dict, styles: list[dict]):
    """Style строки с учётом кегля, пересчитанного под настоящий текст.

    В палитре лежит кегль, подогнанный под РЫБУ; если autofit_stats посчитал
    свой, дальше мерить надо им. Высота строки задана множителем
    (line-height:1.0), поэтому масштабируется вместе с кеглем.
    """
    from .textfit import Style

    st = Style(styles[row["s"]])
    fs = row.get("fs")
    if fs and st.size > 0:
        st.line_height *= fs / st.size
        st.size = fs
    return st


def autofit_stats(by_path: dict[str, dict], kids: dict[str, list[str]],
                  styles: list[dict], text_at: dict[str, str],
                  elastic: set[str]) -> list[tuple[float, str]]:
    """Повторить deck.js autofitStats(): ужать .sr-value под свои ячейки.

    Это единственный шаг раскладки, который делает не браузер, а наш
    собственный скрипт, — и единственное место во всей поправке, где элементы
    ищутся по КЛАССУ, а не по замеру. Иначе и нельзя: deck.js ищет их так же,
    и переносить надо именно его поведение, а не выводить его заново.

    Коэффициент один на весь ряд — берётся самый жёсткий, чтобы все цифры ряда
    остались одного кегля, как у дизайнера. Считается от fs0 (кегль из CSS, до
    вмешательства скрипта): в таблице лежит уже подогнанный под рыбу, и от него
    коэффициент вышел бы возведённым в квадрат.

    Высоту пересчитываем и распространяем: у .sr-value line-height:1.0, то есть
    высота равна кеглю, и под ней едут .sr-label с .sr-caption.
    """
    from .textfit import width

    out: list[tuple[float, str]] = []
    for path, group in kids.items():
        if path not in by_path or "sr-row" not in _classes(by_path[path]):
            continue
        vals = [p for p in _subtree(group, kids)
                if "sr-value" in _classes(by_path[p])]
        ratio, plan = 1.0, []
        for v in vals:
            row = by_path[v]
            cell = by_path.get(v.rpartition(".")[0])   # как parentElement в JS
            text = text_at.get(v, "")
            base = _style_of(row, styles)
            if cell is None or not base.known or not text.strip():
                continue
            fs0 = row.get("fs0") or base.size
            base.line_height *= fs0 / base.size
            base.size = fs0
            # scrollWidth и clientWidth браузер отдаёт целыми, и коэффициент в
            # deck.js считается именно по ним — округляем так же.
            sw = round(width(text, base))
            cw = round(cell["w"] - _inset_h(styles[cell["s"]]))
            if sw <= 0 or cw <= 0:
                continue
            plan.append((v, base))
            ratio = min(ratio, cw / sw)
        if ratio < 1:
            ratio = max(ratio * _SR_SLACK, _SR_FLOOR)
        for v, base in plan:
            row = by_path[v]
            row["fs"] = round(base.size * ratio, 2)
            now = round(base.line_height * ratio
                        + _inset_v(styles[row["s"]]), 2)
            delta = now - row["h"]
            if abs(delta) < 0.01:
                continue
            row["h"] = now
            _propagate(v, delta, by_path, kids, styles, elastic)
            out.append((delta, v))
    return out


def _subtree(roots: list[str], kids: dict[str, list[str]]) -> list[str]:
    """Все потомки перечисленных узлов, включая их самих. Как querySelectorAll."""
    out, stack = [], list(reversed(roots))
    while stack:
        p = stack.pop()
        out.append(p)
        stack.extend(reversed(kids.get(p, [])))
    return out


def _cross_k(style: dict, pstyle: dict) -> float:
    """Доля свободной высоты ряда, что остаётся НАД ребёнком: 0 / 0.5 / 1.

    Поперечная выключка во flex-ряду: align-self ребёнка, а если он её не
    задаёт — align-items родителя. Числом удобнее, чем названием: сдвиг по y
    равен этой доле от изменения свободной высоты, и три случая считаются одной
    формулой.

    baseline и stretch дают 0 — то же, что сейчас и происходит. stretch потому,
    что растянутый ребёнок занимает всю высоту и свободной высоты не остаётся
    вовсе; baseline — честное упрощение: выключка по базовой линии зависит от
    шрифтовых метрик соседей, а в наших шаблонах таких рядов нет (разойдутся —
    увидит check_reflow).
    """
    how = style.get("alignSelf") or "auto"
    if how in ("auto", "", "normal"):
        how = pstyle.get("alignItems") or "normal"
    if how == "center":
        return 0.5
    if how in ("flex-end", "end", "self-end"):
        return 1.0
    return 0.0


def _pinned_low(path: str, parent: str, by_path: dict[str, dict],
                styles: list[dict], elastic: set[str]) -> bool:
    """Ребёнок прижат к низу контейнера — изменения выше его не двигают.

    Так ведёт себя `margin-top:auto` (а равно justify-content:flex-end): поле
    по смыслу «поглоти остаток», и когда содержимое выше укоротилось, остаток
    просто стал больше, а сам элемент остался у нижнего края. У .tcc-arrow это
    и видно: донорская починка выбрасывает текст над стрелкой, браузер держит
    стрелку на y=844, а поправка поднимала её на 808.

    По стилю это НЕ ловится: getComputedStyle отдаёт автополе уже разрешённым
    (`marginTop: '412px'`), и от настоящего поля в 412px оно неотличимо.
    Поэтому признак берётся из замера — элемент стоит ровно на нижней кромке
    содержимого родителя:

        844 + 140 == (360 + 660) - 36   (нижний padding .tcc-body)

    Совпадением это быть не может, и вот почему проверяется `elastic`: у
    контейнера по содержимому дети заполняют его без остатка, и нижний из них
    упирается в кромку просто потому, что места ровно столько. А здесь высота
    навязана снаружи, остаток есть — и если ребёнок всё равно у кромки, значит
    остаток поглощается выше него.
    """
    if parent in elastic:
        return False
    pstyle = styles[by_path[parent]["s"]]
    edge = (by_path[parent]["y"] + by_path[parent]["h"]
            - _px(pstyle, "paddingBottom") - _px(pstyle, "borderBottomWidth"))
    row = by_path[path]
    return abs(row["y"] + row["h"]
               + _px(styles[row["s"]], "marginBottom") - edge) <= _SUM_EPS


def _propagate(path: str, delta: float, by_path: dict[str, dict],
               kids: dict[str, list[str]], styles: list[dict],
               elastic: set[str]) -> None:
    """Сдвинуть следующих за path в потоке и, если нужно, подняться выше."""
    cur, step = path, delta
    while True:
        parent = cur.rpartition(".")[0]
        if not parent or parent not in by_path:
            return                         # дошли до секции: выше двигать нечего
        if not in_flow(cur, by_path, styles):
            return                         # сам вне потока: соседей не двигает
        group = kids.get(parent, [])
        pstyle = styles[by_path[parent]["s"]]
        # Насколько подрастёт САМ родитель — нужно знать ДО сдвигов: в ряду
        # смещение считается от разницы «вырос родитель» и «вырос ребёнок».
        # Высоту при этом не прибавляем, а ПЕРЕСЧИТЫВАЕМ по детям: в ряду она
        # равна максимуму, и подросший ребёнок двигает её только если он был
        # самым высоким.
        grew = 0.0
        if parent in elastic:
            grew = round(inner_height(parent, by_path, kids, styles)
                         - by_path[parent]["h"], 2)
        if _stacks(pstyle, len(group)):
            # Колонка/поток: вниз едет то, что ниже изменившегося И в потоке.
            # Прижатый к низу — исключение: его держит не предыдущий сосед, а
            # нижняя кромка родителя (см. _pinned_low).
            for sib in group[group.index(cur) + 1:]:
                if (in_flow(sib, by_path, styles)
                        and not _pinned_low(sib, parent, by_path, styles,
                                            elastic)):
                    _shift(sib, step, by_path, kids)
        else:
            # Ряд: по вертикали детей ставит поперечная выключка, от свободной
            # высоты ряда. У центрированного она делится пополам: .quote-box
            # высотой 1080 (grew=0) при укорочении .quote-text на 120.64 сдвигает
            # его вниз на 60.32 — ровно то, что показывает замер.
            for sib in group:
                if not in_flow(sib, by_path, styles):
                    continue
                k = _cross_k(styles[by_path[sib]["s"]], pstyle)
                dy = k * (grew - (step if sib == cur else 0.0))
                if abs(dy) >= 0.01:
                    _shift(sib, dy, by_path, kids)
        if parent not in elastic:
            return                         # родитель поглотил: выше ничего
        if abs(grew) < 0.01:
            return
        by_path[parent]["h"] = round(by_path[parent]["h"] + grew, 2)
        cur, step = parent, grew


def drop(path: str, by_path: dict[str, dict], kids: dict[str, list[str]],
         styles: list[dict], elastic: set[str]) -> float:
    """Убрать элемент из раскладки: поднять то, что было под ним. -> дельта.

    Нужно это донорской починке (см. repair.py): у донора есть необязательный
    заголовок, у реального слайда его нет, и всё, что под ним, стоит на 79px
    ниже, чем надо. Своей формулы тут нет намеренно — выброс элемента даёт ровно
    то же следствие, что и перенос строки: содержимое контейнера стало ниже.
    Поэтому считается он тем же _propagate, который сверен с браузером на 94212
    рамках, а не вторым, отдельно написанным распространением.

    Дельту не складываем руками (высота + поля + зазор), а берём как разницу
    inner_height до и после. Слагаемые те же, но их набор зависит от раскладки:
    в ряду высота контейнера — максимум, а не сумма, и зазор исчезает только
    если выброшенный был не единственным. Разница это учитывает сама.
    """
    parent = path.rpartition(".")[0]
    if parent not in by_path:
        by_path[path]["off"] = True
        return 0.0                         # прямой ребёнок секции: выше некуда
    was = inner_height(parent, by_path, kids, styles)
    by_path[path]["off"] = True
    delta = round(inner_height(parent, by_path, kids, styles) - was, 2)
    _propagate(path, delta, by_path, kids, styles, elastic)
    return delta


def _shift(path: str, delta: float, by_path: dict[str, dict],
           kids: dict[str, list[str]]) -> None:
    """Сдвинуть элемент и всё его поддерево по вертикали."""
    _move(path, delta, "y", by_path, kids)


def _shift_x(path: str, delta: float, by_path: dict[str, dict],
             kids: dict[str, list[str]]) -> None:
    """Сдвинуть элемент и всё его поддерево по горизонтали."""
    _move(path, delta, "x", by_path, kids)


def _move(path: str, delta: float, axis: str, by_path: dict[str, dict],
          kids: dict[str, list[str]]) -> None:
    stack = [path]
    while stack:
        p = stack.pop()
        by_path[p][axis] = round(by_path[p][axis] + delta, 2)
        stack.extend(kids.get(p, []))
