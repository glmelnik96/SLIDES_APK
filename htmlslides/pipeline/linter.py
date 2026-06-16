"""Статический линтер собранной деки — первая линия QA, без LLM и браузера.

Запрещённые приёмы проверяются только на freeform-слайдах: шаблоны доверены
by-design (сами используют инлайн-style), их контент экранирован autoescape.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: Классы deck.css/motion.css, разрешённые во freeform-фрагментах (бренд-словарь v2).
#: chrome-* инъектит ассемблер ВНУТРЬ freeform-секции, поэтому они в allowlist
#: (промпт всё равно запрещает LLM рисовать chrome — он отвечает только за контент).
ALLOWED_CLASSES = frozenset({
    "slide", "slide--chrome-sm",
    # типографика v2
    "t-hero-156", "t-head-42", "t-head-54", "t-head-36", "t-body-30",
    "t-sub-36", "t-number-320",
    # layout-утилиты (generic). freeform-zone — обёртка контента (ставит ассемблер).
    "freeform-zone", "content", "content--center", "row", "col",
    # шапка-заголовок freeform (ассемблер выносит на уровень слайда, top:72)
    "content-head", "content-head-title", "content-head-sub",
    # бренд-паттерны
    "pattern-lines", "pattern-lines--m", "pattern-lines--xl",
    "pattern-waterfall", "pattern-dots",
    # поверхности (на токенах тем — см. Task 2)
    "card", "accent-block",
    # анимации (m-*/is-* префиксы уже допускаются _is_allowed_class)
    "m-enter", "m-enter-left", "m-enter-right", "m-enter-scale",
    "m-stagger", "m-fade",
    # chrome (инъектит ассемблер)
    "chrome-logo", "chrome-note", "chrome-num",
})


@dataclass(frozen=True)
class LintIssue:
    slide_index: int     # 1-based, по порядку <section class="slide"> в html
    code: str            # forbidden_style | forbidden_tag | unknown_class | multi_accent | overflow | safe_zone
    detail: str = ""


_SLIDE_RE = re.compile(
    r'<section[^>]*class="[^"]*\bslide\b[^"]*"[^>]*>.*?</section>', re.DOTALL)
_CLASS_ATTR_RE = re.compile(r'class="([^"]*)"')
_FORBIDDEN = [
    ("forbidden_style", re.compile(r"\bstyle\s*=", re.IGNORECASE)),
    ("forbidden_style", re.compile(
        r"box-shadow|text-shadow|gradient|border-radius", re.IGNORECASE)),
    ("forbidden_tag", re.compile(r"<\s*(?:i|em|u|script)\b", re.IGNORECASE)),
]


def _is_allowed_class(name: str) -> bool:
    return name in ALLOWED_CLASSES or name.startswith(("m-", "is-"))


def lint_html(html: str) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for index, slide in enumerate(_SLIDE_RE.findall(html), start=1):
        if 'data-template="freeform"' in slide:
            for code, pattern in _FORBIDDEN:
                match = pattern.search(slide)
                if match:
                    issues.append(LintIssue(index, code, match.group(0)))
            for attr in _CLASS_ATTR_RE.findall(slide):
                for name in attr.split():
                    if not _is_allowed_class(name):
                        issues.append(LintIssue(index, "unknown_class", name))
        accents = slide.count("accent-block")
        if accents > 1:
            issues.append(LintIssue(index, "multi_accent", f"{accents} accent-block"))
    return issues


# chrome-текст (колонтитул) не считаем контентом слайда — вырезаем перед проверкой.
_CHROME_NOTE_RE = re.compile(
    r'<div class="chrome-note">.*?</div>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def validate_freeform(slide_html: str, index: int) -> list[LintIssue]:
    """Минимальные структурные гарантии freeform-слайда: непустой + есть текст.

    Остальное (запрещённые приёмы/классы — линтер; вылет за safe-зону —
    measure_overflow) проверяется отдельно. Замечания идут в autofix-круг,
    при повторном провале build деградирует слайд на blank.
    """
    body = _CHROME_NOTE_RE.sub("", slide_html)
    text = _TAG_RE.sub("", body).strip()
    issues: list[LintIssue] = []
    if not text:
        issues.append(LintIssue(index, "freeform_empty",
                                "нет видимого текста в контент-зоне"))
    return issues
