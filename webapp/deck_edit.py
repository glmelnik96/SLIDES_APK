"""Persist the browser-edited deck HTML and count its slides."""
from __future__ import annotations

import hashlib
import itertools
import re
from pathlib import Path
from urllib.parse import quote

from webapp.paths import session_dir

_SLIDE_RE = re.compile(r'<section[^>]*\bclass="[^"]*\bslide\b', re.IGNORECASE)
# The browser editor injects contenteditable="true" on leaf nodes for in-place
# editing; strip it on persist so the saved/downloaded/exported deck isn't left
# globally editable. Matches the attribute with or without a value.
_CONTENTEDITABLE_RE = re.compile(r'\s+contenteditable(?:\s*=\s*"[^"]*")?',
                                 re.IGNORECASE)
# deck.js marks the visible slide with class "is-active". The editor serializes
# the live DOM on save, so that runtime marker gets baked onto whichever slide
# was showing. On reload deck.js activates slide 1 but never clears the stale
# marker, leaving two slides visible at once — the later one (e.g. contacts)
# overlaid every slide ("all slides became contacts after refresh"). Strip the
# token from <section> class attributes only; the identical token also names CSS
# animation selectors in the embedded <style>, which must stay intact.
_SECTION_CLASS_RE = re.compile(r'(<section\b[^>]*\bclass=")([^"]*)(")',
                               re.IGNORECASE)
# The editor preview loads the deck with &editor=1, and deck.js puts "no-motion"
# on <body> (quiet preview: entrances/loops shown in their final state so
# repeated saves don't restart the show). Serializing the live DOM bakes that
# runtime class into deck.html, and motion.css's .no-motion rules then kill ALL
# animations in the saved/downloaded deck. Strip the token from the <body> class
# attribute only — the identical token names the kill-switch selectors in the
# embedded motion.css, which must stay intact.
_BODY_CLASS_RE = re.compile(r'(<body\b[^>]*\bclass=")([^"]*)(")', re.IGNORECASE)
# Recoloring a built deck is one attribute: deck.css resolves every component
# color from tokens keyed by <html data-theme="dark|light">, so flipping the
# attribute repaints the whole deck; PNG/PPTX exports re-render from deck.html
# and pick the new theme up with no engine involvement.
THEMES = ("dark", "light")
# Встроенные ресурсы деки — их выносит наружу только миниатюра (extract_slide).
_INLINE_STYLE_RE = re.compile(r"<style\b[^>]*>[\s\S]*?</style>", re.IGNORECASE)
_INLINE_SCRIPT_RE = re.compile(r"<script\b(?![^>]*\bsrc=)[^>]*>[\s\S]*?</script>",
                               re.IGNORECASE)

_THEME_ATTR_RE = re.compile(r'(<html\b[^>]*?\bdata-theme=")([^"]*)(")',
                            re.IGNORECASE)
_HTML_TAG_RE = re.compile(r'<html\b[^>]*>', re.IGNORECASE)
# Theme tokens are var(--…); a LITERAL hex/rgb inside a slide's markup (freeform
# slide from chat-edit/autofix) was authored against one background and may not
# survive a repaint — flag such slides so the UI can suggest a visual check.
# Attribute context (style=/fill=/…) keeps template comments and deck.js out.
_HARDCODED_COLOR_RE = re.compile(
    r'\b(?:style|fill|stroke|color|bgcolor)\s*=\s*"[^"]*'
    r'(?:#[0-9a-fA-F]{3,8}\b|rgba?\s*\()',
    re.IGNORECASE)


def count_slides(html: str) -> int:
    return len(_SLIDE_RE.findall(html))


def get_theme(html: str) -> str:
    m = _THEME_ATTR_RE.search(html)
    return m.group(2) if m and m.group(2) in THEMES else "dark"


def set_theme(html: str, theme: str) -> str:
    if theme not in THEMES:
        raise ValueError(f"unknown theme: {theme!r} (dark|light)")
    if _THEME_ATTR_RE.search(html):
        return _THEME_ATTR_RE.sub(
            lambda m: m.group(1) + theme + m.group(3), html, count=1)
    # No attribute (e.g. a deck saved by an old editor build) — add it, else the
    # CSS token blocks wouldn't match and the deck would render unthemed.
    return _HTML_TAG_RE.sub(
        lambda m: m.group(0)[:-1] + f' data-theme="{theme}">', html, count=1)


def slides_with_hardcoded_colors(html: str) -> list[int]:
    """1-based indices of slides whose markup carries literal colors (see
    _HARDCODED_COLOR_RE) — candidates for a visual check after a repaint."""
    starts = [m.start() for m in _SLIDE_RE.finditer(html)]
    flagged = []
    for i, start in enumerate(starts):
        # Bound at the section's close, not the next slide: the tail after the
        # LAST slide holds the embedded deck.js, which must not leak into scan.
        close = html.find("</section>", start)
        end = close if close != -1 else (
            starts[i + 1] if i + 1 < len(starts) else len(html))
        if _HARDCODED_COLOR_RE.search(html[start:end]):
            flagged.append(i + 1)
    return flagged


def _tag_body(block: str) -> str:
    """Содержимое <style>/<script> без самих тегов."""
    return block[block.index(">") + 1:block.rindex("<")]


def assets_version(html: str) -> str:
    """Отпечаток обвязки — ключ её кэша в браузере.

    Считаем по СОДЕРЖИМОМУ, а не по mtime деки: правка текста слайда трогает
    файл, но не стили, и версия от mtime сбрасывала бы кэш на каждом сохранении
    — то есть возвращала бы ровно ту стоимость, ради которой всё и затевалось.
    Перекрас dark↔light содержимое меняет, и новый адрес получается сам.
    Замер: извлечь и хешировать 330 КБ — 4 мс."""
    css, js = inline_assets(html)
    digest = hashlib.blake2b(css.encode("utf-8"), digest_size=8)
    for script in js:
        digest.update(script.encode("utf-8"))
    return digest.hexdigest()


