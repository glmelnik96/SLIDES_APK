"""deck.html -> PPTX с нативными редактируемыми объектами. Без браузера и без ИИ.

Геометрия берётся из geometry.json, снятого офлайн (calibrate.py), а текст и
цифры — из самой деки. На известных деревьях браузер в рантайме не
поднимается: экспорт — это разбор lxml плюс сборка пакета, то есть десятые доли
секунды и десятки мегабайт вместо секунд и сотен.

Единственное исключение — дерево, которого в таблице нет: тогда поднимается
один браузер на всю деку и доснимает именно эти слайды (_Builder.learn,
~1 с плюс 8 мс на слайд). Без этого такой слайд молча исчезал из файла, и
случай не гипотетический: на сохранённых деках прогонов терялось до 40%
слайдов, потому что в отпечаток добавился data-slot, а деки остались старые.

Единицы. Слайд 1920x1080 px -> 13 1/3 x 7 1/2 дюйма, значит
  1 px = 12192000 / 1920 = 6350 EMU  (целое, без накопления ошибки округления)
  1 px = 960 pt / 1920 = 0.5 pt      (тоже точно)
Оба коэффициента целые/точные не случайно — именно поэтому за опорный размер
взяты 13.333x7.5 дюйма, а не «примерно 16:9».

Шрифт НЕ встраивается: лицензия ParaType этого не разрешает, а техническая
возможность разрешением не является. В файл идёт имя семейства из CSS плюс
panose самого SB Sans — на машине с установленным шрифтом вид совпадёт точно, а
без него подбор достанется PowerPoint. Почему именно panose шрифта, а не
запасного Verdana из CSS, и чем это измеряется — в pptxshell.

Тема, мастер и раскладки — там же, в pptxshell: сами слайды нативны давно, а
болванка под ними оставалась офисной.
"""
from __future__ import annotations

import base64
import io
import json
import re
from functools import lru_cache
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.util import Emu, Pt

from .domkey import sources_digest, variant_key, walk_paths
from . import repair
from .pptxshell import Shell, set_alpha
from .reflow import apply as reflow_apply
from .svgshapes import (face_for, fit_image, parse_clip_polygon, place_svg,
                        set_crop, set_custgeom, style_value)

EMU_PX = 6350                     # EMU в одном слайдовом пикселе (точно)
PT_PX = 0.5                       # pt в одном слайдовом пикселе (точно)
SLIDE_W, SLIDE_H = 1920 * EMU_PX, 1080 * EMU_PX

DATA = Path(__file__).parent / "data" / "geometry.json"

_ALIGN = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER,
          "right": PP_ALIGN.RIGHT, "justify": PP_ALIGN.JUSTIFY,
          "start": PP_ALIGN.LEFT, "end": PP_ALIGN.RIGHT}

_RGB_RE = re.compile(r"rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)"
                     r"(?:[,/\s]+([\d.]+))?\s*\)")

# Полосатый фон в том виде, в каком его отдаёт getComputedStyle: четыре стопа,
# цвет -> цвет -> прозрачный -> прозрачный. Второй стоп — ширина штриха,
# четвёртый — шаг. Узко по форме намеренно: не распознали — значит не рисуем,
# и это видно в stats["dropped"], а не превращается в кривую полосу.
_STRIPE_RE = re.compile(
    r"repeating-linear-gradient\(\s*([\d.]+deg)\s*,\s*"
    r"(rgba?\([^)]*\))\s+[\d.]+px\s*,\s*"
    r"rgba?\([^)]*\)\s+([\d.]+)px\s*,\s*"
    r"rgba?\([^)]*\)\s+[\d.]+px\s*,\s*"
    r"rgba?\([^)]*\)\s+([\d.]+)px\s*\)")

# Точечный паттерн (.pattern-dots) в том же виде из getComputedStyle: круг
# цвета радиусом в первом стопе и сразу прозрачный стоп. Шаг сетки в строке
# не лежит — он приходит отдельным свойством backgroundSize.
_DOT_RE = re.compile(
    r"radial-gradient\(\s*circle\s*,\s*(rgba?\([^)]*\))\s+([\d.]+)px\s*,\s*"
    r"rgba?\([^)]*\)\s+[\d.]+px\s*\)")

# Сдвиг сетки точек, deck.css:552 (`background-position:5px 5px`). Значение
# живёт здесь, а не в замере, потому что backgroundPosition в снимаемый набор
# свойств не входит (domkey.JS_COLLECT). Что оно не разошлось с CSS, проверяет
# тест: иначе вся сетка молча уехала бы на несколько пикселей.
_DOT_ORIGIN = 5.0

# Предел по числу точек на одну область. Самая крупная сетка деки — 4368 точек
# (.contacts-dots светлой темы, 1802x960 с шагом 20), и это норма; предел стоит
# против шага в пару пикселей, который дал бы полмиллиона точек в одном контуре
# и файл, который PowerPoint не откроет. Отказ не молчит — его видно в
# stats["dropped"]. Прежний предел 4000 был снят с тёмной темы (там максимум
# 2700) и молча резал контакты светлой — ровно та потеря, против которой он
# ставился.
_DOT_LIMIT = 8000

# Имя темы в PowerPoint видно пользователю — пишем по-русски, как и всё
# остальное в интерфейсе генератора. Неизвестная тема останется своим id.
_THEME_RU = {"dark": "тёмная", "light": "светлая"}


class ConvertError(Exception):
    """Деку не удалось разобрать или в таблице нет нужного варианта."""


@lru_cache(maxsize=1)
def _table() -> dict:
    if not DATA.exists():
        raise ConvertError(
            f"нет {DATA}: сначала снимите геометрию "
            "`python -m htmlslides.topptx.calibrate`")
    return json.loads(DATA.read_text(encoding="utf-8"))


def table_state() -> tuple[bool, str]:
    """Сошлась ли таблица с нынешними шаблонами. -> (сошлась, что сказать людям).

    Отвечает на единственный вопрос: промах по ключу — это новая раскладка
    (штатно, донор справится) или таблица отстала от шаблонов (не штатно, донор
    даст до 790 px молча)? Отличить их по самому промаху нельзя, по отпечатку
    источников — можно (см. domkey.sources_digest).

    Сошёлся отпечаток — «сошлась» и есть ответ на ВСЕ промахи этой деки: перебор
    по определению весь в таблице, значит промахнуться могла только смесь,
    которой в переборе не было. Не сошёлся — доверия нет ни к одному промаху.

    Таблица версии 3 отпечатка не несла вовсе. Для неё ответ «не сошлась»: не
    потому что она плоха, а потому что проверить нечем, а молчаливое «наверное,
    всё хорошо» — ровно та ошибка, против которой эта функция и написана.
    """
    t = _table()
    want = sources_digest()
    got = t.get("sources")
    if got == want:
        return True, f"таблица сошлась с шаблонами ({want})"
    rebuild = "пересоберите таблицу: python -m htmlslides.topptx.calibrate"
    if got is None:
        return False, (f"таблица версии {t.get('version')} снята без отпечатка "
                       f"шаблонов — сверить не с чем; {rebuild}")
    return False, (f"таблица отстала от шаблонов: снята на {got}, "
                   f"сейчас {want}; {rebuild}")


