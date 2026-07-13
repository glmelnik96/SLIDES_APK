"""Этап 2 точного переноса: ИИ-вёрстка exact-слайда с гарантией дословности.

Схема: Section → атомы (дословный текст) → ИИ отдаёт брендовую вёрстку ТОЛЬКО с
метками {{aK}} → проверки чистоты/полноты → программа подставляет дословный текст.
ИИ физически не печатает слова источника, поэтому исказить их не может. Провал
проверки/сети → остаётся детерминированный html Этапа 1 (build_exact_plan).
"""
from __future__ import annotations

import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from html import escape, unescape
from typing import Optional

from ..brand import brand_rules
from ..models import DeckPlan, SlidePlan
from ..parsers.base import (CodeBlock, ImageBlock, InputDoc, ListBlock, Section,
                            TableBlock, TextBlock)
from .exact_builder import _block_html
from .filler import (_FILL_MAX_TOKENS, _FILL_NO_THINK, _FORBIDDEN_FRAGMENT,
                     _TRANSIENT_API_ERRORS, _extract_html)
from .linter import ALLOWED_CLASSES, _is_allowed_class


@dataclass(frozen=True)
class Atom:
    """Минимальный дословный кусок слайда. block — исходный блок для структурных
    видов (table/image/code), которые программа разворачивает целиком."""
    id: str
    kind: str          # heading | paragraph | list_item | table | image | code
    text: str
    block: object = None


def atomize(section: Section) -> list[Atom]:
    """Section → список Atom (порядок сохраняем). Заголовок, если есть, всегда a1."""
    atoms: list[Atom] = []
    counter = 0

    def _next_id() -> str:
        nonlocal counter
        counter += 1
        return f"a{counter}"

    if section.heading:
        atoms.append(Atom(_next_id(), "heading", section.heading))
    for block in section.blocks:
        if isinstance(block, TextBlock):
            atoms.append(Atom(_next_id(), "paragraph", block.text))
        elif isinstance(block, ListBlock):
            for item in block.items:
                atoms.append(Atom(_next_id(), "list_item", item))
        elif isinstance(block, TableBlock):
            atoms.append(Atom(_next_id(), "table", "", block))
        elif isinstance(block, ImageBlock):
            atoms.append(Atom(_next_id(), "image", block.alt or "", block))
        elif isinstance(block, CodeBlock):
            atoms.append(Atom(_next_id(), "code", block.text, block))
    return atoms


# Метка-заполнитель: {{aK}} либо {{aK:i-j}} (i,j — 1-based номера слов для <b>).
_MARK_RE = re.compile(r"\{\{\s*(a\d+)(?::(\d+)-(\d+))?\s*\}\}")


def _bold_word_range(text: str, i: int, j: int) -> str:
    """Escaped-текст с <b> вокруг слов i..j (1-based). Символы сохраняем дословно:
    <b> охватывает ровно эти слова и разделители между ними. Неверный диапазон →
    просто escaped-текст без акцента (текст важнее акцента)."""
    tokens = re.split(r"(\s+)", text)                  # чётные — слова, нечётные — пробелы
    word_positions = [k for k, tok in enumerate(tokens) if tok and not tok.isspace()]
    if i < 1 or j < i or j > len(word_positions):
        return escape(text)
    start, end = word_positions[i - 1], word_positions[j - 1]
    out: list[str] = []
    for k, tok in enumerate(tokens):
        piece = escape(tok)
        if k == start:
            piece = "<b>" + piece
        if k == end:
            piece = piece + "</b>"
        out.append(piece)
    return "".join(out)


def _emphasize(text: str, i: Optional[int], j: Optional[int], *, br: bool) -> str:
    html = escape(text) if i is None else _bold_word_range(text, i, j)
    return html.replace("\n", "<br>") if br else html


def _atom_html(atom: Atom, i: Optional[int], j: Optional[int]) -> str:
    """Один атом → дословный html. Структурные развернём через _block_html
    (диапазон слов к ним неприменим)."""
    if atom.kind in ("table", "image", "code"):
        html, _ = _block_html(atom.block)
        return html
    return _emphasize(atom.text, i, j, br=(atom.kind == "paragraph"))


