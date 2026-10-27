"""Оболочка PPTX: тема, мастер и раскладки. Отдельно от геометрии слайдов.

Зачем это нужно. Объекты на слайдах давно нативные, но сам файл выходил на
болванке python-pptx: тема «Office Theme», шрифт Calibri, офисная палитра и
одиннадцать чужих раскладок («Сравнение», «Заголовок и вертикальный текст»).
При ПРОСМОТРЕ это не видно — каждая фигура несёт свой srgbClr и своё имя
шрифта, — зато редактирование ломается тихо:

  * новая надпись получает Calibri 18 вместо SB Sans Display;
  * в палитре строка «Цвета темы» — офисная синь 4F81BD, брендового зелёного
    26D07C там нет вовсе;
  * «Новый слайд» предлагает офисные макеты, а не макеты деки;
  * новая фигура получает из темы тень (outerShdw blurRad=40000 во всех трёх
    effectStyle болванки) — ровно поэтому в convert._rect стоит
    shadow.inherit=False, иначе плашки деки были бы с тенью.

Откуда берётся тема. Не из нашей головы: палитра, формат-схема и
objectDefaults скопированы из `ppt/theme/theme1.xml` официального
`skill_assets/Cloud.ru_Template_2026.pptx` — файл data/theme.xml, 5868 байт,
байт в байт. Это даёт то, чего реконструкция по CSS дать не может: при
копировании фигур между нашей декой и собранной руками презентацией
scheme-цвета совпадают по ИМЕНИ И ЗНАЧЕНИЮ, то есть вставка не перекрашивается.
Тест сверяет снимок с архивом и падает, когда шаблон обновили.

Сам 28-мегабайтный шаблон основой экспорта при этом не становится: слайды и
геометрия — из каталога HTML-шаблонов, как и раньше. Отсюда и граница — берём
только `theme1.xml`, ни одной раскладки.

Где CSS и официальный шаблон расходятся (замер 07.10.2026) — расхождения
оставлены как есть:
    accent2  0062FF  в шаблоне   против  --cl-ultramarine 0063FF в deck.css
    accent4  CFF400  в шаблоне   против  --cl-yellow      CFF500
    accent3  18F4CF  (бирюза)    в deck.css такого токена нет вообще
Чинить нечего: наши фигуры пишут цвет явным srgbClr и схему не спрашивают, так
что единственная работа схемы — совместимость с ручными деками, а для неё
нужно совпадение с шаблоном, а не с CSS.

Шрифт не встраивается (лицензия ParaType), поэтому в `<a:latin>` идёт имя плюс
panose — подсказка подборщику на машине, где SB Sans не установлен.
"""
from __future__ import annotations

from pathlib import Path

from lxml import etree
from pptx.dml.color import RGBColor
from pptx.opc.constants import CONTENT_TYPE as CT
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn
from pptx.parts.slide import SlideLayoutPart
from pptx.text.text import Font

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"

THEME = Path(__file__).parent / "data" / "theme.xml"

# Болванка python-pptx — слайд 10x7.5 дюйма; наша дека 13 1/3x7.5. Рамки
# плейсхолдеров мастера снята с узкой, поэтому их растягиваем (см. _restretch).
STOCK_W = 9144000

FONT = "SB Sans Display"

# Panose снят с установленного файла шрифта (fontTools, таблица OS/2):
#   SB Sans Display Regular -> 2 11 5 3 4 5 4 2 2 4 -> 020B0503040504020204
# Пишем ЕГО, а не panose запасного Verdana, хотя в CSS fallback именно Verdana.
# Два независимых довода.
#   1. Так делает сам бренд: в objectDefaults официального шаблона стоит
#      panose="020B0503040504020204" pitchFamily="34" charset="0" — те же
#      байты, что мы намерили у файла шрифта.
#   2. Замер ширины. Одна и та же строка (122 знака: кириллица, латиница,
#      цифры), сумма ширин глифов в em:
#        SB Sans Display 57.44    Verdana  62.38  (+8.6%)
#        Arial           56.07  (-2.4%)   Tahoma 54.88 (-4.5%)
#        Helvetica Neue  56.50  (-1.6%)
#      Verdana — САМЫЙ далёкий из доступных кандидатов, и строка в нём вылезет
#      из рамки, снятой по SB Sans. Правильный panose отправляет подборщик к
#      соседям по классификации, а они ближе. Там, где SB Sans установлен (а
#      дека и делается там), panose не участвует вовсе: совпало имя.
PANOSE = "020B0503040504020204"
PITCH_FAMILY = "34"        # variable pitch (2) + swiss, без засечек (0x20)
CHARSET = "0"

_LAYOUT_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"\
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"\
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"\
 preserve="1">
