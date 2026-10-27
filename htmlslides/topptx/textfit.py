"""Измерение текста без браузера: ширина строки и число переносов.

Зачем это нужно. Таблица геометрии (geometry.json) снята на синтетической рыбе,
и всё, что в раскладке зависит ТОЛЬКО от дерева секции, она описывает точно.
Остаётся одна зависимость, которую деревом не выразить: число строк. В
.g22-cell (колоночный flex) заголовок .g22-name стоит над .g22-text, и если
заголовок переносится на вторую строку, текст уезжает вниз ровно на одну
строку — 30px * 1.15 = 34.5px. Калибровка это измеряет и печатает; чинить это
можно только одним способом — посчитать, сколько строк займёт НАСТОЯЩИЙ текст.

Почему HarfBuzz, а не свои метрики. Ширину строки определяют не только
advance-ширины глифов: в SB Sans есть GPOS-кернинг до 162/1000 em, то есть
4.9px на кегле 30 — этого хватает, чтобы перенос случился на слово раньше или
позже. Писать свой разбор GPOS значит писать второй шейпер и расходиться с
браузером в деталях. Chrome раскладывает текст именно HarfBuzz'ом, поэтому мы
берём тот же HarfBuzz и получаем не приближение, а тот же ответ: на пробе из
9 строк (кегли 18-320, letter-spacing от -6 до +0.5) расхождение с
getBoundingClientRect не превысило 0.013px — это округление Chrome до 1/64
пикселя, а не ошибка модели.

Почему woff2 распаковывается здесь. HarfBuzz не умеет woff2 (сборка без
woff2-декодера), и на сжатом файле он молча отдаёт .notdef на каждый символ —
ровно 0.5em, то есть правдоподобную, но неверную ширину. Такую ошибку легко не
заметить, поэтому распаковка fontTools'ом сделана единственным путём к шрифту:
другого способа получить Face в этом модуле нет.
"""
from __future__ import annotations

import re
from functools import lru_cache

# Пробелы, по которым CSS разрешает перенос. Мягкий перенос и неразрывный
# пробел сюда намеренно не входят: U+00A0 перенос запрещает, и браузер по нему
# строку не рвёт.
_BREAK_SPACE = " \t\n\r\f\v            　"


@lru_cache(maxsize=8)
def _face(filename: str):
    """hb.Face по имени файла из htmlslides/assets/fonts.

    woff2 -> sfnt в памяти: на диск ничего не кладём, иначе у рантайма
    появилась бы запись в пакет (на сервере он может быть только для чтения).
    """
    import io
    from importlib import resources

    import uharfbuzz as hb
    from fontTools.ttLib import TTFont

    raw = (resources.files("htmlslides") / "assets" / "fonts"
           / filename).read_bytes()
    buf = io.BytesIO()
    tt = TTFont(io.BytesIO(raw))
    tt.flavor = None                       # снять woff2-обёртку
    tt.save(buf)
    return hb.Face(hb.Blob(buf.getvalue()))


@lru_cache(maxsize=1)
def _faces_by_family() -> dict[tuple[str, int], str]:
    """{(семейство в нижнем регистре, вес): файл} из htmlslides.fonts._FACES.

    Источник правды один — тот же список, из которого собирается @font-face в
    деке. Продублировать его здесь значило бы завести второй: подменят файл в
    fonts.py, а измеритель продолжит мерить старый.
    """
    from ..fonts import _FACES

    out: dict[tuple[str, int], str] = {}
    for family, weights, filename in _FACES:
        for w in weights.split():
            out[(family.lower(), int(w))] = filename
    return out


def _resolve(family_css: str, weight: str | int) -> str | None:
    """CSS font-family + font-weight -> файл шрифта. None, если нет своего.

    Вес подбираем ближайшим, а не точным: deck.css просит 500 там, где в
    источнике есть только 400 и 600 (см. комментарий в fonts.py), и ошибиться
    на фейс значит ошибиться в ширине процентов на пять.
    """
    table = _faces_by_family()
    try:
        want = int(str(weight).strip())
    except ValueError:                     # normal / bold
        want = 700 if str(weight).strip() == "bold" else 400
    for name in family_css.split(","):
        fam = name.strip().strip('"\'').lower()
        avail = sorted({w for f, w in table if f == fam})
        if avail:
            return table[(fam, min(avail, key=lambda w: abs(w - want)))]
    return None


def _advance(text: str, filename: str) -> float:
    """Ширина текста в единицах em (upem нормирован к 1.0)."""
    import uharfbuzz as hb

    face = _face(filename)
    font = hb.Font(face)
    font.scale = (face.upem, face.upem)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(font, buf)
    return sum(p.x_advance for p in buf.glyph_positions) / face.upem