def _color(css: str | None) -> tuple[RGBColor, float] | None:
    """'rgb(34,34,34)' / 'rgba(0,0,0,0)' -> (цвет, альфа). None, если прозрачно."""
    if not css:
        return None
    m = _RGB_RE.match(css.strip())
    if not m:
        return None
    r, g, b = (int(round(float(m.group(i)))) for i in (1, 2, 3))
    alpha = float(m.group(4)) if m.group(4) is not None else 1.0
    if alpha <= 0.01:
        return None
    return RGBColor(r, g, b), alpha


def _px(css: str | None) -> float:
    """'104px' -> 104.0. Нечисловое ('normal', 'none') -> 0."""
    if not css:
        return 0.0
    m = re.match(r"^(-?[\d.]+)px$", css.strip())
    return float(m.group(1)) if m else 0.0


def own_text(el) -> str:
    """Собственный текст элемента: его текстовые узлы, без текста потомков.

    Та же семантика, что у браузерной половины (JS_COLLECT считает nodeType===3
    только у самого элемента). Благодаря ей контейнер не присваивает себе текст
    всех вложенных абзацев, и каждая надпись становится ровно одной рамкой.
    """
    parts = [el.text or ""]
    parts += [(c.tail or "") for c in el]
    return re.sub(r"\s+", " ", "".join(parts)).strip()


def _family(css_family: str) -> str:
    """'"SB Sans Display", Verdana, sans-serif' -> 'SB Sans Display'."""
    first = (css_family or "").split(",")[0].strip().strip('"\'')
    return first or "Verdana"


def _opacity(st: dict) -> float:
    """Собственная непрозрачность элемента из CSS. Нечисловое -> 1.0.

    Берём СВОЮ, а не унаследованную: computed opacity в CSS не наследуется, и
    браузер отдаёт у потомка 1, даже когда родитель полупрозрачен. Нам это и
    нужно — в деке opacity стоит на самих элементах, и каждый из них в PPTX
    своя фигура: сноска .chrome-note (0.5 на 1649 деревьях из 1651, на двух
    0.65) и точки .kpi-dots (0.5).
    """
    try:
        return min(1.0, max(0.0, float(st.get("opacity", 1))))
    except (TypeError, ValueError):
        return 1.0


def _wraps(it: dict, st: dict, text: str) -> bool:
    """Разрешать ли PowerPoint переносить этот текст.

    Разрешаем ровно там, где перенёс и браузер, — то есть где строк больше
    одной. Иначе word_wrap выключен, и текст выходит за край рамки, как
    выходит он и в деке.

    Зачем. Браузер подгоняет ширину таких рамок ПОД ТЕКСТ (inline-block,
    max-content, flex-элемент по содержимому), и мы копируем эту ширину в
    PPTX как есть. Запас выходит нулевой: из 678 текстовых рамок эталонной
    деки у 136 — то есть у каждой пятой — ширина рамки совпадает с шириной
    текста с точностью до 0.01 px. Любая мелочь на стороне PowerPoint
    (округление кегля до сотой пункта, своя трактовка трекинга) делает строку
    на волос шире рамки, и дальше поведение движков расходится принципиально:

      * Chrome при overflow-wrap:normal слово НЕ рвёт — выпускает за край;
      * PowerPoint рвёт его посреди слова.

    Получалось «СЕТЬ» -> «СЕТ» + «Ь» на обложке и «Аудит» -> «Ауди» + «т» в
    service-table, причём вторая строка налезала на то, что стоит ниже. Это и
    есть то самое «почти на каждом слайде что-то едет»: ломались не единицы, а
    пятая часть всех надписей.

    Перенос считает HarfBuzz — тот же шейпер, которым раскладывает текст сам
    Chrome, с тем же GPOS-кернингом (textfit). Полосу берём по content-box:
    рамка и padding тексту не принадлежат.
    """
    from .reflow import _inset_h
    from .textfit import Style, line_count

    style = Style(st)
    fs = it.get("fs")
    if fs and style.size > 0:
        style.line_height *= fs / style.size
        style.size = fs
    if not style.known:
        return True                       # чужой шрифт: мерить нечем, не мешаем
    return line_count(text, it["w"] - _inset_h(st), style) > 1


def _rename(shape, name: str):
    """Переименовать фигуру, если она вообще создалась (у плашки без заливки нет).

    Имя — атрибут cNvPr/@name; PowerPoint уникальности не требует, и это кстати:
    у плашки и её надписи один и тот же элемент-источник.
    """
    if shape is not None:
        shape.name = name
    return shape


def _sel(el) -> str:
    """Имя фигуры: 'div.tcc-card[cards]' — тег, класс и слот, как в разметке.

    Это не косметика, а часть редактируемости. На слайде деки до двух сотен
    фигур, и в «Области выделения» они назывались «Прямоугольник 37» и
    «TextBox 112» — найти в таком списке нужную подпись нельзя. Селектор же
    совпадает с тем, что человек видит в инспекторе браузера на той же деке.
    """
    cls = (el.get("class") or "").split()
    base = f"{el.tag}.{cls[0]}" if cls else str(el.tag)
    slot = el.get("data-slot")
    return f"{base}[{slot}]" if slot else base


def _hexrgb(css: str | None) -> str | None:
    """'rgb(34,34,34)' / '#222222' -> '222222'. Прозрачное и мусор -> None."""
    if css and css.strip().startswith("#"):
        h = css.strip().lstrip("#")
        return (h * 2 if len(h) == 3 else h).upper() if len(h) in (3, 6) else None
    got = _color(css)
    return f"{got[0]}" if got else None


def _display_names() -> dict[str, str]:
    """{id шаблона: русское имя макета} из каталога — для имён раскладок.

    Каталог тянем лениво и мягко: имя раскладки косметическое (его видно в
    «Новый слайд»), и валить из-за него экспорт деки неправильно. Нет каталога —
    раскладка называется своим id, как и была.
    """
    try:
        from htmlslides.library import TemplateLibrary

        return {s.id: s.display_name for s in TemplateLibrary.load().templates}
    except Exception:
        return {}


def _common(cssvars: dict[str, dict], name: str) -> str | None:
    """Самое частое значение переменной по шаблонам, в hex.

    Фон деки — именно самое частое, а не «цвет первого слайда»: у
    statement-green --bg зелёный, у остальных двадцати четырёх — фон темы. Так
    мастер получает фон деки, а не фон случайного макета.
    """
    from collections import Counter

    counts = Counter(h for d in cssvars.values()
                     if (h := _hexrgb(d.get(name))) is not None)
    return counts.most_common(1)[0][0] if counts else None


