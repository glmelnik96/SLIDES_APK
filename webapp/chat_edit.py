"""Chat-driven single-slide edit of an HTML deck via Kimi.

Given the assembled deck HTML and a 1-based slide index, send that slide's
<section> plus the user's instruction to Kimi and swap the revised <section>
back into the deck. Reuses htmlslides' brand rules and allowed-class allowlist
for on-brand output. Operates on the persisted deck.html — no changes to the
source engine repositories.
"""
from __future__ import annotations

import re
from typing import Any

_SECTION_RE = re.compile(r"<section\b[^>]*>.*?</section>", re.DOTALL | re.IGNORECASE)
_FORBIDDEN = re.compile(
    r"\bstyle\s*=|<\s*(?:i|em|u|script)\b|box-shadow|text-shadow|gradient|border-radius",
    re.IGNORECASE,
)
# Kimi-K2.6 always reasons (reasoning counts against completion tokens), so a full
# content slide needs a large budget or the closing </section> gets truncated away.
# Mirrors the generous Kimi budgets in Slides_bot/llm/roles.py.
_MAX_TOKENS = 16000
_FENCE_RE = re.compile(r"```(?:html)?\s*(.*?)```", re.DOTALL)


def count_slides(deck_html: str) -> int:
    return len(_SECTION_RE.findall(deck_html))


def nth_section(deck_html: str, index: int) -> str:
    """Return the index-th (1-based) ``<section>`` of a deck, or "" if absent."""
    spans = _SECTION_RE.findall(deck_html)
    return spans[index - 1] if 1 <= index <= len(spans) else ""


def _replace_nth_section(deck_html: str, index: int, new_section: str) -> str:
    spans = [m.span() for m in _SECTION_RE.finditer(deck_html)]
    if not (1 <= index <= len(spans)):
        raise ValueError(f"slide index {index} out of range 1..{len(spans)}")
    start, end = spans[index - 1]
    return deck_html[:start] + new_section + deck_html[end:]


def _extract_section(reply: str) -> str:
    fence = _FENCE_RE.search(reply)
    text = fence.group(1) if fence else reply
    m = _SECTION_RE.search(text)
    if not m:
        raise ValueError("model reply contained no complete <section> "
                         "(likely truncated — increase token budget)")
    return m.group(0)


def _kimi():
    from htmlslides.pipeline.client import KimiClient
    # Interactive single-slide edit. The big win is disabling Kimi-K2.6's reasoning:
    # by default it spends 120-250s "thinking" before emitting the <section>, which
    # made edits crawl. A single-slide HTML rewrite doesn't need deep reasoning, so
    # we turn thinking off (text-only, valid here — no images) for a fast response.
    # One attempt, no retry, with a generous timeout as a backstop under the
    # browser's 5-min ceiling.
    return KimiClient(timeout=280.0, max_retries=0,
                      extra_body={"thinking": {"type": "disabled"}})


def _system_prompt() -> str:
    from htmlslides.brand import brand_rules
    from htmlslides.pipeline.linter import ALLOWED_CLASSES
    classes = ", ".join(sorted(ALLOWED_CLASSES))
    return (
        "Ты редактируешь ОДИН слайд презентации Cloud.ru, заданный как HTML <section>.\n"
        "Примени инструкцию пользователя, сохрани бренд и структуру слайда.\n"
        "Верни ТОЛЬКО исправленный <section ...>...</section>, без пояснений и без ```.\n"
        f"Используй только классы: {classes}.\n"
        "Запрещено: атрибут style, теги i/em/u/script, тени, градиенты, скругления.\n\n"
        f"БРЕНД-ПРАВИЛА:\n{brand_rules()}"
    )


def rewrite_slide(deck_html: str, slide_index: int, instruction: str,
                  client: Any | None = None) -> str:
    """Return deck HTML with slide `slide_index` (1-based) rewritten per instruction."""
    sections = _SECTION_RE.findall(deck_html)
    if not (1 <= slide_index <= len(sections)):
        raise ValueError(f"slide index {slide_index} out of range 1..{len(sections)}")
    current = sections[slide_index - 1]
    client = client or _kimi()
    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content":
            f"Текущий слайд (HTML):\n{current}\n\n"
            f"Инструкция:\n{instruction}\n\n"
            "Верни ТОЛЬКО исправленный <section>...</section>."},
    ]
    reply = client.chat(messages, max_tokens=_MAX_TOKENS)
    new_section = _extract_section(reply)
    if _FORBIDDEN.search(new_section):
        retry = messages + [
            {"role": "assistant", "content": reply},
            {"role": "user", "content":
                "В ответе запрещённые приёмы (style=/i/em/u/script/тени/градиенты/"
                "скругления). Перепиши без них, верни ТОЛЬКО <section>...</section>."},
        ]
        reply = client.chat(retry, max_tokens=_MAX_TOKENS)
        new_section = _extract_section(reply)
    return _replace_nth_section(deck_html, slide_index, new_section)