class Style:
    """Разобранный computed style — ровно то, что нужно для измерения.

    Собирается из того же словаря, что лежит в палитре geometry.json, поэтому
    калибровка и конвертер мерят ОДНИМИ параметрами.
    """

    __slots__ = ("file", "size", "spacing", "transform", "line_height")

    def __init__(self, style: dict):
        self.file = _resolve(style.get("fontFamily") or "",
                             style.get("fontWeight") or "400")
        self.size = _num(style.get("fontSize"))
        self.spacing = _num(style.get("letterSpacing"))     # normal -> 0
        self.transform = (style.get("textTransform") or "none").strip()
        self.line_height = self._lh(style.get("lineHeight"))

    def _lh(self, css: str | None) -> float:
        """Высота строки в px. 'normal' -> из метрик шрифта, как в браузере."""
        if css and css.strip() != "normal":
            return _num(css)
        if not self.file:
            return self.size * 1.2
        face = _face(self.file)
        # Chrome: ascent+descent+lineGap из hhea, нормированные на upem.
        import uharfbuzz as hb

        font = hb.Font(face)
        ext = font.get_font_extents("ltr")
        return (ext.ascender - ext.descender + ext.line_gap) / face.upem * self.size

    @property
    def known(self) -> bool:
        """Шрифт наш и кегль осмыслен — измерению можно верить."""
        return bool(self.file) and self.size > 0


def _num(css: str | None) -> float:
    """'30px' / '-.3px' -> float. 'normal', '', None -> 0."""
    if not css:
        return 0.0
    m = re.match(r"^\s*(-?[\d.]+)", css)
    return float(m.group(1)) if m else 0.0


def width(text: str, st: Style) -> float:
    """Ширина текста в px с кернингом и letter-spacing.

    letter-spacing Chrome добавляет ПОСЛЕ каждого символа, включая последний —
    не (n-1) зазоров, а n. Проверено на пробе: при ls=-6 и кегле 320 модель
    совпала с getBoundingClientRect до 0.013px.
    """
    if not st.known or not text:
        return 0.0
    t = _transformed(text, st.transform)
    return _advance(t, st.file) * st.size + st.spacing * len(t)


def _transformed(text: str, transform: str) -> str:
    if transform == "uppercase":
        return text.upper()
    if transform == "lowercase":
        return text.lower()
    if transform == "capitalize":
        return " ".join(w[:1].upper() + w[1:] for w in text.split(" "))
    return text


def _tokens(text: str) -> list[str]:
    """Разбить на единицы переноса: слово + прилипший к нему хвост пробелов.

    Пробел остаётся ПРИ слове слева, потому что в CSS он и переносится вместе с
    ним: висящий в конце строки пробел в ширину строки не входит.
    """
    return re.findall(rf"[^{re.escape(_BREAK_SPACE)}]+[{re.escape(_BREAK_SPACE)}]*",
                      text)


def wrap(text: str, avail: float, st: Style) -> list[str]:
    """Разложить текст по строкам шириной не больше avail. -> строки без хвостов.

    Жадный перенос по пробелам — то же, что делает браузер при
    word-break:normal без переносов по слогам (в деке переносов нет: дефисную
    расстановку CSS не включает, а hyphens по умолчанию none).

    Слово, которое само длиннее полосы, НЕ рубим: при overflow-wrap:normal
    браузер его тоже не рубит, а выпускает за край. Рубить значило бы получить
    лишнюю строку там, где её нет.
    """
    if not st.known:
        return [text] if text else []
    t = " ".join(_transformed(text, st.transform).split())
    if not t:
        return []
    if avail <= 0:
        return [t]
    lines: list[str] = []
    cur = ""
    for tok in _tokens(t):
        word = tok.rstrip(_BREAK_SPACE)
        probe = (cur + " " + word) if cur else word
        if cur and _raw_width(probe, st) > avail + 0.01:
            lines.append(cur)
            cur = word
        else:
            cur = probe
    if cur:
        lines.append(cur)
    return lines


def _raw_width(text: str, st: Style) -> float:
    """width() без повторного transform: внутри wrap он уже применён."""
    return _advance(text, st.file) * st.size + st.spacing * len(text)


def line_count(text: str, avail: float, st: Style) -> int:
    """Сколько строк займёт текст в полосе avail. Минимум 1 для непустого."""
    if not text.strip():
        return 0
    return max(len(wrap(text, avail, st)), 1)
