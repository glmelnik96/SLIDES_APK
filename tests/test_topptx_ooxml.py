"""Целостность пакета PPTX: порядок детей, ссылки, id фигур, типы частей.

Зачем отдельный модуль. Остальные тесты проверяют СОДЕРЖАНИЕ — что в теме
брендовая палитра, что рамка встала на замеренное место. Этот проверяет, что
PowerPoint вообще согласится открыть файл без диалога «обнаружена проблема с
содержимым, восстановить?». Восстановление — не косметика: PowerPoint выкидывает
часть, которую не понял, и дека теряет слайды молча.

Четыре вещи, на которых PowerPoint спотыкается, и все четыре мы правим руками,
минуя объектную модель python-pptx (подмена blob темы, вставка `p:bg`, сборка
раскладки из строки, цвет в `a:defRPr`), то есть без проверки они ничем не
прикрыты:

  * ПОРЯДОК ДЕТЕЙ. В OOXML почти всё — xsd:sequence, и порядок обязателен.
    Ловушка в том, что lxml про схему не знает: `rpr.insert(0, fill)` проходит
    молча, а файл уже невалиден, потому что в `a:defRPr` сначала идёт `a:ln`.
    Ровно это тут и было, и ровно поэтому Shell.dress теперь ставит цвет через
    Font.color — python-ppt знает последовательность.
  * ССЫЛКИ. `r:embed` на картинку, `r:id` на раскладку: если id не объявлен
    в .rels или цель не существует, часть не грузится.
  * ID ФИГУР. `cNvPr/@id` уникален внутри слайда.
  * ТИПЫ ЧАСТЕЙ. У части без Override и без Default по расширению нет типа.

Откуда берётся эталон порядка. Не из схем ECMA-376 (их нет в репозитории и
тянуть их в зависимости ради теста не стоит), а из самого официального шаблона:
515 XML-частей, 102 раскладки, всё написано самим PowerPoint, значит порядок в
нём по определению валиден. Из него собирается таблица пар «тег A встречался
только раньше тега B у такого родителя» — 2906 пар на 182 типа родителей, — и
нарушением считается наш файл, где A стоит позже B. Эмпирика, а не схема: зато
проверяет сразу весь пакет и ровно по тому образцу, который и открывает
PowerPoint.

Почему хватает деки с рыбой на 112 слайдов, а не всего перебора на 1651.
У геометрии щель тут была бы: 112 слайдов — это 27 деревьев из 1651 (1.6%),
потому что четыре слайда на шаблон отличаются длиной текста, а не структурой,
и дают один ключ (её закрывает fidelity.compare_all). Но эти проверки смотрят
не на числа, а на словарь: какие теги есть и какие пары «родитель -> ребёнок»
встречаются. Он замерен у обеих дек и совпадает ровно: 223 тега и 342 пары,
разницы нет ни в одну сторону. Больше слайдов добавляют строк в таблице, а не
новых видов узлов, — значит медленная дека тут ничего бы не поймала, и 3.4 с
против 19 с экспорта взяты не в обмен на покрытие.
"""
import collections
import os
import posixpath
import zipfile
from pathlib import Path

os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

import pytest
from lxml import etree

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = ROOT / "skill_assets" / "Cloud.ru_Template_2026.pptx"

# Родители, у которых дети — повторяющаяся группа выбора, а не
# последовательность: порядка там нет вовсе, судить по нему нельзя.
# `a:path` держит EG_Path2DCommand с maxOccurs="unbounded", и путь из двух
# контуров законно читается «moveTo lnTo moveTo lnTo» — то есть lnTo раньше
# moveTo. В официальном шаблоне все пути односегментные, поэтому таблица без
# этого исключения давала 24 ложных нарушения на наших SVG-иконках.
# Остальные unbounded-контейнеры (pathLst, avLst, gdLst, gsLst, cxnLst) держат
# детей ОДНОГО тега, а одинаковые теги в пары не попадают, и правка им не нужна.
FREE_ORDER = {A + "path"}


