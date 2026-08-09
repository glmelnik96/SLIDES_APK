"""Сборка self-contained HTML-деки из DeckPlan: рендер слайдов + инлайн движка."""
from __future__ import annotations

import base64
import re
from functools import lru_cache
from html import escape as html_escape
from importlib import resources

from jinja2 import Environment, FunctionLoader, select_autoescape
from markupsafe import Markup

from .fonts import fonts_css
from .library import SlotValidationError, TemplateLibrary
from .models import DeckPlan, SlidePlan


class AssembleError(Exception):
    """Контент не прошёл слот-контракт или шаблон не найден."""


_PAGE = """<!DOCTYPE html>
<html lang="ru" data-theme="{theme}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
{fonts_css}
{deck_css}
{motion_css}
</style>
</head>
<body>
<div class="deck-stage">
{slides}
</div>
<div class="deck-progress"></div>
<script>
{deck_js}
</script>
<script>
{diagram_js}
</script>
</body>
</html>
"""


def _read_pkg(relpath: str) -> str:
    node = resources.files("htmlslides")
    for part in relpath.split("/"):
        node = node / part
    return node.read_text("utf-8")


def _load_slide_template(name: str) -> str:
    return _read_pkg(f"templates/slides/{name}")


_ASSET_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml", ".webp": "image/webp",
}


@lru_cache(maxsize=None)
def _asset_data_uri(name: str) -> str:
    """Inline a packaged asset (htmlslides/assets/<name>) as a data: URI so the
    self-contained deck stays offline-safe. Used for default template artwork."""
    node = resources.files("htmlslides") / "assets"
    for part in name.split("/"):
        node = node / part
    data = node.read_bytes()
    ext = name[name.rfind("."):].lower() if "." in name else ""
    mime = _ASSET_MIME.get(ext, "application/octet-stream")
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _num(value) -> float:
    """Безопасно привести строку слота к float для геометрии графиков.

    Данные могут прийти как "85%", "1 200", "99,982", "1.234.567" или "—".
    Берём первое число (пробелы/неразрывные пробелы — разделители тысяч,
    запятая — десятичный разделитель). Если точек несколько (тысячные
    разделители) — последняя считается десятичной. На мусоре возвращаем 0.0,
    чтобы assemble() не падал на нечисловых данных диаграммы.
    """
    m = re.search(r"-?\d[\d \u00a0\u202f.,]*", str(value))   # первое число
    if not m:
        return 0.0
    s = m.group(0).replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    had_comma = "," in s
    s = s.replace(",", ".")
    # несколько точек = тысячные разделители (1.234.567 -> 1234567); но если была
    # запятая (десятичный разделитель), последнюю точку оставляем десятичной.
    if s.count(".") > 1:
        if had_comma:
            head, _, tail = s.rpartition(".")
            s = head.replace(".", "") + "." + tail
        else:
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


# Короткие единицы измерения, которые нельзя отрывать от числа переносом строки.
# Только однозначные многобуквенные токены (+ «ч», «%»): единичные «м/г/т/с» не
# берём — рискуют ложно склеиться. Судья ловил «18 | мес.» и «12 | ВМ», разбитые
# переносом, — читается как ошибка вёрстки.
_GLUE_UNITS = (r"мес|ч|сут|сек|мин|мс|млрд|млн|тыс|руб|₽|чел|шт|раз|км|кг|"
               r"ГБ|ТБ|МБ|КБ|ВМ|%|TB|GB|MB|KB|ms")
_GLUE_RE = re.compile(r"(\d)[ ]+(" + _GLUE_UNITS + r")(?!\w)")


_NBSP = chr(0x00A0)  # неразрывный пробел U+00A0