class _Builder:
    """Сборка одного PPTX из разобранной деки."""

    def __init__(self, theme: str):
        table = _table()
        if theme not in table["themes"]:
            raise ConvertError(f"нет калибровки темы {theme!r}")
        # Палитра — КОПИЯ списка: learn() дописывает в неё стили доснятых
        # слайдов, а _table() кэширован на весь процесс (lru_cache), так что
        # без копии замер одной деки утёк бы в кэш и достался следующей.
        # Варианты копировать не нужно — туда никто не пишет (доснятое лежит
        # отдельно, в self.measured, и по номеру слайда, а не по ключу).
        self.styles = list(table["styles"])
        self.variants = table["themes"][theme]
        self.cssvars = table.get("vars", {}).get(theme, {})
        self.prs = Presentation()
        self.shell = Shell(self.prs, SLIDE_W, SLIDE_H,
                           _THEME_RU.get(theme, theme))
        self.shell.dress(_common(self.cssvars, "--bg"),
                         _common(self.cssvars, "--fg"))
        self.names = _display_names()
        self.stats = {"slides": 0, "shapes": 0, "text": 0, "rect": 0,
                      "edge": 0, "stripe": 0, "dot": 0, "picture": 0, "svg": 0,
                      "svg_shapes": 0, "unsupported": {}, "missing": [],
                      "repaired": [], "measured": [], "reflow": 0,
                      "layouts": 0, "dropped": {}}
        # Варианты по шаблону — для донорской починки промаха (repair.py).
        # Группируем один раз: ключ хранит шаблон первым полем, так что это
        # разбор строки, а не новый индекс в таблице.
        self.by_template: dict[str, list[dict]] = {}
        for key, var in self.variants.items():
            self.by_template.setdefault(key.split("|")[0], []).append(var)
        # Геометрия, которой слайд в итоге собран: {(номер, путь): (x,y,w,h)}.
        # Нужна проверке точности (fidelity.py): она сверяет НЕ картинку, а
        # ровно эти числа с браузерным замером той же деки.
        self.placed: dict[tuple[int, str], tuple[float, float, float, float]] = {}
        # Геометрия, доснятая браузером для ЭТОЙ деки: {номер слайда: (строки,
        # стиль секции)}. По номеру, а не по ключу дерева, — почему именно так,
        # см. learn(). Пусто, пока в деке нет неизвестных таблице деревьев.
        self.measured: dict[int, tuple[list[dict], int]] = {}

    # ---------------------------------------------------------------- таблица
    def absent(self, sections) -> list[tuple[int, str]]:
        """Слайды, которых нет в таблице. -> [(номер, ключ)] в порядке деки.

        Этот список решает, поднимать ли браузер, — то есть браузер встаёт на
        ЛЮБОЙ промах по таблице, а не только там, где не справился и донор.
        Выбор неочевидный, поэтому вот числа, на которых он сделан.

        На деке из сегодняшних шаблонов донор точен: 7 слайдов из 112 (смеси
        внутри элемента списка, их в таблице не будет никогда), 150 рамок,
        худшее отклонение 0.010 px — ТОЧНЕЕ табличного пути с его 0.030 px.
        По этим числам донор и выглядел достаточным.

        А на деках прогонов, где отпечаток разъехался, тот же донор дал 159
        промахов >0.5 px из 411 рамок — 39%, с худшим 790 px: .content-head
        шириной 1570 вместо 780. И ни одной жалобы: слайд собран, файл
        открывается. Причина в repair._sig_el — донор сопоставляет элементы по
        тегу и классу, а data-slot не смотрит вовсе. Когда разъехался именно
        он, донор находит «идеального» двойника с нулём лишних элементов и
        переносит геометрию ДРУГОЙ раскладки целиком; а reflow.drop правит
        только высоты, ширину ему править нечем.

        Отличить эти два случая по одному промаху нельзя: разница не в ключе, а
        в том, соответствует ли таблица шаблонам, — а этого сейчас не проверяет
        никто (в geometry.json лежит version: 3, который convert даже не
        читает). Пока нет отпечатка шаблонов, безопасная сторона одна: мерить.
        Стоит это ~1 с на деку со смесью (и 0, когда смесей нет), а взамен
        снимает и 790 px, и молчаливость. Кому секунда дороже — есть
        measure_missing=False, и тогда остаётся донор, как было.
        """
        return [(i, k) for i, s in enumerate(sections, start=1)
                if (k := variant_key(s)) not in self.variants]

    def learn(self, html: str, sections, want: list[tuple[int, str]],
              *, timeout_ms: int = 120_000) -> int:
        """Доснять браузером слайды, которым геометрию больше взять негде.

        want — что отдала absent(): там же и условие, при котором браузер
        вообще поднимается, и почему оно именно такое.

        Зачем вообще, если таблица полна. Полна она ОТНОСИТЕЛЬНО перебора, с
        которого снята: поменяется шаблон или добавится атрибут, входящий в
        отпечаток, — и ключ у живой деки станет другим, а таблица об этом не
        узнает. Так и вышло: data-slot попал в отпечаток позже, чем были
        сохранены деки прогонов, и на них терялось до 40% слайдов (5 из 5
        -> 3). Донорская починка тут не спасает — она собирает смесь из
        измеренных кусков ОДНОГО шаблона, а когда разъехался отпечаток, не
        совпадает ни один кусок.

        Почему мерим саму деку, а не достраиваем страницу из шаблонов. Дека
        самодостаточна: ни одной внешней ссылки на css/js/картинки (проверено
        на четырёх реальных деках — 0 внешних), так что файл открывается в
        браузере как есть и мерится ровно в том окружении, в котором его
        увидит пользователь. Собранная заново страница — это CSS и скрипты из
        СЕГОДНЯШНЕГО репозитория, то есть ровно та подмена, из-за которой
        промах и случился.

        Хранится замер ПО НОМЕРУ СЛАЙДА, а не по ключу дерева, и это главное
        здесь. Ключ не включает текст (так и задумано: длина текста —
        величина непрерывная, ключами её не покрыть), поэтому одно дерево
        встречается в деке много раз с разным текстом. Первая версия складывала
        доснятое в таблицу по ключу — и девять слайдов service-table получали
        геометрию того одного, на котором ключ впервые замерили: промах до
        111.69 px по ширине .svc-desc, ровно на тех слайдах, где текст другой.
        По номеру слайда такого нет: браузер в том же проходе мерит ВСЕ секции,
        так что у каждой своя, снятая по её собственному тексту.

        Отсюда и остальное. Строки собираются с fit={} и peers=None: fit/peers
        нужны, чтобы по нескольким профилям рыбы понять, какие рамки подогнаны
        под текст, и записать, КАК их пересчитывать, — а пересчитывать нечего,
        замер уже на реальном тексте. По той же причине add_slide не применяет
        к ним поправку на перенос строк.

        Остальные слайды деки, у которых с геометрией всё хорошо, замером НЕ
        подменяются, хотя браузер в том же проходе отдаёт и их. Табличный путь
        измерен на 566 тысячах чисел и держит 0.030 px — менять его на
        неизмеренный в тех же масштабах нет причины; а разница в точности
        настолько мала, что выбор между ними не про точность вовсе.

        Стоит это одного запуска браузера: замерено 1002 мс фиксированно плюс
        ~8 мс на слайд (1/3/10/30 слайдов -> 1002/1052/1108/1235 мс), и только
        когда слайд иначе потерялся бы. Для сравнения, экспорт картинками
        снимает скриншот КАЖДОГО слайда всегда.

        -> сколько слайдов снято.
        """
        from . import calibrate

        if not want:
            return 0

        slides = calibrate.collect(html, timeout_ms=timeout_ms)
        # Селекторы в питоне и в браузере разные по форме (xpath contains
        # против section.slide), и совпадение количества — единственное, чем
        # проверяется, что zip ниже свёл ту секцию с тем замером. Разъехались
        # — лучше упасть: молча взять геометрию соседнего слайда хуже, чем
        # отказаться мерить.
        if len(slides) != len(sections):
            raise ConvertError(
                f"браузер снял {len(slides)} секций, в разметке "
                f"{len(sections)} — замер неизвестных деревьев невозможен")

        # Обратная палитра: в файле стили лежат списком, а compact() ждёт
        # словарь «подпись -> индекс» и дописывает в него новые наборы.
        rev = {json.dumps(st, sort_keys=True, ensure_ascii=False): i
               for i, st in enumerate(self.styles)}
        base = len(rev)
        for i, key in want:
            section, slide = sections[i - 1], slides[i - 1]
            tmpl = section.get("data-template") or "?"
            if slide["template"] != tmpl:
                raise ConvertError(
                    f"слайд {i}: браузер видит шаблон {slide['template']!r}, "
                    f"в разметке {tmpl!r}")
            root_sig = json.dumps(slide["root"], sort_keys=True,
                                  ensure_ascii=False)
            self.measured[i] = (calibrate.compact(slide, rev, {}, None),
                                rev.setdefault(root_sig, len(rev)))
            self.stats["measured"].append((i, key))
        # Палитра выросла — дотянуть до неё список стилей. Порядок вставки в
        # dict совпадает с индексами (они и выданы как len(rev)), поэтому
        # хвост ключей ложится в хвост списка один к одному.
        self.styles += [json.loads(s) for s in list(rev)[base:]]
        return len(self.measured)

    # ---------------------------------------------------------------- фигуры
    def _sides(self, st) -> dict[str, tuple[float, tuple]]:
        """Какие рамки у элемента есть на самом деле. -> {сторона: (ширина, цвет)}.

        Нужно потому, что в CSS рамка поштучная, а у фигуры PPTX она одна на
        весь контур. Раньше смотрели ТОЛЬКО border-top — и `border-top:1px`
        превращался в обводку по всем четырём сторонам. Это не мелочь: так
        задан разделитель строк у .svc-row (service-table), .tl-row (timeline)
        и .sr-cell (stats-row), и вместо трёх тонких линий PowerPoint рисовал
        три зелёных ПРЯМОУГОЛЬНИКА вокруг каждой строки. По разнице «чернил» со
        снимком браузера это были слайды 64 (18.1%) и 72 (16.1%) — второе и
        третье места снизу по всей деке.

        Ширина сторон в палитре есть у всех четырёх, цвет — только у top и
        left (domkey.JS_COLLECT). Поэтому вертикальным сторонам берём цвет
        левой, горизонтальным — верхней, а если своего нет — соседний. В
        шаблонах рамка всегда одноцветная (`border:1px solid var(--line)`),
        так что приближение ни на одном сегодняшнем шаблоне не срабатывает;
        лежит оно здесь, чтобы разноцветная рамка дала чуть неверный цвет, а
        не исчезла совсем.

        Нулевая ширина проверяется без оглядки на border-style: у style:none
        браузер отдаёт computed width ровно 0px, так что ширина уже всё знает.
        """
        top, left = _color(st.get("borderTopColor")), _color(st.get("borderLeftColor"))
        out = {}
        for side, col in (("Top", top or left), ("Bottom", top or left),
                          ("Left", left or top), ("Right", left or top)):
            w = _px(st.get(f"border{side}Width"))
            if w and col:
                out[side.lower()] = (w, col)
        return out

    def _edge(self, slide, it, side, w, col):
        """Одна сторона рамки отдельной линией — когда рамка не по всему контуру."""
        x, y = it["x"], it["y"]
        x2, y2 = x + it["w"], y + it["h"]
        # Линия идёт по СЕРЕДИНЕ полосы рамки: в border-box рамка лежит внутри
        # габарита, то есть верхняя занимает y..y+w, а её ось — y+w/2.
        seg = {"top": (x, y + w / 2, x2, y + w / 2),
               "bottom": (x, y2 - w / 2, x2, y2 - w / 2),
               "left": (x + w / 2, y, x + w / 2, y2),
               "right": (x2 - w / 2, y, x2 - w / 2, y2)}[side]
        from pptx.enum.shapes import MSO_CONNECTOR

        shp = slide.shapes.add_connector(
            MSO_CONNECTOR.STRAIGHT, *(Emu(round(v * EMU_PX)) for v in seg))
        shp.line.color.rgb = col[0]
        shp.line.width = Pt(w * PT_PX)
        set_alpha(shp.line.fill, col[1])
        self.stats["edge"] += 1
        return shp

    def _stripes(self, slide, it, st) -> int:
        """`repeating-linear-gradient` -> настоящие полосы. -> сколько нарисовано.

        Это «шурешки» обложки (.cover-hatch): полоса 1800x183 из вертикальных
        штрихов 2 px с шагом 20. В CSS они заданы фоновым градиентом, а
        _rect смотрит только backgroundColor — у градиентного элемента он
        прозрачный, поэтому на слайде не появлялось РОВНО НИЧЕГО: единственный
        акцент обложки пропадал целиком, и на снимке из PowerPoint низ слайда
        пустой.

        Рисуем штрихи отдельными прямоугольниками, а не картинкой: требование
        «редактируемы все объекты», и 90 штрихов на обложку — цена посильная.
        """
        m = _STRIPE_RE.search(st.get("backgroundImage") or "")
        if m is None or m.group(1) != "90deg":
            return 0
        col = _color(m.group(2))
        bar, step = float(m.group(3)), float(m.group(4))
        # Предел по числу штрихов: цена линейна, и шаг в пиксель на полосе
        # во весь слайд дал бы под две тысячи фигур на одном слайде — это
        # уже не редактируемый слайд, а неоткрываемый файл.
        if not col or bar <= 0 or step <= 0 or it["w"] / step > 400:
            return 0
        # Непрозрачность берём из палитры, а в палитре у .cover-hatch стоит 1,
        # хотя в deck.css у неё opacity:.5. Так решает сама дека: калибровка
        # снимает её в режиме покоя (?editor=1 -> body.no-motion), а там
        # motion.css:179 гасит лупы через `opacity:1 !important`. Печать деки
        # делает то же самое, снимок PNG — нет, поэтому на картинке штрихи
        # бледнее. Это расхождение деки с собой, и чинить его надо в CSS.
        alpha = col[1] * _opacity(st)
        n, x = 0, 0.0
        while x + bar <= it["w"] + 0.01:
            shp = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE, Emu(round((it["x"] + x) * EMU_PX)),
                Emu(round(it["y"] * EMU_PX)), Emu(round(bar * EMU_PX)),
                Emu(round(it["h"] * EMU_PX)))
            shp.shadow.inherit = False
            shp.fill.solid()
            shp.fill.fore_color.rgb = col[0]
            set_alpha(shp.fill, alpha)
            shp.line.fill.background()
            shp.text_frame.text = ""
            x += step
            n += 1
        self.stats["stripe"] += n
        return n

    def _dots(self, slide, it, st) -> int:
        """`radial-gradient(circle,...)` -> сетка точек ОДНОЙ фигурой. -> точек.

        Это .pattern-dots: точечная подложка kpi, statement, contacts,
        section-dots и service-table. Точек много — самая крупная область
        (.contacts-dots светлой темы, 1802x960 с шагом 20) несёт 4368 точек, а
        на всю деку их за двадцать тысяч. Отдельными фигурами, как штрихи
        обложки, столько не разложить: слайд с тысячами фигур в PowerPoint уже
        не редактируют, его ждут.

        Поэтому вся сетка — один собственный контур (a:custGeom) с квадратом в
        каждом узле. Так даже ближе к исходнику: в CSS это ТОЖЕ один элемент с
        фоновой картинкой, а не тысяча элементов. Фигура остаётся родной и
        редактируемой — двигается, удаляется и перекрашивается целиком, ровно
        как контейнер с currentColor в деке.

        Картинкой-заливкой было бы в сто раз дешевле, но это единственный
        растр в файле, который нельзя ни перекрасить, ни увеличить без каши, —
        а вектор стоит лишних пары сотен килобайт на деку.
        """
        m = _DOT_RE.search(st.get("backgroundImage") or "")
        if m is None:
            return 0
        col = _color(m.group(1))
        r = float(m.group(2))
        size = [_px(v) for v in (st.get("backgroundSize") or "").split()]
        if not col or r <= 0 or len(size) != 2 or min(size) <= 0:
            return 0

        def nodes(step: float, span: float) -> list[float]:
            # Центр первой точки: сдвиг фона плюс половина плитки — круг в
            # radial-gradient без позиции стоит в её середине. Берём только
            # узлы с центром внутри рамки: фон в CSS обрезан по border-box, и
            # у точки на самом краю видна была бы половина. В деке таких нет
            # ни одной, а разница в любом случае в полтора пикселя.
            first = _DOT_ORIGIN + step / 2
            return [first + k * step
                    for k in range(max(0, int((span - first) // step) + 1))]

        xs, ys = nodes(size[0], it["w"]), nodes(size[1], it["h"])
        if not xs or not ys or len(xs) * len(ys) > _DOT_LIMIT:
            return 0

        # Узел — КВАДРАТ со стороной в диаметр точки из CSS, центром в узле.
        # Фирменный паттерн Cloud.ru пиксельный, и в PPTX, где точки — вектор,
        # который разглядывают и редактируют вблизи, узел обязан быть
        # квадратным. В CSS та же точка нарисована radial-gradient(circle...):
        # на трёх пикселях в браузере круг от квадрата не отличить, поэтому
        # расхождение с разметкой сознательное и одностороннее.
        rad = round(r * EMU_PX)

        def square(x: float, y: float) -> str:
            cx, cy = round(x * EMU_PX), round(y * EMU_PX)
            return (f'<a:moveTo><a:pt x="{cx - rad}" y="{cy - rad}"/></a:moveTo>'
                    f'<a:lnTo><a:pt x="{cx + rad}" y="{cy - rad}"/></a:lnTo>'
                    f'<a:lnTo><a:pt x="{cx + rad}" y="{cy + rad}"/></a:lnTo>'
                    f'<a:lnTo><a:pt x="{cx - rad}" y="{cy + rad}"/></a:lnTo>'
                    "<a:close/>")

        body = "".join(square(x, y) for y in ys for x in xs)

        w, h = round(it["w"] * EMU_PX), round(it["h"] * EMU_PX)
        shp = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Emu(round(it["x"] * EMU_PX)),
            Emu(round(it["y"] * EMU_PX)), Emu(w), Emu(h))
        shp.shadow.inherit = False
        shp.fill.solid()
        shp.fill.fore_color.rgb = col[0]
        set_alpha(shp.fill, col[1] * _opacity(st))
        shp.line.fill.background()
        shp.text_frame.text = ""
        set_custgeom(shp, w, h, body, "norm")
        self.stats["dot"] += len(xs) * len(ys)
        return len(xs) * len(ys)

    def _background(self, slide, it, st) -> int:
        """Фоновая картинка элемента -> фигуры. -> сколько нарисовано.

        Одно место, где решается судьба backgroundImage, и одно место, где
        отказ попадает в отчёт. Молчаливая потеря уже однажды стоила деке 40%
        слайдов, поэтому «не нарисовали» обязано быть видно в stats["dropped"].
        """
        n = self._stripes(slide, it, st) or self._dots(slide, it, st)
        bg = st.get("backgroundImage") or ""
        if not n and bg and bg != "none":
            kind = bg.split("(")[0]
            self.stats["dropped"][kind] = self.stats["dropped"].get(kind, 0) + 1
        return n

    def _rect(self, slide, it, st):
        """Прямоугольник под заливку/рамку элемента.

        Непрозрачность элемента умножается на альфу цвета — там же, где это
        делают текст и штрихи фона: в CSS это два независимых множителя
        (`opacity` у элемента и альфа у самого цвета), а в DrawingML — одна
        `<a:alpha>` у цвета. На сегодняшних шаблонах множитель всегда 1
        (полупрозрачны только сноска и точки, а у них нет ни заливки, ни
        рамки), поэтому файл от этого не меняется; стоит оно здесь, чтобы
        полупрозрачная плашка в новом шаблоне не приехала глухой.
        """
        if self._background(slide, it, st):
            return None
        op = _opacity(st)
        fill = _color(st.get("backgroundColor"))
        sides = self._sides(st)
        # Обводка фигуры годится, только когда рамка идёт по всему контуру и
        # одинаковая; иначе стороны рисуются линиями по отдельности (см. _sides).
        whole = len(sides) == 4 and len(set(sides.values())) == 1
        if not fill and not sides:
            return None
        if not fill and not whole:
            for side, (w, col) in sides.items():
                self._edge(slide, it, side, w, (col[0], col[1] * op))
            return None
        shp = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Emu(round(it["x"] * EMU_PX)),
            Emu(round(it["y"] * EMU_PX)), Emu(round(it["w"] * EMU_PX)),
            Emu(round(it["h"] * EMU_PX)))
        shp.shadow.inherit = False
        if fill:
            shp.fill.solid()
            shp.fill.fore_color.rgb = fill[0]
            # Альфу пишет set_alpha: свойства fill.transparency у python-pptx
            # нет, присваивание молча пропадало (подробно — в pptxshell).
            set_alpha(shp.fill, fill[1] * op)
        else:
            shp.fill.background()
        if whole:
            w, col = next(iter(sides.values()))
            shp.line.color.rgb = col[0]
            shp.line.width = Pt(w * PT_PX)
            set_alpha(shp.line.fill, col[1] * op)
        else:
            shp.line.fill.background()
            for side, (w, col) in sides.items():
                self._edge(slide, it, side, w, (col[0], col[1] * op))
        shp.text_frame.text = ""
        self.stats["rect"] += 1
        return shp

    def _text(self, slide, it, st, text):
        """Текстовая рамка: кегль, цвет, выключка и интерлиньяж как в CSS.

        Кегль берём из строки (`fs`), если поправка его пересчитала, и только
        потом из палитры. В палитре он снят с РЫБЫ: у «цифр-героев» stats-row
        размер выставляет наш deck.js по длине значения, и на реальном слайде он
        другой (см. reflow.autofit_stats). Интерлиньяж масштабируется тем же
        отношением — у .sr-value он задан множителем, так что в пикселях равен
        кеглю и уехал бы вместе с ним.
        """
        box = slide.shapes.add_textbox(
            Emu(round(it["x"] * EMU_PX)), Emu(round(it["y"] * EMU_PX)),
            Emu(round(it["w"] * EMU_PX)), Emu(round(it["h"] * EMU_PX)))
        tf = box.text_frame
        # Рамка из таблицы — border-box, а текст в HTML живёт в content-box:
        # CSS padding и border переезжают во внутренние отступы рамки. У
        # обычного текста (p, h1..h6) они нулевые — то же, что прежнее
        # «обнулить дефолтные 0.1/0.05 дюйма PowerPoint». Но у плашек с
        # заливкой (например .ba-delta: padding 10px 18px) нулевые отступы
        # прижимали текст к верх-лево чёрного чипа — «текст чуть поехал».
        for side in ("left", "right", "top", "bottom"):
            cap = side.capitalize()
            ins = _px(st.get(f"padding{cap}")) + _px(st.get(f"border{cap}Width"))
            setattr(tf, f"margin_{side}", Emu(round(ins * EMU_PX)))
        tf.word_wrap = _wraps(it, st, text)
        tf.auto_size = MSO_AUTO_SIZE.NONE
        tf.vertical_anchor = MSO_ANCHOR.TOP

        base = _px(st.get("fontSize"))
        fs = it.get("fs") or base
        k = (fs / base) if (it.get("fs") and base) else 1.0

        p = tf.paragraphs[0]
        p.alignment = _ALIGN.get(st.get("textAlign", "left"), PP_ALIGN.LEFT)
        lh = _px(st.get("lineHeight")) * k
        if lh:
            p.line_spacing = Pt(lh * PT_PX)

        run = p.add_run()
        caps = st.get("textTransform") == "uppercase"
        run.text = text.upper() if caps else text
        f = run.font
        # Вес 600 — это НЕ флаг Bold (он про вес 700), а имя начертания
        # «… Semibold»; правило и причины — в svgshapes.face_for.
        f.name, f.bold = face_for(_family(st.get("fontFamily", "")),
                                  st.get("fontWeight", "400"))
        if fs:
            f.size = Pt(fs * PT_PX)
        col = _color(st.get("color"))
        if col:
            f.color.rgb = col[0]
            # opacity элемента из CSS — в альфу цвета текста. Без этого сноска
            # .chrome-note (opacity .5) шла на всех 112 слайдах в полную силу.
            set_alpha(f.fill, col[1] * _opacity(st))
        ls = _px(st.get("letterSpacing"))
        if ls:
            # PPTX хранит трекинг в сотых пункта
            run.font._rPr.set("spc", str(int(round(ls * PT_PX * 100))))
        self.stats["text"] += 1
        return box

    def _picture(self, slide, it, st, data: bytes):
        """<img> -> картинка, кадрированная так же, как её кадрирует браузер.

        object-fit в рамку не попадает: у .svc-image img стоит contain, у
        .tl-art-img — cover, а getBoundingClientRect и там и там отдаёт рамку
        целиком. Поэтому раньше картинка просто растягивалась на рамку. С
        дежурным ассетом пропорции почти совпадают и этого не видно, а стоит
        пользователю подставить своё изображение — пропорции врут сразу.

        Сама геометрия — в svgshapes.fit_image: ровно та же задача, что у
        <image preserveAspectRatio> внутри SVG, поэтому и формула одна.
        """
        box = (it["x"], it["y"], it["w"], it["h"])
        fit = None
        try:
            from PIL import Image

            with Image.open(io.BytesIO(data)) as im:
                fit = fit_image(im.size, box, st.get("objectFit") or "fill")
        except Exception:
            pass
        # fit is None — либо размер исходника не прочитался, либо он нулевой; и то
        # и другое значит «кадрирование посчитать не из чего». Ставим по рамке,
        # как было до этой правки: потерять картинку хуже, чем поставить её
        # неточно, а битый исходник всё равно не нарисуется.
        x, y, w, h = fit[0] if fit else box
        pic = slide.shapes.add_picture(
            io.BytesIO(data), Emu(round(x * EMU_PX)), Emu(round(y * EMU_PX)),
            Emu(max(round(w * EMU_PX), 1)), Emu(max(round(h * EMU_PX), 1)))
        if fit:
            set_crop(pic, fit[1])
        self.stats["picture"] += 1
        return pic

    # ----------------------------------------------------------------- слайд
    def _rows_for(self, section, key: str, index: int):
        """Строки геометрии слайда. -> (строки, стиль секции) | None.

        Копия строк, а не сами строки таблицы: один ключ встречается в деке
        много раз, и правка на месте накапливалась бы со слайда на слайд.

        Промах по ключу — не приговор. Шесть шаблонов ветвятся ВНУТРИ элемента
        списка, и перебрать это нельзя (у grid-2x2 2^15 деревьев на одну длину
        ряда), поэтому таблица такие смеси не содержит в принципе. Донорская
        починка собирает их из измеренного надмножества — см. repair.py.

        Порядок попыток: замер этой деки (если learn() его делал), таблица,
        донор. Замер первым не для скорости — он к этому моменту уже сделан, —
        а потому что он снят по тексту ИМЕННО этого слайда, тогда как
        табличную строку ещё предстоит подгонять под текст в питоне. Донор
        последним по той же причине с другого конца: он приближённый.
        Если ни одно не дало строк, возвращаем None — и convert() на этом
        падает, а не теряет слайд.
        """
        got = self.measured.get(index)
        if got is not None:
            rows, section_style = got
            return [dict(it) for it in rows], section_style
        variant = self.variants.get(key)
        if variant is not None:
            return [dict(it) for it in variant["items"]], variant["s"]
        got = repair.rows_for(
            section, self.by_template.get(section.get("data-template") or "?",
                                          []), self.styles)
        if got is None:
            self.stats["missing"].append((index, key))
            return None
        rows, donor = got
        self.stats["repaired"].append((index, key))
        # Фон берём у донора: он задан секции, а секция у донора та же.
        return rows, donor["s"]

    def add_slide(self, section, index: int):
        template_id = section.get("data-template") or "?"
        key = variant_key(section)
        got = self._rows_for(section, key, index)
        if got is None:
            return None
        rows, section_style = got

        # Раскладка — своя на каждый шаблон, и её фон тот же, что у секции:
        # «Новый слайд -> Две карточки» должен давать слайд на фоне деки, а не
        # на белом офисном.
        was = len(self.shell.layouts)
        bghex = _hexrgb(self.styles[section_style].get("backgroundColor"))
        slide = self.prs.slides.add_slide(self.shell.layout_for(
            template_id, self.names.get(template_id, ""), bghex))
        self.stats["layouts"] += len(self.shell.layouts) - was
        if bghex:
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = RGBColor.from_string(bghex)

        # Таблица снята на рыбе, и единственное, чем реальный слайд от неё
        # отличается, — число строк. Поправку считаем здесь.
        #
        # Доснятому браузером поправка не нужна и вредна: рамки уже измерены по
        # этому самому тексту, исправлять в них нечего, а приблизить питонов
        # обмер текста к браузерному можно только в минус. Замерено: без
        # поправки доснятые слайды сходятся с браузером в 0.000 px.
        text_at = {path: own_text(el) for path, el in walk_paths(section)}
        if index not in self.measured:
            self.stats["reflow"] += len(reflow_apply(rows, self.styles, text_at))

        geo = {it["p"]: it for it in rows}
        for path, el in walk_paths(section):
            it = geo.get(path)
            if it is None:
                continue                      # внутри <svg> — обрабатывается отдельно
            self.placed[(index, path)] = (it["x"], it["y"], it["w"], it["h"])
            st = self.styles[it["s"]]
            if it["w"] <= 0 or it["h"] <= 0 or st.get("display") == "none":
                continue
            nm = _sel(el)
            # Псевдоэлементы родителя: строки "путь::b"/"путь::a" синтезирует
            # калибровка (domkey.JS_COLLECT) — в DOM их нет, и обход их не
            # встретит. Это декоративные боксы (сепараторы three-col,
            # замыкающая линия timeline), им хватает _rect. Рисуем до самого
            # элемента: ::before в CSS лежит под его содержимым.
            for sfx in ("::b", "::a"):
                pit = geo.get(path + sfx)
                if pit is None:
                    continue
                self.placed[(index, path + sfx)] = (pit["x"], pit["y"],
                                                    pit["w"], pit["h"])
                pst = self.styles[pit["s"]]
                if pit["w"] <= 0 or pit["h"] <= 0 or pst.get("display") == "none":
                    continue
                _rename(self._rect(slide, pit, pst), nm + sfx)
            if el.tag == "img":
                raw = _img_bytes(el.get("src"))
                if raw:
                    _rename(self._picture(slide, it, st, raw), nm)
                continue
            if el.tag == "svg":
                self.stats["svg"] += 1
                before = len(slide.shapes)
                # clip-path родителя: браузер режет лайнарт таймлайна по
                # силуэту панелей (.tl-art), и без той же обрезки хвосты
                # стрелок легли бы диагоналями по строкам слайда. Сам
                # многоугольник — статика deck.css, рамка родителя — из
                # таблицы геометрии.
                clip = None
                parent, pbox = el.getparent(), geo.get(path.rpartition(".")[0])
                if parent is not None and pbox is not None:
                    clip = parse_clip_polygon(
                        style_value(parent, "clip-path"), pbox)
                # color из таблицы отдаём внутрь: брендовые глифы (стрелка,
                # рамки contacts/section-frame, графика quote) обведены
                # stroke="currentColor" и без него выходят невидимыми.
                n, bad = place_svg(slide, el, it, self.cssvars.get(template_id, {}),
                                   st.get("color"), clip)
                self.stats["svg_shapes"] += n
                # Фигуры из svg переименовываем ПОСЛЕ разбора: внутри svgshapes
                # их рождает дюжина разных ветвей (path, rect, circle, text,
                # image), и тащить имя через все — лишняя связность. Сколько их
                # добавилось, видно по дереву фигур.
                for i, shp in enumerate(list(slide.shapes)[before:], start=1):
                    _rename(shp, f"{nm} {i}")
                for tag, cnt in bad.items():
                    self.stats["unsupported"][tag] = (
                        self.stats["unsupported"].get(tag, 0) + cnt)
                continue
            # Имя даём всему, что родил _rect: кроме самой плашки это ещё
            # линии сторон у неподконтурной рамки (_sides) и штрихи полосатого
            # фона (_stripes). Линия — это cxnSp, штрих — обычная фигура.
            before = len(slide.shapes)
            box = self._rect(slide, it, st)
            _rename(box, nm)
            extra = [s for s in list(slide.shapes)[before:] if s is not box]
            for i, shp in enumerate(extra, start=1):
                kind = "рамка" if shp.element.tag.endswith("}cxnSp") else "штрих"
                _rename(shp, f"{nm} {kind} {i}")
            text = own_text(el)
            if text:
                _rename(self._text(slide, it, st, text), f"{nm} текст")
        self.stats["slides"] += 1
        self.stats["shapes"] += len(slide.shapes)
        return slide

    def save(self, path: Path) -> Path:
        self.prs.save(str(path))
        return path


_PPTX_IMAGE_FORMATS = frozenset({"BMP", "GIF", "JPEG", "PNG", "TIFF", "WMF"})


def _img_bytes(src: str | None) -> bytes | None:
    """Данные картинки из data: URI. Внешние ссылки деки не используют.

    Ассеты деки лежат в webp — формате, которого нет в белом списке python-pptx
    (да и у PowerPoint поддержка webp появилась только в свежих версиях).
    Поэтому всё, что не из списка заведомо совместимых форматов,
    перекодируем в PNG: без потерь и открывается везде.
    """
    if not src or not src.startswith("data:"):
        return None
    head, _, payload = src.partition(",")
    if "base64" not in head:
        return None
    try:
        raw = base64.b64decode(payload)
    except Exception:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(raw)) as im:
            if (im.format or "").upper() in _PPTX_IMAGE_FORMATS:
                return raw
            buf = io.BytesIO()
            im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB").save(
                buf, format="PNG")
            return buf.getvalue()
    except Exception:
        return raw      # не смогли опознать — пусть решает python-pptx