def _pairs(el):
    """Пары детей (раньше, позже), по которым можно судить о порядке."""
    if el.tag in FREE_ORDER:
        return
    kids = [k.tag for k in el if isinstance(k.tag, str)]
    for i, a in enumerate(kids):
        for b in kids[i + 1:]:
            if a != b:
                yield a, b


def _xml_parts(z: zipfile.ZipFile):
    for n in z.namelist():
        if n.endswith((".xml", ".rels")):
            try:
                yield n, etree.fromstring(z.read(n))
            except etree.XMLSyntaxError:            # pragma: no cover
                pytest.fail(f"{n}: не разбирается как XML")


def _short(tag: str) -> str:
    return tag.split("}")[-1]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Обе темы -> PPTX. Светлая важна: фон мастера и цвет текста у неё другие."""
    from htmlslides.topptx import convert, fidelity

    out = {}
    d = tmp_path_factory.mktemp("ooxml")
    for theme in ("dark", "light"):
        src = d / f"{theme}.html"
        src.write_text(fidelity.build_deck(theme), encoding="utf-8")
        pptx = src.with_suffix(".pptx")
        convert.convert(src, pptx, theme=theme)
        out[theme] = zipfile.ZipFile(pptx)      # путь потом берём из .filename
    return out


@pytest.fixture(scope="module")
def order_table():
    """{(родитель, A, B): сколько раз} — пары, которые PowerPoint пишет только так."""
    if not OFFICIAL.exists():
        pytest.skip(f"нет {OFFICIAL.name} — эталон порядка брать негде")
    seen = collections.Counter()
    with zipfile.ZipFile(OFFICIAL) as z:
        for _, root in _xml_parts(z):
            for el in root.iter():
                for a, b in _pairs(el):
                    seen[(el.tag, a, b)] += 1
    # Пару, встреченную в обе стороны, шаблон порядком не считает.
    table = {k: v for k, v in seen.items() if (k[0], k[2], k[1]) not in seen}
    assert len(table) > 2000, (
        f"таблица порядка выродилась до {len(table)} пар — проверка стала "
        f"пустой; шаблон подменили или _pairs сломался")
    return table


# ------------------------------------------------------------- порядок детей
@pytest.mark.parametrize("theme", ("dark", "light"))
def test_child_order_matches_what_powerpoint_itself_writes(built, order_table,
                                                           theme):
    """Ни один наш элемент не нарушает порядок, принятый в шаблоне бренда.

    Падение означает, что PowerPoint на этом файле предложит восстановление.
    В сообщении — часть, родитель и пара тегов, чтобы искать не по всему пакету.
    """
    bad = []
    for name, root in _xml_parts(built[theme]):
        for el in root.iter():
            for a, b in _pairs(el):
                hits = order_table.get((el.tag, b, a))
                if hits:
                    bad.append(f"{name}: в <{_short(el.tag)}> тег "
                               f"<{_short(a)}> стоит перед <{_short(b)}>, "
                               f"а шаблон {hits} раз пишет наоборот")
    assert not bad, f"нарушений порядка {len(bad)}:\n" + "\n".join(
        sorted(set(bad))[:10])


def test_the_colour_of_default_text_lands_after_a_line_not_before_it():
    """`a:solidFill` в defRPr встаёт по схеме, даже если там уже есть `a:ln`.

    Явный тест на ловушку: в болванке python-pptx у defRPr нет `a:ln`, поэтому
    неправильная вставка по индексу 0 давала валидный файл СЛУЧАЙНО и сломалась
    бы на первой же болванке с обводкой. Проверяем на defRPr с `a:ln`.
    """
    from pptx.oxml import parse_xml

    from htmlslides.topptx.pptxshell import Shell

    # defRPr с обводкой и уже стоящей градиентной заливкой: правильная вставка
    # обязана сохранить a:ln первым, заменить gradFill и не уехать за latin.
    prs_el = parse_xml(
        f'<p:presentation xmlns:a="{A[1:-1]}" xmlns:p="{P[1:-1]}">'
        f'<p:defaultTextStyle><a:lvl1pPr><a:defRPr sz="1800">'
        f'<a:ln w="100"/><a:gradFill/><a:latin typeface="X"/>'
        f'</a:defRPr></a:lvl1pPr></p:defaultTextStyle></p:presentation>')
    master_el = parse_xml(f'<p:sldMaster xmlns:a="{A[1:-1]}" '
                          f'xmlns:p="{P[1:-1]}"/>')

    class _Fake(Shell):              # ровно то, что dress трогает у оболочки
        def __init__(self):
            self.prs = type("_P", (), {"element": prs_el})()
            self.master = type("_M", (), {"element": master_el})()

    _Fake().dress(None, "FFFFFF")
    rpr = prs_el.find(f"{P}defaultTextStyle/{A}lvl1pPr/{A}defRPr")
    tags = [_short(k.tag) for k in rpr]
    assert tags == ["ln", "solidFill", "latin"], tags
    assert rpr.find(f"{A}solidFill/{A}srgbClr").get("val") == "FFFFFF"


# ------------------------------------------------------------------- ссылки
@pytest.mark.parametrize("theme", ("dark", "light"))
def test_every_relationship_points_at_a_part_that_exists(built, theme):
    """Цель каждой связи лежит в архиве (внешние ссылки не в счёт)."""
    z = built[theme]
    names = set(z.namelist())
    bad = []
    for n in names:
        if not n.endswith(".rels"):
            continue
        owner = posixpath.dirname(posixpath.dirname(n))
        for r in etree.fromstring(z.read(n)):
            if r.get("TargetMode") == "External":
                continue
            full = posixpath.normpath(posixpath.join(owner, r.get("Target")))
            if full not in names:
                bad.append(f"{n}: {r.get('Id')} -> {r.get('Target')}")
    assert not bad, "связи в пустоту:\n" + "\n".join(bad[:10])


@pytest.mark.parametrize("theme", ("dark", "light"))
def test_every_rid_in_the_xml_is_declared_in_the_rels(built, theme):
    """Каждый r:id/r:embed объявлен в .rels своей части.

    Это ловит ровно ту ошибку, которой кончается ручная сборка раскладки: сам
    `sldLayoutId` в мастере добавлен, а связь мастер -> раскладка забыта.
    """
    z = built[theme]
    bad = []
    for name, root in _xml_parts(z):
        if name.endswith(".rels"):
            continue
        rels = posixpath.join(posixpath.dirname(name), "_rels",
                              posixpath.basename(name) + ".rels")
        try:
            have = {r.get("Id") for r in etree.fromstring(z.read(rels))}
        except KeyError:
            have = set()
        for el in root.iter():
            for k, v in el.attrib.items():
                if k.startswith(RNS) and v and v not in have:
                    bad.append(f"{name}: <{_short(el.tag)} "
                               f"{_short(k)}={v}> нет в {rels}")
    assert not bad, "необъявленные ссылки:\n" + "\n".join(bad[:10])


# ---------------------------------------------------------------- id и типы
@pytest.mark.parametrize("theme", ("dark", "light"))
def test_shape_ids_are_unique_inside_every_slide(built, theme):
    """`cNvPr/@id` не повторяется в слайде, раскладке и мастере.

    Повтор PowerPoint не всегда ломает, но анимация и панель выделения начинают
    адресовать не ту фигуру. На слайде деки до 60 фигур, и собираем мы их в
    цикле, так что проверка не теоретическая.
    """
    bad = []
    for name, root in _xml_parts(built[theme]):
        if not any(k in name for k in ("/slides/", "slideLayout", "slideMaster")):
            continue
        ids = [e.get("id") for e in root.iter(P + "cNvPr")]
        dup = [k for k, c in collections.Counter(ids).items() if c > 1]
        if dup:
            bad.append(f"{name}: повторы id {sorted(dup)[:5]}")
    assert not bad, "\n".join(bad[:10])


@pytest.mark.parametrize("theme", ("dark", "light"))
def test_every_part_has_a_content_type(built, theme):
    """У части есть либо Override, либо Default по расширению.

    Раскладки мы создаём через SlideLayoutPart.load с типом в руках — если тип
    перепутать, пакет открывается, а раскладка просто не видна в галерее.
    """
    z = built[theme]
    ct = etree.fromstring(z.read("[Content_Types].xml"))
    default = {e.get("Extension").lower() for e in ct
               if _short(e.tag) == "Default"}
    over = {e.get("PartName").lstrip("/") for e in ct
            if _short(e.tag) == "Override"}
    bad = [n for n in z.namelist()
           if n != "[Content_Types].xml" and n not in over
           and n.rsplit(".", 1)[-1].lower() not in default]
    assert not bad, "части без типа содержимого:\n" + "\n".join(bad[:10])

    # Раскладки должны быть именно Override: Default для .xml в пакете нет.
    layouts = [n for n in z.namelist() if "slideLayouts/slideLayout" in n
               and n.endswith(".xml")]
    assert layouts, "раскладок в пакете нет вообще"
    assert all(n in over for n in layouts), [n for n in layouts if n not in over]


# -------------------------------------------------------------- чтение назад
@pytest.mark.parametrize("theme", ("dark", "light"))
def test_a_fresh_reader_finds_everything_we_wrote(built, theme):
    """Пакет читается заново, и в нём на месте фигуры, текст, рамки и связи.

    Это самая близкая к PowerPoint проверка, которую можно сделать офлайн.
    Остальные тесты читают XML как XML: они увидят порядок и ссылки, но не
    заметят, если сериализация потеряет фигуру или текст. Здесь пакет
    открывается с нуля ЧУЖИМ кодом (python-pptx), который идёт по связям —
    слайд -> раскладка -> мастер -> тема, — и падает, если цепочка рвётся.

    Сверяем с пройденным: fidelity сравнивает с браузером то, что конвертер
    держал в памяти, а не то, что доехало до диска. Между этими двумя числами и
    живут потери при сохранении.
    """
    from lxml import html as LH
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE
    from pptx.util import Emu

    prs = Presentation(built[theme].filename)
    # Сколько секций в деке, столько и слайдов. Число не зашито: каталог растёт
    # (diagram добавил секции), и зашитое 112 падало не по делу.
    src = built[theme].filename.rsplit(".", 1)[0] + ".html"
    want = len(LH.parse(src).xpath('//section[contains(@class,"slide")]'))
    assert want > 100, want
    assert len(prs.slides) == want, (len(prs.slides), want)
    assert prs.slide_width == Emu(1920 * 6350)
    assert prs.slide_height == Emu(1080 * 6350)

    shapes = texts = lines = 0
    for i, slide in enumerate(prs.slides, start=1):
        # Цепочка связей: если раскладка или мастер не разрешились, тут и упадёт.
        assert slide.slide_layout.name, f"слайд {i}: у раскладки нет имени"
        assert slide.slide_layout.slide_master is not None
        for sh in slide.shapes:
            shapes += 1
            assert sh.name, f"слайд {i}: фигура без имени"
            # Геометрия должна доехать целой: None здесь означает, что xfrm
            # потерялся при сериализации и фигура встанет в левый верхний угол.
            assert sh.left is not None and sh.top is not None, f"{sh.name}: нет xfrm"
            if sh.shape_type == MSO_SHAPE_TYPE.LINE:
                # Коннектор из <line> — у горизонтальной линии высота ровно 0,
                # и это правильно: коннектор задан началом и концом, а не
                # рамкой. Нулевыми обе стороны быть всё же не должны — это
                # означало бы линию в точку.
                lines += 1
                assert sh.width or sh.height, f"{sh.name}: линия в точку"
            else:
                assert sh.width and sh.height, f"{sh.name}: нулевой размер"
            if sh.has_text_frame and sh.text_frame.text.strip():
                texts += 1
    assert shapes > 1000, shapes
    assert texts > 500, texts
    assert lines, "ни одного коннектора — ветка <line> осталась непройденной"

    # Тема доезжает именно наша, и доезжает через связь мастера, а не случайно.
    theme_part = prs.slide_masters[0].part.part_related_by(
        "http://schemas.openxmlformats.org/officeDocument/2006/"
        "relationships/theme")
    root = etree.fromstring(theme_part.blob)
    assert root.get("name").startswith("Тема Cloud.ru_2026"), root.get("name")
    latin = root.find(f"{A}themeElements/{A}fontScheme/{A}minorFont/{A}latin")
    assert latin.get("typeface") == "SB Sans Display"