# Короткие предлоги/союзы (1–2 буквы): по правилу рус. типографики их нельзя
# оставлять «висеть» в конце строки — приклеиваем к следующему слову неразрывным
# пробелом (как «в облаке» на титуле). Совпадаем только с ОТДЕЛЬНЫМ словом: перед
# ним начало строки / пробел / открывающая кавычка-скобка, после — пробел и текст.
# Нулевая ширина lookbehind позволяет склеивать подряд идущие («и в облаке»); NBSP
# ( ) тоже \s, поэтому вторая итерация видит границу слова.
_SHORT_WORDS = (r"а|и|о|у|в|к|с|я|на|по|за|из|от|до|со|во|об|не|ни|но|да|же|ли|бы|то")
_PREP_RE = re.compile(r"(?:(?<=[\s(\[«\"„])|^)(" + _SHORT_WORDS + r")[ ]+",
                      re.IGNORECASE)


def _glue_nbsp(value):
    """Jinja-finalize: расставить неразрывные пробелы по правилам рус. типографики —
    (1) число+единица («180 млн», «18 мес.», «12 ВМ») и (2) короткий предлог/союз +
    слово («в облаке») — чтобы вёрстка не рвала их переносом строки. Трогаем ТОЛЬКО
    чистые строки (type is str): int/float геометрии графиков, Markup и Undefined
    проходят как есть; inline-ассеты (data:-URI) пропускаем — там нет прозы, да и не
    гоняем регэксп по мегабайтам base64. Меняется лишь ПРОБЕЛ на неразрывный —
    видимый текст идентичен (важно — авто-режим; дословный «точный перенос»
    рендерится f-строкой в обход Jinja и остаётся нетронутым)."""
    if type(value) is not str or value[:5] == "data:":
        return value
    value = _GLUE_RE.sub("\\1" + _NBSP + "\\2", value)
    value = _PREP_RE.sub("\\1" + _NBSP, value)
    return value


def _asdata(value) -> Markup:
    """Машиночитаемый payload в data-атрибуте (JSON диаграммы): экранируем сами и
    возвращаем Markup — так значение минует finalize-типографику (_glue_nbsp берёт
    только `type is str`) и не экранируется повторно autoescape'ом.

    Типографика в JSON — это порча данных, а не косметика. «Задание на
    комплектацию» приезжало в движок как «Задание на\u00a0комплектацию»: Chromium
    такой токен (15 символов) не переносит, а diagram.js мерил ширину по /\\s+/,
    где \\s ловит и NBSP, — видел два коротких слова, брал крупный кегль, обрезку
    не применял, и подпись молча срезалась overflow:hidden. Вдобавок редактор
    читает data-diagram обратно и сохраняет NBSP в план."""
    return Markup(html_escape(str(value), quote=True))


def _text_w(value, size: float = 30.0) -> float:
    """Оценка ширины строки в px при данном кегле — для SVG-шаблонов, где текст
    надо уместить в viewBox, а <text> не умеет мериться на сервере.

    SVG режет всё, что вышло за viewBox, МОЛЧА: подпись «1 200 млрд» у самого
    длинного бара превращалась в «1 200 мл», и это читается как испорченные
    данные, а не как обрезка. Поэтому оценка сознательно завышена — лучше
    зря отдать бару 30px, чем срезать подпись.

    Замер в Chromium (шрифт деки, font-size 30, Medium), px на символ:
    цифры 17.2, строчная кириллица 17.5, заглавные 22–31 (Ш/Щ/Ю/Ж/М — худшие),
    пробел ~8. Берём 31 на заглавную, 19 на остальное, 9 на пробел — верхняя
    граница по каждому классу. Чистая функция → вёрстка воспроизводима.
    """
    s = str(value or "")
    w = 0.0
    for ch in s:
        if ch.isspace():
            w += 9.0
        elif ch.isupper():
            w += 31.0
        else:
            w += 19.0
    return w * size / 30.0


def _jinja_env() -> Environment:
    env = Environment(
        loader=FunctionLoader(_load_slide_template),
        autoescape=select_autoescape(["html"]),
        finalize=_glue_nbsp,
    )
    env.filters["num"] = _num
    env.filters["textw"] = _text_w
    env.filters["asdata"] = _asdata
    env.globals["asset"] = _asset_data_uri
    return env


