"""Оболочка PPTX: тема, мастер, раскладки, имена фигур.

Почему это вообще надо проверять. Оболочка не видна на картинке: фигуры несут
свой srgbClr и своё имя шрифта, поэтому дека с офисной темой и дека с брендовой
выглядят одинаково, и fidelity на 1574 рамках не заметит разницы. Сломается
только редактирование — молча и у пользователя. Значит, читать надо сам пакет.

Каждый тест ниже привязан к конкретной поломке, которая тут и была:
болванка python-pptx отдавала «Office Theme» с Calibri и офисной палитрой,
одиннадцать чужих раскладок, плейсхолдеры мастера шириной на 10 дюймов и
фигуры с именами «Rectangle 37».
"""
import os
import zipfile
from pathlib import Path

os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

import pytest
from lxml import etree

from htmlslides.topptx import pptxshell

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL = ROOT / "skill_assets" / "Cloud.ru_Template_2026.pptx"


@pytest.fixture(scope="module")
def deck(tmp_path_factory):
    """Эталонная дека -> PPTX. Один раз на модуль: это полный проход Jinja."""
    from htmlslides.topptx import convert, fidelity

    src = tmp_path_factory.mktemp("shell") / "deck.html"
    src.write_text(fidelity.build_deck("dark"), encoding="utf-8")
    out = src.with_suffix(".pptx")
    stats = convert.convert(src, out, theme="dark")
    return zipfile.ZipFile(out), stats


def _part(deck, name: str):
    return etree.fromstring(deck[0].read(name))


def _layouts(deck) -> dict[str, etree._Element]:
    """{имя раскладки: её cSld} — по именам, не по номерам частей."""
    out = {}
    for n in deck[0].namelist():
        if n.startswith("ppt/slideLayouts/slideLayout"):
            csld = _part(deck, n).find(P + "cSld")
            out[csld.get("name")] = csld
    return out


# ----------------------------------------------------------------- снимок темы
def test_the_theme_snapshot_is_the_brand_theme_byte_for_byte():
    """data/theme.xml — копия темы официального шаблона, а не «похожая».

    Смысл копии именно в побайтности: при копировании фигур между нашей декой и
    собранной руками презентацией scheme-цвета совпадут по имени И значению,
    то есть вставка не перекрасится. Любая «улучшенная» палитра это ломает.

    Тест же нужен против обратного — против замороженного снимка: обновят
    шаблон, а у нас останется палитра 2026 года, и никто не узнает.
    """
    if not OFFICIAL.exists():
        pytest.skip(f"нет {OFFICIAL.name} — нечем сверять")
    want = zipfile.ZipFile(OFFICIAL).read("ppt/theme/theme1.xml")
    assert pptxshell.THEME.read_bytes() == want, (
        "снимок брендовой темы разошёлся с шаблоном; обновить:\n"
        "  python -c \"import zipfile,pathlib;"
        "pathlib.Path('htmlslides/topptx/data/theme.xml').write_bytes("
        "zipfile.ZipFile('skill_assets/Cloud.ru_Template_2026.pptx')"
        ".read('ppt/theme/theme1.xml'))\"")


def test_the_panose_is_the_one_the_brand_itself_writes():
    """Panose в <a:latin> — тот же, что бренд пишет в objectDefaults шаблона.

    Три числа (panose, pitchFamily, charset) — единственная подсказка
    подборщику шрифтов на машине без SB Sans, и опечатку в них глазами не
    видно. Сверять есть с чем: в objectDefaults брендовой темы те же байты,
    снятые у файла шрифта независимо (fontTools, OS/2):
    2 11 5 3 4 5 4 2 2 4 -> 020B0503040504020204.
    """
    root = etree.fromstring(pptxshell.THEME.read_bytes())
    latin = root.find(f"{A}objectDefaults/{A}spDef//{A}latin")
    assert latin is not None, "в брендовой теме нет objectDefaults/spDef/latin"
    assert latin.get("typeface").startswith(pptxshell.FONT), (
        "бренд задаёт по умолчанию другое семейство")
    assert pptxshell.PANOSE == latin.get("panose")
    assert pptxshell.PITCH_FAMILY == latin.get("pitchFamily")
    assert pptxshell.CHARSET == latin.get("charset")