def _render(raw_html: str, atoms: list[Atom]) -> str:
    """Заменить все метки {{aK}}/{{aK:i-j}} на дословный html атомов."""
    by_id = {a.id: a for a in atoms}

    def _sub(m: re.Match) -> str:
        atom = by_id[m.group(1)]                       # id гарантирован проверкой полноты
        i = int(m.group(2)) if m.group(2) else None
        j = int(m.group(3)) if m.group(3) else None
        return _atom_html(atom, i, j)

    return _MARK_RE.sub(_sub, raw_html)


_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_CLASS_ATTR_RE = re.compile(r'class="([^"]*)"')
_ALNUM_RE = re.compile(r"[^\W_]")                       # любая буква/цифра (в т.ч. кириллица)
_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)     # HTML-комментарий целиком
_ATTR_NAME_RE = re.compile(r'([A-Za-z_:][\w:.-]*)\s*=')  # имя атрибута внутри тега
# ИИ верстает только классами; единственные допустимые атрибуты. Любой другой
# (title/alt/data-*/style/...) мог бы протащить слова ИИ мимо проверки чистоты
# (та срезает теги целиком) — поэтому бьём по белому списку имён атрибутов.
_ALLOWED_ATTRS = {"class", "data-template"}


def _verify(raw_html: str, atoms: list[Atom]) -> Optional[str]:
    """None — ок; иначе строка-замечание для ретрая (см. порядок в docstring задачи)."""
    used = [m.group(1) for m in _MARK_RE.finditer(raw_html)]
    if Counter(used) != Counter(a.id for a in atoms):
        return ("Метки не совпадают с атомами: используй КАЖДУЮ {{aK}} ровно один раз "
                "и не выдумывай лишних.")
    if _COMMENT_RE.search(raw_html):
        return ("HTML-комментарии запрещены — убери <!-- ... -->. Весь текст "
                "подставит программа метками {{aK}}.")
    stripped = _TAG_RE.sub("", _MARK_RE.sub("", raw_html))
    if _ALNUM_RE.search(unescape(stripped)):
        return ("Есть текст вне меток — печатать слова запрещено. Ставь ТОЛЬКО метки "
                "{{aK}}, весь текст подставит программа.")
    if _FORBIDDEN_FRAGMENT.search(raw_html):
        return ("Запрещённые приёмы (style= / i / em / u / script / тени / градиенты / "
                "скругления). Перепиши без них.")
    for tag in _TAG_RE.findall(raw_html):
        for name in _ATTR_NAME_RE.findall(tag):
            if name.lower() not in _ALLOWED_ATTRS:
                return (f"Атрибут «{name}» запрещён — оставляй только class. Свои слова "
                        "(в title/alt/data-*) печатать нельзя, весь текст даёт программа.")
    for attr in _CLASS_ATTR_RE.findall(raw_html):
        for name in attr.split():
            if not _is_allowed_class(name):
                return f"Неизвестный класс «{name}» — используй только разрешённые классы."
    return None


# Бренд-правила: тот же источник, что у filler. Скобок {} в rules.md нет, но мы и
# так подставляем классы через .replace (не .format) — двойные {{ }} в промпте цели.
_BRAND_BLOCK = f"\n\nБРЕНД-ПРАВИЛА:\n{brand_rules()}"

# ВНИМАНИЕ: обычная (НЕ f/format) строка — {{aK}} остаются литералами; список
# классов подставляется через .replace("__CLASSES__", ...). Не переводить на .format!
EXACT_DESIGN_SYSTEM = ("""\
Ты — дизайнер-верстальщик ОДНОГО слайда презентации Cloud.ru. Текст менять НЕЛЬЗЯ:
слова даны как атомы, ты только КРАСИВО раскладываешь их по бренд-вёрстке.

Дано: список атомов слайда — метка {{aK}}, вид [kind] и текст (текст показан ТОЛЬКО
для понимания раскладки; печатать его запрещено).

Верни ТОЛЬКО код в блоке ```html ... ```.
Требования:
- Корень: <section class="slide" data-template="freeform"> ...контент... </section>.
- Текст ставь ТОЛЬКО метками {{aK}}. НЕ печатай слова из атомов. Каждый атом —
  РОВНО один раз.
- Если есть атом-заголовок (первый, вид [heading]) — ПЕРВЫМ элементом шапка:
  <div class="content-head"><h3 class="content-head-title t-head-42">{{a1}}</h3></div>.
- Дальше — ОБЫЧНЫМ ПОТОКОМ сверху вниз: колонки .row>.col, карточки .card,
  число-герой .t-number-320, крупный тезис .t-hero-156; максимум один .accent-block.
  Пункт вида «Имя — описание» удобно оформить карточкой (метку целиком в .card).
- Акценты БЕЗ печати слов: целый атом можно обернуть в .t-number-320/.t-hero-156/
  .accent-block или в <b> (например <b>{{a4}}</b>). Часть фразы — метка с диапазоном
  слов {{aK:i-j}} (жирными станут слова с i-го по j-е). Печатай только id и номера.
- Используй ТОЛЬКО классы: __CLASSES__.
- Запрещено: атрибут style, теги <i>/<em>/<u>/<script>, тени, градиенты, скругления,
  неизвестные классы. Контент должен умещаться в зону — лучше меньше и крупнее.""" + _BRAND_BLOCK)


