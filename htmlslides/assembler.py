"""Сборка self-contained HTML-деки из DeckPlan: рендер слайдов + инлайн движка."""
from __future__ import annotations

import re
from html import escape as html_escape
from importlib import resources

from jinja2 import Environment, FunctionLoader, select_autoescape

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


def _glue_units(value):
    """Jinja-finalize: приклеить число к его единице неразрывным пробелом (U+00A0),
    чтобы «180 млн», «18 мес.», «12 ВМ» не рвались переносом. Трогаем ТОЛЬКО чистые
    строки (type is str) — int/float геометрии графиков, Markup и Undefined проходят
    как есть. Меняется лишь ПРОБЕЛ на неразрывный: видимый текст идентичен (важно —
    авто-режим; дословный «точный перенос» рендерится f-строкой в обход Jinja)."""
    if type(value) is not str:
        return value
    return _GLUE_RE.sub("\\1" + _NBSP + "\\2", value)


def _jinja_env() -> Environment:
    env = Environment(
        loader=FunctionLoader(_load_slide_template),
        autoescape=select_autoescape(["html"]),
        finalize=_glue_units,
    )
    env.filters["num"] = _num
    return env


def _render_slide(env: Environment, library: TemplateLibrary, slide: SlidePlan) -> str:
    if slide.freeform:
        fragment = str(slide.content.get("html", "")).strip()
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

    return env.get_template(template.file).render(content=slide.content)


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
        slides=slides_html,
    )