<p:cSld name=""><p:spTree>
<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>\
<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
</p:spTree></p:cSld>
<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sldLayout>"""


def _solid_bg(hexrgb: str) -> etree._Element:
    """`<p:bg>` со сплошной заливкой. Фон слайда задаётся ровно так же."""
    bg = etree.Element(P + "bg")
    pr = etree.SubElement(bg, P + "bgPr")
    etree.SubElement(etree.SubElement(pr, A + "solidFill"),
                     A + "srgbClr").set("val", hexrgb)
    etree.SubElement(pr, A + "effectLst")
    return bg


def set_alpha(fmt, alpha: float) -> bool:
    """Полупрозрачность сплошной заливки или линии. -> поставлена ли.

    `fmt` — это `shape.fill` или `shape.line.fill`, уже переведённый в solid()
    и с выставленным цветом.

    Почему руками по XML. В python-pptx 1.0.2 у FillFormat НЕТ свойства
    transparency — ни сеттера, ни геттера (`dir(FillFormat)`: back_color,
    background, fore_color, from_fill_parent, gradient, gradient_angle,
    gradient_stops, pattern, patterned, solid, type). Присваивание
    `fill.transparency = 0.86` не падает и не предупреждает: Python просто
    заводит на объекте посторонний атрибут, который никто никогда не прочитает,
    а в файл уходит `<a:srgbClr val="31C56E"/>` без альфы. Ровно так и было —
    и стоило это дорого: заливка области line-chart задана в разметке как
    fill-opacity="0.14", то есть еле заметный зелёный wash по тёмному фону, а в
    PowerPoint приезжала СПЛОШНЫМ зелёным во всю высоту графика. По разнице
    «чернил» со снимком браузера line-chart был худшим шаблоном деки: 18.5% при
    1–3% у остальных.

    В DrawingML прозрачность живёт не у заливки, а у цвета — дочерним
    `<a:alpha val="..."/>` внутри `<a:srgbClr>`, в тысячных долях процента
    (100000 == непрозрачно). Поэтому ищем цветовой узел внутри solidFill и
    дописываем альфу в него.
    """
    if not 0.0 <= alpha < 1.0:
        return False                      # 1.0 и мусор: писать нечего
    solid = fmt._xPr.find(A + "solidFill")
    if solid is None:
        return False
    clr = solid.find(A + "srgbClr")
    if clr is None:
        clr = solid.find(A + "schemeClr")
    if clr is None:
        return False
    for old in clr.findall(A + "alpha"):
        clr.remove(old)
    etree.SubElement(clr, A + "alpha").set(
        "val", str(max(0, min(100000, int(round(alpha * 100000))))))
    return True


class Shell:
    """Тема, мастер и раскладки одной деки.

    Порядок в __init__ не случаен: офисные раскладки удаляются ДО того, как
    появится первый слайд. python-pptx отказывается удалять раскладку, на
    которую кто-то ссылается (SlideLayouts.remove), и правильно делает —
    иначе слайд остался бы без родителя.
    """

    def __init__(self, prs, slide_w: int, slide_h: int, theme_name: str):
        self.prs = prs
        self.master = prs.slide_masters[0]
        prs.slide_width, prs.slide_height = slide_w, slide_h
        self._restretch(slide_w)
        self._retheme(theme_name)
        for layout in list(self.master.slide_layouts):
            self.master.slide_layouts.remove(layout)
        # Раскладка на шаблон, по первому его слайду: {id шаблона: раскладка}.
        self.layouts: dict[str, object] = {}

    # ----------------------------------------------------------------- мастер
    def _restretch(self, slide_w: int) -> None:
        """Плейсхолдеры мастера — под нашу ширину слайда.

        Болванка python-pptx свёрстана на 10 дюймов, и после смены размера
        слайда её заголовок с подвалом занимали левые три четверти полотна.
        На слайдах деки это не видно (там нет плейсхолдеров), но «Вид ->
        Образец слайдов» открывается кривым, а это тоже часть редактируемости.
        """
        k = slide_w / STOCK_W
        for sh in self.master.shapes:
            if sh.left is None or sh.width is None:
                continue
            sh.left, sh.width = int(round(sh.left * k)), int(round(sh.width * k))

    def dress(self, bg: str | None, fg: str | None) -> None:
        """Фон мастера и цвет текста по умолчанию — как у деки.

        Цвет нужен из-за того, как бренд решает тёмные макеты: в официальном
        шаблоне все 102 раскладки живут на `masterClrMapping`, а тёмные просто
        красят фон в `schemeClr tx1` и выставляют цвет текста явно. Мы делаем
        так же — не трогаем clrMap, иначе вставка из ручной деки перекрасится.
        Но тогда на тёмной деке новая надпись пришла бы цветом tx1 (графит на
        графите), поэтому уровень 1 в otherStyle мастера и в defaultTextStyle
        презентации получает цвет деки.

        Заливку ставим не вручную, а через Font.color: в defRPr порядок детей
        фиксированный (ln, потом группа заливки, потом latin/ea/cs), и вставка
        по индексу его ломает — PowerPoint на такой файл предлагает
        «восстановить». python-pptx знает последовательность и вставляет в своё
        место, попутно убирая заливку, которая там была.
        """
        if bg:
            csld = self.master.element.find(P + "cSld")
            old = csld.find(P + "bg")
            if old is not None:
                csld.remove(old)
            csld.insert(0, _solid_bg(bg))        # bg идёт первым в cSld
        if not fg:
            return
        for lvl in (self.master.element.find(f"{P}txStyles/{P}otherStyle/"
                                             f"{A}lvl1pPr"),
                    self.prs.element.find(f"{P}defaultTextStyle/{A}lvl1pPr")):
            if lvl is None:
                continue
            rpr = lvl.find(A + "defRPr")
            if rpr is not None:
                Font(rpr).color.rgb = RGBColor.from_string(fg)

    # -------------------------------------------------------------------- тема
    def _retheme(self, theme_name: str) -> None:
        """Подменить тему пакета брендовой.

        Часть темы в python-pptx — непрозрачный Part без объектной модели
        (в отличие от слайдов и мастера), поэтому правим blob. Это дешевле и
        честнее, чем собирать fmtScheme из 2796 байт градиентов руками.
        """
        root = etree.fromstring(THEME.read_bytes())
        root.set("name", f"{root.get('name')} ({theme_name})")
        # Шрифтовую схему берём НЕ из шаблона: там minor = «SB Sans Display
        # Regular», имя начертания, а не семейства. Наши прогоны пишут
        # «SB Sans Display», и новый текст должен совпадать с ними буквально,
        # иначе PowerPoint считает это другим шрифтом.
        for which in ("majorFont", "minorFont"):
            latin = root.find(f"{A}themeElements/{A}fontScheme/{A}{which}/{A}latin")
            latin.attrib.clear()
            latin.set("typeface", FONT)
            latin.set("panose", PANOSE)
            latin.set("pitchFamily", PITCH_FAMILY)
            latin.set("charset", CHARSET)
        part = self.master.part.part_related_by(RT.THEME)
        part._blob = etree.tostring(root, xml_declaration=True,
                                    encoding="UTF-8", standalone=True)

    # --------------------------------------------------------------- раскладки
    def layout_for(self, template_id: str, name: str, bg: str | None):
        """Раскладка шаблона; создаётся при первом слайде этого шаблона.

        Имя — русское из каталога («Две карточки»), именно оно появится в
        «Новый слайд». Фон — замеренный фон секции: у statement-green он
        зелёный, и без него новый слайд этого макета вышел бы на фоне деки.

        Плейсхолдеров нет сознательно. Весь текст деки уже стоит на слайде
        собственными рамками с замеренной геометрией; плейсхолдер поверх
        означал бы вторую рамку того же заголовка, причём не на месте. У
        офисной «Blank» их три (дата, подвал, номер) — и номер слайда дека
        рисует сама.
        """
        got = self.layouts.get(template_id)
        if got is not None:
            return got

        root = etree.fromstring(_LAYOUT_XML.encode("utf-8"))
        csld = root.find(P + "cSld")
        csld.set("name", name or template_id)
        if bg:
            csld.insert(0, _solid_bg(bg))
        pkg = self.prs.part.package
        part = SlideLayoutPart.load(
            pkg.next_partname("/ppt/slideLayouts/slideLayout%d.xml"),
            CT.PML_SLIDE_LAYOUT, pkg,
            etree.tostring(root, xml_declaration=True, encoding="UTF-8",
                           standalone=True))
        # Связь в обе стороны: мастер -> раскладка (чтобы её было видно в
        # галерее) и раскладка -> мастер (без неё PowerPoint считает файл
        # повреждённым, а python-pptx не может дойти до темы).
        part.relate_to(self.master.part, RT.SLIDE_MASTER)
        rid = self.master.part.relate_to(part, RT.SLIDE_LAYOUT)
        lst = self.master.element.get_or_add_sldLayoutIdLst()
        entry = lst._add_sldLayoutId()
        # ST_SlideLayoutId начинается с 2147483648; болванка нумерует с 649.
        entry.set("id", str(2147483649 + len(lst.sldLayoutId_lst) - 1))
        entry.set(qn("r:id"), rid)
        layout = part.slide_layout
        self.layouts[template_id] = layout
        return layout