def _atoms_prompt(atoms: list[Atom]) -> str:
    lines: list[str] = []
    for atom in atoms:
        if atom.kind in ("table", "image", "code"):
            desc = {"table": "таблица", "image": "картинка", "code": "код"}[atom.kind]
            lines.append(f"{{{{{atom.id}}}}} [{atom.kind}] — {desc} "
                         "(вставится целиком как есть)")
        else:
            lines.append(f"{{{{{atom.id}}}}} [{atom.kind}] {atom.text}")
    return ("Атомы этого слайда — используй КАЖДУЮ метку ровно один раз, "
            "сам текст не печатай:\n" + "\n".join(lines))


def _design_slide(client, section: Section, slide: SlidePlan) -> SlidePlan:
    """Один слайд: ИИ-вёрстка по протоколу меток. Провал проверки после ретрая →
    вернуть slide как есть (фолбэк-html Этапа 1). Дословность не страдает никогда."""
    atoms = atomize(section)
    if not atoms:
        return slide
    system = EXACT_DESIGN_SYSTEM.replace(
        "__CLASSES__", ", ".join(sorted(ALLOWED_CLASSES)))
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": _atoms_prompt(atoms)}]
    reply = client.chat(messages, max_tokens=_FILL_MAX_TOKENS, extra_body=_FILL_NO_THINK)
    raw = _extract_html(reply)
    problem = _verify(raw, atoms)
    if problem:
        retry = messages + [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": problem +
             " Верни ТОЛЬКО ```html-блок: та же брендовая вёрстка, но текст — только "
             "метками {{aK}}, каждый атом ровно один раз, без своих слов."}]
        reply = client.chat(retry, max_tokens=_FILL_MAX_TOKENS, extra_body=_FILL_NO_THINK)
        raw = _extract_html(reply)
        problem = _verify(raw, atoms)
        if problem:
            return slide
    designed = _render(raw, atoms)
    return slide.model_copy(update={"content": {"html": designed, "exact": True}})


def design_exact_deck(client, doc: InputDoc, plan: DeckPlan, *,
                      workers: int = 8, progress=lambda message: None) -> DeckPlan:
    """ИИ-вёрстка всех exact-слайдов параллельно. Сбой одного (проверка/сеть) не
    валит деку — остаётся его детерминированный html. Слайд↔секция по порядку."""
    sections = doc.sections
    total = len(plan.slides)
    done_lock = threading.Lock()
    done = 0

    def _tick() -> None:
        nonlocal done
        with done_lock:
            done += 1
            k = done
        progress(f"design: слайд {k}/{total}")

    def one(pos: int, slide: SlidePlan) -> SlidePlan:
        if pos >= len(sections):
            return slide
        try:
            result = _design_slide(client, sections[pos], slide)
        except _TRANSIENT_API_ERRORS as exc:
            progress(f"warn: слайд {slide.index} — сбой API ({type(exc).__name__}); "
                     "оставляю простой вид (как Этап 1)")
            result = slide
        except Exception as exc:                       # noqa: BLE001
            # Любой иной сбой (400/401, неверный формат, неожиданное) НЕ должен
            # ронять всю деку: фолбэк-html Этапа 1 всегда корректен и дословен.
            progress(f"warn: слайд {slide.index} — не удалось сверстать "
                     f"({type(exc).__name__}); оставляю простой вид (как Этап 1)")
            result = slide
        _tick()
        return result

    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(one, pos, s) for pos, s in enumerate(plan.slides)]
        slides = [f.result() for f in futures]
    finally:
        pool.shutdown()
    if getattr(client, "usage_total", None):
        progress(f"design: токены {client.usage_total}")
    return plan.model_copy(update={"slides": slides})