def convert(deck_html: str | Path, out_pptx: str | Path,
            *, theme: str | None = None, measure_missing: bool = True) -> dict:
    """Сконвертировать деку в PPTX. -> статистика (и список промахов таблицы).

    measure_missing — поднимать ли браузер ради слайда, которому геометрию не
    дала таблица (_Builder.absent -> learn). По умолчанию да: секунда на
    экспорт дешевле и потерянного слайда, и 790 px от донора. Выключается там,
    где браузера нет совсем; тогда остаётся донор, а если не справится и он —
    ошибка, а не пропажа слайда.

    Слайд не теряется ни при каком исходе. Раньше терялся: когда ключа не было
    в таблице и донор не находился, add_slide возвращала None, и слайда просто
    не оказывалось в файле — при коде возврата 0 и открывающемся PPTX. На деках
    прогонов это съедало 5 из 28, 2 из 12 и 2 из 5 слайдов, и заметить это
    можно было только пересчитав слайды руками. Теперь остаток промахов —
    ConvertError с номерами слайдов.
    """
    from lxml import html as LH

    # Путь или сама разметка. Решает содержимое строки, а НЕ os.stat: деку
    # длиной 368 KB Path.exists() не проверяет, а падает с OSError «File name
    # too long», и документированный вход «передать HTML строкой» работал
    # только на коротких обрывках. Именно этим входом и пользуется вебапп —
    # дека у него уже в памяти, на диск её писать незачем.
    #
    # src остаётся None, когда пришла разметка. Пустым Path его подменять
    # нельзя: Path("") — это ".", и .exists() у него True, так что ниже
    # learn() попыталась бы прочитать каталог как файл.
    text = deck_html if isinstance(deck_html, str) else str(deck_html)
    src: Path | None = None
    if "<" not in text[:1024] and len(text) < 1024:
        # Строка без тегов — это задумывался путь. Не нашли файл — так и
        # говорим: иначе короткая опечатка в пути уходила бы в разбор как
        # разметка и возвращалась жалобой «в деке нет <section>», то есть
        # ответом не на тот вопрос.
        src = Path(text)
        if not src.exists():
            raise ConvertError(f"нет файла деки: {text!r}")
    doc = LH.parse(str(src)) if src else LH.document_fromstring(text)
    if theme is None:
        found = doc.xpath("//html/@data-theme")
        theme = found[0] if found else "dark"

    sections = doc.xpath('//section[contains(@class,"slide")]')
    if not sections:
        raise ConvertError("в деке нет <section class=\"slide\">")

    b = _Builder(theme)
    # Сначала дешёвый проход по ключам: есть ли в деке дерево, которого таблица
    # не знает. Нет — браузер не поднимается вовсе, и экспорт остаётся тем же
    # разбором lxml, что и был. Почему решает именно промах по таблице, а не
    # «донор не справился», — см. _Builder.absent.
    gap = b.absent(sections)
    fresh, verdict = table_state()
    b.stats["table"] = verdict
    # Совет по делу, а не один на все случаи. Раньше промах всегда предлагал
    # пересобрать таблицу — и на сошедшейся таблице это ЛОЖНЫЙ совет: пересборка
    # не добавит раскладки, которой в переборе нет, и человек потратит три
    # минуты, чтобы получить ту же ошибку. Отличить эти два случая и есть вся
    # работа отпечатка источников.
    why = (f"{verdict}, значит этой раскладки нет в переборе — её нужно "
           f"добавить в refdeck или дать замерить браузером" if fresh else
           verdict)
    measured = False
    if not gap:
        pass
    elif not measure_missing:
        why = f"замер браузером отключён (measure_missing=False), {why}"
    else:
        try:
            b.learn(src.read_text(encoding="utf-8") if src else text,
                    sections, gap)
            measured = True
        except ConvertError:
            # Замер сам нашёл несходимость (разное число секций, разъехавшийся
            # порядок) — это не «браузера нет», и глотать такое нельзя.
            raise
        except Exception as exc:
            # А вот «браузера нет» глотаем, и намеренно: Playwright — зависимость
            # разработки, на сервере его может не быть вовсе. Донор ещё не
            # пробовал и, возможно, закроет всё сам; а если нет — текст ошибки
            # ниже назовёт настоящую причину, а не догадку о ней.
            b.stats["measure_error"] = f"{type(exc).__name__}: {exc}"
            why = f"замер браузером не удался ({type(exc).__name__}), {why}"

    for i, sec in enumerate(sections, start=1):
        b.add_slide(sec, i)
    if b.stats["missing"]:
        nums = ", ".join(str(i) for i, _ in b.stats["missing"])
        raise ConvertError(
            f"нет геометрии для слайдов: {nums} "
            f"({len(b.stats['missing'])} из {len(sections)}); {why}")

    # Единственное место, где отпечаток источников что-то РЕШАЕТ, а не сообщает.
    # Сюда попадают слайды, которым геометрию дал донор: ключа в таблице не было
    # и браузер не отработал. Донор при сошедшейся таблице точен (0.010 px) —
    # это штатная смесь, и молчать про неё правильно. При отставшей таблице он
    # же молча отдаёт геометрию ДРУГОЙ раскладки: 39% рамок мимо, худшая на
    # 790 px, и файл при этом открывается. Такой экспорт лучше не отдавать
    # вовсе, чем отдать без единой жалобы, — поэтому здесь ошибка.
    if gap and not measured and not fresh:
        nums = ", ".join(str(i) for i, _ in gap)
        raise ConvertError(
            f"геометрия слайдов {nums} собрана донором, а проверить её нечем: "
            f"{verdict}. Донор переносит рамки другой раскладки (замерено: до "
            f"790 px), поэтому экспорт остановлен. Либо пересоберите таблицу, "
            f"либо дайте замерить браузером (measure_missing=True)")
    b.save(Path(out_pptx))
    b.stats["theme"] = theme
    return b.stats


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="deck.html -> редактируемый PPTX")
    ap.add_argument("deck", nargs="?")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--theme", default=None)
    ap.add_argument("--no-measure", action="store_true",
                    help="не поднимать браузер для неизвестных деревьев "
                         "(тогда промах по таблице — ошибка)")
    ap.add_argument("--check-table", action="store_true",
                    help="сверить таблицу с нынешними шаблонами и выйти")
    a = ap.parse_args(argv)
    # Отдельная проверка, потому что экспорт отвечает на этот вопрос только у
    # деки со смесью: у деки без промахов таблица может отстать на что угодно,
    # и экспорт этого не заметит (ему и незачем). Здесь же вопрос задаётся
    # прямо — например в CI, где деки под рукой нет.
    if a.check_table:
        fresh, verdict = table_state()
        print(("" if fresh else "! ") + verdict)
        return 0 if fresh else 1
    if not a.deck or not a.out:
        ap.error("нужны deck и out (или --check-table)")
    try:
        st = convert(a.deck, a.out, theme=a.theme,
                     measure_missing=not a.no_measure)
    except ConvertError as exc:
        # Отдельная ветка нужна, чтобы из командной строки промах выглядел
        # одной строкой и ненулевым кодом, а не трассировкой. Файл при этом НЕ
        # записан — это и есть смысл правки: лучше ничего, чем файл без слайдов.
        print(f"! {exc}")
        return 1
    print(f"тема {st['theme']}: слайдов {st['slides']}, фигур {st['shapes']} "
          f"(текст {st['text']}, плашек {st['rect']}, картинок {st['picture']})")
    print(f"  svg: {st['svg']} блоков -> {st['svg_shapes']} фигур")
    print(f"  линий рамок {st['edge']}, штрихов фона {st['stripe']}")
    print(f"  раскладок: {st['layouts']} (по одной на шаблон)")
    print(f"  {st['table']}")
    if st["unsupported"]:
        print(f"  не перенесено из svg: {st['unsupported']}")
    if st["dropped"]:
        print(f"  не перенесено из фона: {st['dropped']} "
              f"(точечный паттерн — это тысячи фигур на слайд)")
    if st["measured"]:
        # Не ошибка, но сигнал: таблица отстала от шаблонов. Геометрия этих
        # слайдов точная (снята браузером по реальному тексту), а вот секунда
        # на браузер платится при каждом экспорте такой деки, пока таблицу не
        # пересоберут.
        print(f"  доснято браузером: {len(st['measured'])} деревьев")
        for idx, key in st["measured"][:10]:
            print(f"    слайд {idx}: {key}")
    if st["repaired"]:
        # Не ошибка, но и не норма: геометрия здесь приближённая. Печатаем, чтобы
        # было видно, какая смесь встречается в жизни часто и стоит того, чтобы
        # стать осью перебора — измеренный вариант всегда точнее починенного.
        print(f"  починено по донору: {len(st['repaired'])}")
        for idx, key in st["repaired"][:10]:
            print(f"    слайд {idx}: {key}")
    # Печатать st["missing"] здесь больше нечего: до этой строки с промахом не
    # дойти, convert() на нём падает.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