def inline_assets(html: str) -> tuple[str, list[str]]:
    """Встроенные в деку ресурсы: (css, [js…]) — ровно в порядке появления.

    Отдаются тем же эндпоинтом, что и сама дека: так миниатюра ссылается на код,
    байт в байт совпадающий с её собственной вёрсткой, а не на похожий."""
    css = "\n".join(_tag_body(m.group(0)) for m in _INLINE_STYLE_RE.finditer(html))
    js = [_tag_body(m.group(0)) for m in _INLINE_SCRIPT_RE.finditer(html)]
    return css, js


def _share_assets(html: str, version: str) -> str:
    """Заменить встроенные <style>/<script> ссылками на общий deck-asset.

    Ссылки ОТНОСИТЕЛЬНЫЕ: документ отдаётся по .../jobs/<sid>/deck, поэтому
    «deck-asset» разрешается в .../jobs/<sid>/deck-asset и одинаково работает
    локально и за шлюзом с префиксом /slides — вычислять базу не нужно.

    Каждый скрипт остаётся ОТДЕЛЬНЫМ тегом. Склеить их в один файл было бы на
    запрос меньше, но исключение в первом унесло бы второй: сейчас deck.js и
    diagram.js падают независимо, и терять это ради байтов не стоит.
    """
    ver = quote(version, safe="")
    html = _INLINE_STYLE_RE.sub(
        '<link rel="stylesheet" href="deck-asset?kind=css&amp;v=' + ver + '">',
        html, count=1)
    counter = itertools.count()
    return _INLINE_SCRIPT_RE.sub(
        lambda _m: '<script src="deck-asset?kind=js&amp;i=' + str(next(counter))
                   + "&amp;v=" + ver + '"></script>',
        html)


def extract_slide(html: str, index: int, *,
                  shared_version: str | None = None) -> str | None:
    """Полный HTML-документ, в котором оставлен ТОЛЬКО слайд index (1-based).

    Шапка (стили, токены темы) и хвост (встроенный deck.js) сохраняются —
    документ остаётся самодостаточным. Лёгкие миниатюры редактора грузят один
    слайд вместо всей деки: 8 миниатюр = 8 документов, а не 8×N рендеров.
    Вне диапазона — None.

    shared_version переводит документ в режим ОБЩИХ ресурсов: встроенные
    <style>/<script> заменяются ссылками на соседний deck-asset. Замер
    2026-09-10: миниатюра весит 334 КБ, из которых 330 (99%) — та же самая
    обвязка (236 КБ CSS + 94 КБ JS), а собственно слайд 3 КБ. Браузер тянет и
    разбирает её заново для КАЖДОЙ миниатюры; со ссылкой — один раз на ленту
    (замер на 20 миниатюрах: 4824 КБ → 309 КБ, 217 мс → 107 мс).

    Режим строго по запросу, а не по умолчанию: этот же extract_slide питает
    точечное «Улучшить слайд» (glass.py), и тот отдаёт html в Playwright, где
    относительная ссылка на сессионный URL не разрешится — слайд поехал бы в
    vision-QA голым, без единого стиля.

    Значение уходит в URL как ?v=: без него перекрас деки dark↔light показывал
    бы в миниатюрах кэш от прошлой темы."""
    starts = [m.start() for m in _SLIDE_RE.finditer(html)]
    if not 1 <= index <= len(starts):
        return None
    spans = []
    for i, start in enumerate(starts):
        close = html.find("</section>", start)
        end = close + len("</section>") if close != -1 else (
            starts[i + 1] if i + 1 < len(starts) else len(html))
        spans.append((start, end))
    out = (html[:spans[0][0]] + html[spans[index - 1][0]:spans[index - 1][1]]
           + html[spans[-1][1]:])
    return out if shared_version is None else _share_assets(out, shared_version)


def strip_contenteditable(html: str) -> str:
    return _CONTENTEDITABLE_RE.sub("", html)


def strip_is_active(html: str) -> str:
    def _clean(m: "re.Match[str]") -> str:
        classes = [c for c in m.group(2).split() if c != "is-active"]
        return m.group(1) + " ".join(classes) + m.group(3)

    return _SECTION_CLASS_RE.sub(_clean, html)


def strip_no_motion(html: str) -> str:
    def _clean(m: "re.Match[str]") -> str:
        classes = [c for c in m.group(2).split() if c != "no-motion"]
        return m.group(1) + " ".join(classes) + m.group(3)

    return _BODY_CLASS_RE.sub(_clean, html)


def deck_path(session_id: str) -> Path:
    return session_dir(session_id) / "deck.html"


def save_deck(session_id: str, html: str) -> Path:
    if not html or not html.strip():
        raise ValueError("пустой HTML деки — сохранять нечего")
    path = deck_path(session_id)
    path.write_text(
        strip_no_motion(strip_is_active(strip_contenteditable(html))),
        encoding="utf-8")
    return path


def ensure_deck(session_id: str, source_path: str | None) -> Path | None:
    """Return the session's deck.html. If it doesn't exist yet, seed it by copying
    the engine's produced deck (source_path, an absolute path from the terminal
    `done` event). Returns None if there is no deck to serve."""
    p = deck_path(session_id)
    if p.is_file():
        return p
    if source_path and Path(source_path).is_file():
        p.write_text(Path(source_path).read_text(encoding="utf-8"), encoding="utf-8")
        return p
    return None