# ------------------------------------------------------------- тема в экспорте
def test_the_exported_theme_is_the_brand_one_and_not_office(deck):
    """В пакете брендовая палитра и SB Sans, а не Office Theme и Calibri."""
    th = _part(deck, "ppt/theme/theme1.xml")
    assert "Cloud.ru" in th.get("name")
    sc = th.find(f"{A}themeElements/{A}clrScheme")
    got = {c.tag.split("}")[1]: c[0].get("val") for c in sc}
    assert got["accent1"] == "26D07C", "брендовый зелёный не в accent1"
    assert got["dk1"] == "222222" and got["lt1"] == "FFFFFF"
    # Офисную болванку узнаём по accent1 4F81BD — его быть не должно нигде.
    assert "4F81BD" not in got.values()
    for which in ("majorFont", "minorFont"):
        latin = th.find(f"{A}themeElements/{A}fontScheme/{A}{which}/{A}latin")
        assert latin.get("typeface") == pptxshell.FONT
        assert latin.get("panose") == pptxshell.PANOSE


def test_the_font_scheme_names_exactly_what_the_runs_name(deck):
    """Прогоны называют шрифт схемы или его начертание Semibold — и только их.

    В брендовом шаблоне minor = «SB Sans Display Regular» — имя начертания, а
    не семейства. Возьми мы схему как есть, новая надпись пришла бы шрифтом с
    другим именем, и PowerPoint считал бы деку двухшрифтовой. Поэтому схема
    называет семейство («SB Sans Display»), обычные прогоны — его же, а
    полужирные — начертание «SB Sans Display Semibold»: это вес 600, который
    флагом b=1 (вес 700) не выражается (см. svgshapes.face_for).
    """
    th = _part(deck, "ppt/theme/theme1.xml")
    minor = th.find(f"{A}themeElements/{A}fontScheme/{A}minorFont/{A}latin")
    sld = _part(deck, "ppt/slides/slide1.xml")
    runs = {e.get("typeface") for e in sld.findall(f".//{A}rPr/{A}latin")}
    assert runs, "на первом слайде нет ни одного прогона с явным шрифтом"
    fam = minor.get("typeface")
    assert runs <= {fam, f"{fam} Semibold"}
    assert fam in runs, "обычное начертание со слайда исчезло"


# ---------------------------------------------------------------- раскладки
def test_no_office_layout_survives(deck):
    """Офисных раскладок не осталось ни одной — ни в галерее, ни в пакете."""
    names = set(_layouts(deck))
    assert not names & {"Blank", "Title Slide", "Title and Content",
                        "Comparison", "Two Content", "Title Only"}


def test_every_slide_sits_on_the_layout_of_its_template(deck):
    """Слайд лежит на раскладке своего шаблона, и имя у неё русское.

    Это то, что пользователь увидит в «Новый слайд»: «Две карточки», а не
    «slideLayout18» и не «Title and Content». Заодно проверяется, что раскладка
    одна на шаблон, а не одна на слайд: 112 слайдов эталонной деки собраны из
    25 шаблонов.
    """
    from htmlslides.library import TemplateLibrary

    titles = {s.display_name for s in TemplateLibrary.load().templates}
    names = set(_layouts(deck))
    assert names <= titles, f"раскладка не из каталога: {sorted(names - titles)}"
    assert len(names) == deck[1]["layouts"]
    assert 20 <= len(names) <= 25, f"ожидали раскладку на шаблон, вышло {len(names)}"


def test_the_layout_takes_the_background_of_its_section(deck):
    """Фон раскладки — замеренный фон секции, а не общий фон деки.

    У statement-green секция зелёная; без этого «Новый слайд -> Акцентный
    тезис (зелёный)» дал бы графитовый слайд, и пользователь решил бы, что
    макет сломан.
    """
    def bg(name):
        node = _layouts(deck)[name].find(f"{P}bg//{A}srgbClr")
        return node is not None and node.get("val")

    assert bg("Акцентный тезис (зелёный)") == "26D07C"
    assert bg("Акцентный тезис") == "222222"