def _render_slide(env: Environment, library: TemplateLibrary, slide: SlidePlan) -> str:
    if slide.freeform:
        fragment = str(slide.content.get("html", "")).strip()
        # Полностью собранный слайд-<section> (снимок из inline/чат-правки в
        # редакторе) уже несёт свой chrome и модификаторы класса (напр.
        # statement--accent — это и есть «зелёный» statement). Разворачивать его
        # нельзя: теряется класс секции и удваивается chrome — после перезагрузки
        # два слайда с разными мастерами схлопывались в один. Отдаём как есть.
        if (not slide.content.get("exact")
                and re.match(r'(?is)^<section\b[^>]*\bclass="[^"]*\bslide\b', fragment)
                and "chrome-logo" in fragment):
            return fragment
        if fragment.startswith("<section"):
            match = re.match(r"^<section[^>]*>(.*)</section>\s*$", fragment, re.DOTALL)
            inner = match.group(1) if match else fragment
        else:
            inner = fragment
        # chrome инъектит ассемблер (logo/note/num), LLM отвечает только за контент;
        # малый логотип — freeform это контентный слайд.
        # Заголовок-шапку (.content-head) выносим на уровень слайда (позиция top:72
        # и размер t-head-42 — как во всех шаблонах), остальной контент — в
        # .freeform-zone (safe-зона), чтобы вёрстка не уезжала за границы.
        head = ""
        m = re.match(r'^\s*(<div class="content-head">.*?</div>)(.*)$',
                     inner, re.DOTALL)
        if m:
            head, inner = m.group(1), m.group(2)
        chrome = env.get_template("_chrome.html").render()
        if slide.content.get("exact"):
            # exact-слайд: контент в .exact-fit (JS ужимает масштаб под .exact-zone),
            # отдельный маркер data-template="exact" — для QA/отладки.
            return ('<section class="slide slide--chrome-sm" data-template="exact">'
                    f'{chrome}{head}<div class="exact-zone">'
                    f'<div class="exact-fit">{inner}</div></div></section>')
        return ('<section class="slide slide--chrome-sm" data-template="freeform">'
                f'{chrome}{head}<div class="freeform-zone">{inner}</div></section>')

    try:
        template = library.get(slide.template_id)
    except SlotValidationError as exc:
        raise AssembleError(f"slide {slide.index}: {exc}") from exc

    errors = library.validate_content(slide.template_id, slide.content)
    if errors:
        details = "; ".join(f"{e.code}:{e.slot}" for e in errors)
        raise AssembleError(f"slide {slide.index}: {details}")

    # index — уникальный порядковый номер слайда в деке: шаблоны используют его,
    # чтобы делать SVG-id инстанс-уникальными (иначе два одинаковых мастера на
    # странице дают дубли id, и clip/gradient «схлопываются» — см. cover-image).
    return env.get_template(template.file).render(
        content=slide.content, index=slide.index)


def assemble(plan: DeckPlan, *, theme: str = "dark") -> str:
    """Собрать дека-страницу. Бросает AssembleError при нарушении контрактов."""
    if theme not in ("dark", "light"):
        raise AssembleError(f"unknown theme: {theme!r} (dark|light)")
    env = _jinja_env()
    library = TemplateLibrary.load()
    ordered = sorted(plan.slides, key=lambda s: s.index)
    slides_html = "\n".join(_render_slide(env, library, s) for s in ordered)
    return _PAGE.format(
        theme=theme,
        title=html_escape(plan.title or "Презентация"),
        fonts_css=fonts_css(),
        deck_css=_read_pkg("engine/deck.css"),
        motion_css=_read_pkg("engine/motion.css"),
        deck_js=_read_pkg("engine/deck.js"),
        diagram_js=_read_pkg("engine/diagram.js"),
        slides=slides_html,
    )
