"""Контракт между офлайн-калибровкой и рантайм-конвертацией в PPTX.

Геометрия слайдов снимается ОДИН раз в браузере (calibrate.py) и ложится в
geometry.json. В рантайме конвертер не поднимает браузер: он разбирает deck.html
через lxml и приклеивает к элементам заранее снятые координаты. Чтобы геометрия
попала на тот же элемент, обе стороны обязаны нумеровать DOM ОДИНАКОВО — этот
модуль и есть то место, где правило нумерации живёт в единственном экземпляре.

Правило пути: цепочка индексов среди элементов-детей (текстовые узлы и
комментарии не считаются), от <section> вниз, через точку — "1.0.2". Детей
СНАЧАЛА фильтруют, и только потом нумеруют — обе половины, иначе <style> первым
ребёнком сдвинул бы нумерацию у одной из них. Обходы совпадают, потому что в
шаблонах нет HTML-таблиц: браузер не доинсертит <tbody>, а значит el.children и
элементы-дети lxml дают одну и ту же нумерацию. Всё это проверяется тестами
(tests/test_topptx_domkey.py), а не доверием: расхождение здесь не падает — оно
молча отдаёт координаты соседу.

Ключ варианта — structure_key(): отпечаток дерева секции (путь, тег, класс,
слот). Сначала здесь была «подпись» из числа элементов в списочных слотах, и это
оказалось неверно по существу. Раскладка зависит не только от длин списков:
statement.html при длинном тексте добавляет секции класс .statement--wide, и
deck.css:209 этим классом ПРЯЧЕТ блок стрелок, а плашку растягивает на формат.
Для подписи оба варианта — один и тот же пустой ключ, поэтому эталонная дека
(в ней текст всегда близок к max_chars) измерила только широкий, а реальная дека
с коротким заголовком молча получала чужие координаты: стрелки с рамкой 0x0 и
растянутую плашку. Таких ветвлений в каталоге много — необязательный subtitle,
номер секции, карточная стрелка при body>200 символов, — и перечислять их руками
значит заводить второй источник правды рядом с шаблонами.

Отпечаток же снимается с готовой разметки, поэтому ветвление любой природы
попадает в ключ само. И главное: обе половины контракта считают его ОДНОЙ
функцией по lxml (калибровка — по собранной эталонной деке, конвертер — по
реальной), так что разойтись им нечем. Это дороже подписи: ключ строже, и
непокрытый вариант даёт промах по таблице. Промах — штатная ситуация, его
подхватывает браузерный запас в convert.py; молча неверная геометрия — нет.

Элементы СТРОГО внутри <svg> в отпечаток не входят. Причина не в удобстве: у них
геометрию считает не браузер — Jinja вписывает готовые координаты в системе
viewBox (см. комментарий-геометрию в bar-chart.html и stacked-bar.html), а
CSS-рамка самого <svg> от содержимого не зависит. Конвертер читает их атрибуты
прямо из HTML — точнее, чем из таблицы. Попади они в ключ, у bar-chart с 2 и с 6
барами были бы разные ключи при одинаковой браузерной раскладке, то есть лишние
замеры и промахи на ровном месте.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache

# Теги, которые обход не нумерует: вёрстки они не задают, а в el.children
# браузера попадают. Список явный и сверяется с JS-половиной тестом
# test_js_and_python_skip_the_same_tags.
#
# Отдельно держится допущение «браузер ничего не доинсертит»: <tbody> внутрь
# <table> он добавляет сам, и тогда обходы разойдутся. Таблиц в каталоге нет, и
# за этим следит test_catalogue_has_no_tables_so_tbody_cannot_appear — упадёт
# он, а не геометрия.
_SKIP_TAGS = frozenset({"script", "style", "template"})


def inside_svg(el) -> bool:
    """Элемент СТРОГО внутри <svg>: координаты заданы автором в системе viewBox.

    Сам <svg> сюда не попадает намеренно — его рамку ставит CSS, и она нужна,
    чтобы пересчитать viewBox в слайдовые пиксели. Ровно это же различие держит
    браузерная половина (JS_COLLECT наследует флаг от РОДИТЕЛЯ).
    """
    p = el.getparent()
    while p is not None and p.get("data-template") is None:
        if p.tag == "svg":
            return True
        p = p.getparent()
    return False


def is_authored(el) -> bool:
    """Сам <svg> или его содержимое: позиции расставил автор, а не браузер."""
    return el.tag == "svg" or inside_svg(el)


@lru_cache(maxsize=1)
def list_slots_by_template() -> dict[str, tuple[str, ...]]:
    """{template_id: (имена списочных слотов,)} из library.json.

    Один источник правды для обеих сторон: калибровка перечисляет варианты по
    этим именам, конвертер по ним же считает подпись разобранной деки.
    """
    from ..library import TemplateLibrary

    out: dict[str, tuple[str, ...]] = {}
    for spec in TemplateLibrary.load().templates:
        out[spec.id] = tuple(sorted(
            name for name, slot in (spec.slots or {}).items()
            if slot.kind == "list"))
    return out


def element_children(el) -> list:
    """Элементы-дети lxml в порядке документа; комментарии и PI отброшены."""
    return [c for c in el if isinstance(c.tag, str) and c.tag not in _SKIP_TAGS]


@lru_cache(maxsize=1)
def css_var_names() -> tuple[str, ...]:
    """Имена всех кастомных свойств, объявленных в deck.css.

    Список берётся из самой таблицы стилей, а не дублируется здесь руками:
    добавят в deck.css новую переменную — калибровка подхватит её сама.
    """
    import re

    from ..assembler import _read_pkg

    css = _read_pkg("engine/deck.css")
    return tuple(sorted(set(re.findall(r"(--[a-z0-9-]+)\s*:", css))))


def js_collect() -> str:
    """JS_COLLECT с подставленным списком имён CSS-переменных."""
    import json

    return JS_COLLECT.replace("__VAR_NAMES__",
                              json.dumps(list(css_var_names())))


def walk_paths(section):
    """Обойти секцию, выдавая (path, element) в том же порядке, что и браузер.

    path — строка вида "1.0.2"; у прямых детей <section> путь из одного индекса.
    """
    def rec(el, prefix: list[int]):
        for i, child in enumerate(element_children(el)):
            path = prefix + [i]
            yield ".".join(str(n) for n in path), child
            yield from rec(child, path)

    yield from rec(section, [])


def structure_fingerprint(section) -> str:
    """Построчный отпечаток дерева секции — то, от чего зависит раскладка.

    Первая строка — классы самой <section>: там живут контентные ветвления
    мастера (.statement--wide), и без неё ключ не отличил бы широкий вариант от
    обычного. Дальше по одной строке на элемент: путь, тег, класс, слот.

    Текст в отпечаток НЕ входит. Его влияние другой природы: длина строки меняет
    кегль (autofitStats) и число переносов, то есть величину непрерывную, а не
    перечислимую — ключами её не покрыть. Насколько это сдвигает рамки, калибровка
    измеряет: эталонная дека содержит варианты с коротким и с длинным текстом, они
    дают ОДИН ключ, и проверка совпадения внутри ключа печатает расхождение.
    """
    lines = ["section " + (section.get("class") or "")]
    for path, el in walk_paths(section):
        if inside_svg(el):
            continue
        lines.append(f'{path} {el.tag} {el.get("class") or ""} '
                     f'{el.get("data-slot") or ""}')
    return "\n".join(lines)


def structure_key(section) -> str:
    """Короткий ключ варианта раскладки. Хэш от structure_fingerprint()."""
    blob = structure_fingerprint(section).encode("utf-8")
    return hashlib.blake2b(blob, digest_size=6).hexdigest()


def variant_key(section) -> str:
    """Ключ строки в geometry.json: "шаблон|отпечаток".

    Шаблон в ключе избыточен (его класс уже в отпечатке), но ключ читают люди:
    по "statement|6f2a..." видно, какой макет промахнулся.
    """
    return f'{section.get("data-template") or "?"}|{structure_key(section)}'


# Что входит в отпечаток источников. Список не «всё, что в репозитории», а ровно
# то, от чего зависят ключи и снятые координаты:
#   templates/slides/* — разметка, то есть сами отпечатки деревьев;
#   templates/library.json — какие слоты и ветвления шаблон вообще допускает;
#   engine/deck.css, motion.css — раскладка, то есть снятые рамки.
# deck.js сюда НЕ входит, и это решение, а не недосмотр: в калибровке он уже
# отработал (viewport 1920x1080 -> --deck-scale 1), и на рамки в снимке не
# влияет — его правка таблицу не портит, а ложная тревога заставляла бы
# пересобирать 9.5 MB из-за анимации.
_SOURCES = ("templates/library.json", "engine/deck.css", "engine/motion.css")

# Функции, задающие ПРАВИЛО ключа. Хэшируется их исходник, а не весь domkey.py:
# правка комментария в шапке модуля таблицу не ломает, а правка обхода — ломает.
# Именно этот случай однажды и стоил деке 40% слайдов: data-slot попал в
# отпечаток позже, чем были сняты деки, ключи у всех сменились, и никто не
# заметил. Теперь сменится и отпечаток источников.
_KEY_RULE = ("_SKIP_TAGS", "inside_svg", "is_authored", "element_children",
             "walk_paths", "structure_fingerprint", "structure_key",
             "variant_key")


@lru_cache(maxsize=1)
def sources_digest() -> str:
    """Отпечаток шаблонов, CSS и правила ключа. -> 12 hex-символов.

    Зачем. geometry.json лежит в репозитории ОТДЕЛЬНО от шаблонов, и разойтись
    они могут молча: поменяли разметку — у живой деки другой ключ, а таблица об
    этом не узнает. Промах по ключу сам по себе штатен (смешанная раскладка,
    которой не было в переборе), и на него есть донорская починка. Но цена у
    двух случаев разная и измерена: на смесях при СОШЕДШЕЙСЯ таблице донор даёт
    худшее отклонение 0.010 px, а на деках с разъехавшимся отпечатком — 159
    промахов >0.5 px из 411 рамок, худший 790 px. Отличить их по самому промаху
    нельзя — разница не в ключе, а в том, соответствует ли таблица шаблонам.
    Это и есть ровно то, что здесь считается.

    Хэш, а не время правки файла: mtime меняет git checkout, не меняя
    содержимого, и отпечаток срабатывал бы на каждом клоне репозитория.

    По той же причине переводы строк сводятся к LF: git на Windows
    (core.autocrlf=true) выкладывает шаблоны с CRLF, и по сырым байтам таблица,
    снятая на сервере, «отставала» на машине разработчика, а снятая на Windows —
    на сервере. Раскладку перевод строки не меняет: Jinja сама сводит их к LF.
    """
    h = hashlib.blake2b(digest_size=6)
    from importlib import resources
    root = resources.files("htmlslides")

    def feed(name: str, blob: bytes) -> None:
        # Имя в хэш тоже: иначе переименование шаблона прошло бы незамеченным,
        # а ключ оно меняет (data-template лежит в variant_key).
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(blob.replace(b"\r\n", b"\n"))
        h.update(b"\0")

    tpl = root / "templates" / "slides"
    for entry in sorted(p.name for p in tpl.iterdir() if p.name.endswith(".html")):
        feed(f"templates/slides/{entry}", (tpl / entry).read_bytes())
    for name in _SOURCES:
        node = root
        for part in name.split("/"):
            node = node / part
        feed(name, node.read_bytes())

    import inspect
    import sys
    me = sys.modules[__name__]
    for name in _KEY_RULE:
        obj = getattr(me, name)
        # У _SKIP_TAGS исходник не возьмёшь — это значение, и в хэш идёт оно.
        src = (repr(sorted(obj)) if isinstance(obj, frozenset)
               else inspect.getsource(obj))
        feed(f"domkey.{name}", src.encode("utf-8"))
    # Сборочный JS — тоже правило, но не ключа, а СОСТАВА строк: он решает,
    # какие строки вообще попадают в таблицу (с 2026-10-26 — ещё и
    # синтетические ::before/::after). Новый сборщик со старой таблицей — это
    # молча неполные слайды, ровно тот сорт расхождения, ради которого отпечаток
    # и существует.
    feed("domkey.JS_COLLECT", JS_COLLECT.encode("utf-8"))
    return h.hexdigest()


# Браузерная половина контракта. Держится рядом с walk_paths намеренно: это
# один алгоритм в двух рантаймах, и расходиться они не должны.
#
# Почему именно так:
#   * viewport ровно 1920x1080 -> rescale() выставляет --deck-scale = 1, и
#     getBoundingClientRect отдаёт настоящие слайдовые пиксели, а не
#     отмасштабированные под окно (deck.js:104-112);
#   * координаты приводим к началу самой секции, поэтому центрирование
#     .deck-stage через translate(-50%,-50%) на результат не влияет;
#   * вместе с рамкой снимаем подмножество computed style — то, что станет
#     свойствами фигуры PPTX. Этого набора хватает, чтобы собрать текстовую
#     рамку или прямоугольник, не возвращаясь в браузер.
JS_COLLECT = r"""
(() => {
  const VAR_NAMES = __VAR_NAMES__;
  const PROPS = ["fontFamily","fontSize","fontWeight","lineHeight","letterSpacing",
                 "color","backgroundColor","backgroundImage","backgroundSize",
                 "textAlign","textTransform","opacity","borderTopWidth",
                 "borderLeftWidth","borderTopColor","borderLeftColor",
                 // Правую и нижнюю рамки держим не для вида, а для content-box:
                 // у .fg-cell рамка 1px, и без неё доступная тексту полоса
                 // съезжала на пиксель. Этого пикселя хватило, чтобы проверка
                 // выключки «x == левый край полосы» не сработала и левая
                 // выключка определилась как центральная.
                 "borderRightWidth","borderBottomWidth",
                 "borderTopStyle","display","flexDirection","justifyContent",
                 // alignItems задаёт поперечную выключку всем детям ряда,
                 // alignSelf — перебивает её у одного. Это не про вид, а про
                 // координату: .quote-box центрирует .quote-text по высоте
                 // 1080, и при укорочении текста на 120px рамка едет вниз на
                 // 60. Без alignSelf пришлось бы считать, что исключений не
                 // бывает, а они как раз и ловятся поштучно.
                 "alignItems","alignSelf","overflow",
                 // Зазоры по отдельности, а не shorthand: у .sr-rows задан
                 // только row-gap, и computed gap приходит как "56px normal" —
                 // такую строку парсер молча читал как 0, сумма детей
                 // расходилась с замером на все 56, и поправка считала высоту
                 // .sr-rows заданной раскладкой.
                 "rowGap","columnGap",
                 // Колонки grid'а браузер отдаёт уже разрешёнными в пиксели
                 // ("420px 420px 420px 420px"), то есть их число — замер, а не
                 // разбор нашего CSS. По нему reflow понимает, лежат дети в
                 // один ряд (высота = максимум) или в несколько.
                 "gridTemplateColumns",
                 // position нужен не для вида, а для потока: .svc-desc и
                 // .svc-rows заданы absolute, то есть ВНЕ потока, и высота
                 // соседа их не двигает. Без этого свойства поправка на
                 // перенос строк тянула их за собой (см. reflow.in_flow).
                 "position",
                 // По отдельности, а не shorthand: по ним считается
                 // content-box родителя — доступная ширина для элементов,
                 // чья рамка подогнана под текст (см. calibrate.available).
                 "paddingTop","paddingRight","paddingBottom","paddingLeft",
                 // Поля — слагаемое в сумме детей, по которой reflow отличает
                 // контейнер «по содержимому» от растянутого. У .kpi-desc стоит
                 // margin-top:41px, и без него сумма детей .kpi-side не сходится
                 // с замером: признак «высота по содержимому» переворачивается
                 // на противоположный, и поправка останавливается не там.
                 "marginTop","marginRight","marginBottom","marginLeft",
                 // flexGrow — кто забирает освободившееся место в ряду. Если
                 // сосед растягивается, сузившаяся рамка НЕ двигает тех, кто за
                 // ней: место съест растяжение, а не сдвиг.
                 "flexGrow",
                 // Пределы ширины — не стилистика, а часть fit-content:
                 // у .tcc-tab стоит width:fit-content с min-width:299px и
                 // max-width:869px, и без них подгонка под текст давала 186
                 // вместо 299. Вместе с ними нужен и box-sizing: от него
                 // зависит, к какой рамке предел относится.
                 "minWidth","maxWidth","boxSizing",
                 // object-fit — не стилистика, а кадрирование: у .svc-image img
                 // стоит contain, у .tl-art-img — cover. Рамку браузер отдаёт
                 // одну и ту же в любом случае, поэтому по рамке это не видно, и
                 // без свойства картинку просто растягивало на рамку. С дежурным
                 // ассетом пропорции почти совпадают и ошибка не бросается в
                 // глаза, а со своей картинкой пользователя — сразу.
                 "objectFit"];
  const SKIP = new Set(["SCRIPT","STYLE","TEMPLATE"]);
  // Отфильтровать СНАЧАЛА, нумеровать ПОТОМ — буквально как element_children()
  // в питонной половине. Если нумеровать по el.children и пропускать на ходу,
  // то <style> первым ребёнком отдаёт следующему элементу индекс 1, а lxml тому
  // же элементу — 0, и геометрия поедет на соседа. В шаблонах таких тегов
  // сейчас нет, но контракт обязан совпадать не по счастью, а по построению
  // (см. test_topptx_domkey.py::test_skipped_tag_shifts_both_walks_alike).
  const kids = (el) => Array.from(el.children)
    .filter((c) => !SKIP.has(c.tagName));
  const round = (v) => Math.round(v * 100) / 100;

  const out = [];
  // Элементы, которым deck.js переписал кегль inline (autofitStats). Базовый
  // кегль снимается ОДНИМ общим проходом в самом конце — см. ниже.
  const overridden = [];
  document.querySelectorAll("section.slide").forEach((sec) => {
    const base = sec.getBoundingClientRect();
    const items = [];

    const visit = (el, path, authored) => {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const style = {};
      PROPS.forEach((p) => { style[p] = cs[p]; });
      // Прямой текст элемента: только собственные текстовые узлы, без текста
      // детей — иначе контейнер «присвоил» бы себе текст всех потомков.
      const own = Array.from(el.childNodes)
        .filter((n) => n.nodeType === 3)
        .map((n) => n.textContent)
        .join("")
        .trim();
      const rec = {
        path: path.join("."),
        tag: el.tagName.toLowerCase(),
        cls: el.getAttribute("class") || "",
        slot: el.getAttribute("data-slot") || null,
        // authored = элемент СТРОГО внутри <svg>: координаты задал автор в
        // системе viewBox, браузер их не считал. Калибровка такие элементы не
        // хранит — конвертер читает их атрибуты из HTML напрямую. Сам <svg>
        // сюда НЕ попадает: его рамку ставит CSS, и она нужна, чтобы
        // пересчитать viewBox в слайдовые пиксели.
        authored: authored,
        x: round(r.left - base.left), y: round(r.top - base.top),
        w: round(r.width), h: round(r.height),
        // scrollWidth нужен, чтобы воспроизвести autofitStats() без браузера
        sw: el.scrollWidth, sh: el.scrollHeight,
        text: own,
        style: style,
      };
      items.push(rec);
      if (el.style.fontSize) overridden.push([rec, el, el.style.fontSize]);
      // Псевдоэлементы ::before/::after — не DOM-узлы: их не видит ни этот
      // обход, ни walk_paths в питоне. Но deck.css рисует ими настоящие
      // детали: сепараторы three-col (.tcl-col+.tcl-col::before) и замыкающую
      // линию timeline (.tl-row:last-child::after) — без синтетической строки
      // конвертер молча их терял («пропали линии»). Берём только декоративные
      // БОКСЫ: position:absolute и явные размеры в px. Текстовые псевдо
      // (content:counter у .chrome-num) сюда не попадают — у них width auto.
      // Путь — родительский плюс "::b"/"::a": walk_paths такого не выдаст,
      // значит со строками настоящих детей он не столкнётся никогда.
      ["::before", "::after"].forEach((pe) => {
        const pc = getComputedStyle(el, pe);
        if (pc.content === "none" || pc.display === "none"
            || pc.position !== "absolute") return;
        const pw = parseFloat(pc.width), ph = parseFloat(pc.height);
        if (!isFinite(pw) || !isFinite(ph) || pw <= 0 || ph <= 0) return;
        if (pc.left === "auto" && pc.right === "auto") return;
        if (pc.top === "auto" && pc.bottom === "auto") return;
        // Контейнер abs-бокса — padding-box позиционированного родителя; в
        // обоих местах deck.css это сам элемент (у него position:relative),
        // поэтому к его рамке прибавляем собственный border.
        const px = (pc.left !== "auto")
          ? r.left + (parseFloat(cs.borderLeftWidth) || 0) + parseFloat(pc.left)
          : r.right - (parseFloat(cs.borderRightWidth) || 0)
            - parseFloat(pc.right) - pw;
        const py = (pc.top !== "auto")
          ? r.top + (parseFloat(cs.borderTopWidth) || 0) + parseFloat(pc.top)
          : r.bottom - (parseFloat(cs.borderBottomWidth) || 0)
            - parseFloat(pc.bottom) - ph;
        const ps = {};
        PROPS.forEach((p) => { ps[p] = pc[p]; });
        items.push({
          path: path.join(".") + (pe === "::before" ? "::b" : "::a"),
          tag: pe, cls: el.getAttribute("class") || "", slot: null,
          authored: false,
          x: round(px - base.left), y: round(py - base.top),
          w: round(pw), h: round(ph), sw: 0, sh: 0, text: "", style: ps,
        });
      });
      // Флаг наследуется от РОДИТЕЛЯ: дети <svg> авторские, сам <svg> — нет.
      const childAuthored = authored || el.tagName.toLowerCase() === "svg";
      kids(el).forEach((c, i) => visit(c, path.concat(i), childAuthored));
    };

    kids(sec).forEach((c, i) => visit(c, [i], false));

    // Стиль самой <section>: фон слайда. Рамка у неё по определению
    // 0,0,w,h, поэтому храним только style — иначе заливку слайда взять
    // неоткуда, обход-то начинается с детей секции.
    const rcs = getComputedStyle(sec);
    const rootStyle = {};
    PROPS.forEach((p) => { rootStyle[p] = rcs[p]; });

    // Значения CSS-переменных, разрешённые НА САМОЙ СЕКЦИИ. Так и надо: в
    // атрибутах SVG цвета записаны как fill="var(--chart-2)", а переменные не
    // глобальны по теме — deck.css:219 переопределяет --bg/--line на уровне
    // слайда (statement-green). Снимать их у :root значило бы покрасить такие
    // слайды не в тот цвет.
    const vars = {};
    VAR_NAMES.forEach((n) => {
      const v = rcs.getPropertyValue(n).trim();
      if (v) vars[n] = v;
    });

    out.push({
      template: sec.getAttribute("data-template"),
      w: round(base.width), h: round(base.height),
      root: rootStyle,
      vars: vars,
      items: items,
    });
  });

  // Базовый кегль там, где его переписал deck.js. autofitStats() сжимает
  // .sr-value под ширину ячейки и ставит результат inline, поэтому в
  // getComputedStyle приходит кегль, подогнанный под РЫБУ. Поправке в питоне
  // нужен исходный: коэффициент сжатия считается от него, и без него
  // пересчитать высоту цифры под настоящий текст нельзя.
  //
  // Проход отдельный и последний по двум причинам. Сброс inline-стиля меняет
  // раскладку — делать это до замеров нельзя. И делается он пакетом (сбросить
  // всё -> прочитать всё -> вернуть всё), иначе каждый элемент заставлял бы
  // браузер пересчитывать страницу из четырёх сотен слайдов заново.
  if (overridden.length) {
    overridden.forEach((o) => { o[1].style.fontSize = ""; });
    overridden.forEach((o) => { o[0].fs0 = getComputedStyle(o[1]).fontSize; });
    overridden.forEach((o) => { o[1].style.fontSize = o[2]; });
  }
  return out;
})()
"""