def test_layouts_carry_no_placeholders(deck):
    """Ни одного плейсхолдера: вся разметка деки уже стоит на слайде.

    У офисной «Blank» их три — дата, подвал и номер слайда. Номер дека рисует
    сама (p.chrome-note), и второй, унаследованный, встал бы не на место.
    """
    for name, csld in _layouts(deck).items():
        assert csld.findall(f"{P}spTree/{P}sp") == [], f"{name}: лишние фигуры"


# -------------------------------------------------------------------- мастер
def test_the_master_fits_the_wide_canvas(deck):
    """Плейсхолдеры мастера растянуты под 1920px, а не оставлены на 10 дюймах.

    На слайдах деки плейсхолдеров нет, так что «Образец слайдов» — это
    единственное место, где видно болванку. Было: номер слайда кончался на
    8 686 800 EMU при ширине полотна 12 192 000, то есть в двух третях.
    """
    pres = _part(deck, "ppt/presentation.xml")
    width = int(pres.find(P + "sldSz").get("cx"))
    m = _part(deck, "ppt/slideMasters/slideMaster1.xml")
    rights = []
    for sp in m.findall(f"{P}cSld/{P}spTree/{P}sp"):
        off, ext = sp.find(f".//{A}off"), sp.find(f".//{A}ext")
        if off is None or ext is None:
            continue
        rights.append(int(off.get("x")) + int(ext.get("cx")))
    assert rights, "у мастера не нашлось ни одного плейсхолдера с рамкой"
    assert max(rights) <= width, "плейсхолдер вылез за полотно"
    # Болванка свёрстана на 9 144 000 EMU; если бы растяжку не сделали,
    # правый край остался бы заметно левее.
    assert max(rights) > 9_144_000 * 0.95


def test_new_text_is_readable_on_a_dark_deck(deck):
    """Цвет текста по умолчанию — цвет деки, иначе графит на графите.

    clrMap мы не трогаем (в брендовом шаблоне все 102 раскладки живут на
    masterClrMapping, и вставка из ручной деки не должна перекрашиваться),
    поэтому tx1 остаётся графитом. Значит, читаемость даёт только явный цвет
    в otherStyle мастера и в defaultTextStyle презентации.
    """
    m = _part(deck, "ppt/slideMasters/slideMaster1.xml")
    assert dict(m.find(P + "clrMap").attrib)["bg1"] == "lt1", (
        "clrMap подменён — вставка из ручной деки перекрасится")
    for part, path in (
            (m, f"{P}txStyles/{P}otherStyle/{A}lvl1pPr/{A}defRPr/{A}solidFill"),
            (_part(deck, "ppt/presentation.xml"),
             f"{P}defaultTextStyle/{A}lvl1pPr/{A}defRPr/{A}solidFill")):
        fill = part.find(path)
        assert fill is not None, f"нет цвета по умолчанию: {path}"
        assert fill.find(A + "srgbClr").get("val") == "FFFFFF"


# ------------------------------------------------------------- имена фигур
def test_shapes_are_named_by_selector_not_rectangle_37(deck):
    """Имя фигуры — селектор из разметки; «Область выделения» становится картой.

    Без этого на слайде с двумя сотнями фигур список выглядит как
    «Прямоугольник 37 / TextBox 112» и нужную подпись в нём не найти.
    """
    sld = _part(deck, "ppt/slides/slide80.xml")       # two-col-cards со смесью
    names = [e.get("name") for e in sld.findall(f".//{P}cNvPr")
             if e.get("name")]
    assert names
    assert not any(n.startswith(("Rectangle", "TextBox", "Picture", "Прямоуг"))
                   for n in names), f"безымянные фигуры остались: {names[:5]}"
    assert any("tcc-" in n for n in names), f"нет селекторов карточек: {names}"
    assert any(n.endswith(" текст") for n in names), "надписи не помечены"


def test_the_selector_carries_tag_class_and_slot():
    """Разбор имени по частям — на случай, если разметка их переставит."""
    from lxml import html as LH

    from htmlslides.topptx.convert import _sel

    el = LH.fragment_fromstring(
        '<h1 class="cover-title big" data-slot="title">A</h1>')

    assert _sel(el) == "h1.cover-title[title]"
    assert _sel(LH.fragment_fromstring("<div>A</div>")) == "div"
    assert _sel(LH.fragment_fromstring('<div class="x">A</div>')) == "div.x"
