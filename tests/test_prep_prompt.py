"""Промпт «Подготовить документ» знает макеты, которые реально получит раздел.

Промпт копируют во внешний ИИ, чтобы тот разложил документ под генератор. Каталог
макетов в нём набран руками и тихо устареет, стоит добавить или переименовать макет.
Тесты сверяют его с библиотекой: каждый макет, который стеклянная сборка может дать
разделу, в промпте назван, а каждое названное имя существует. Правила про числа
пришли из исследования 2026-09-11: модель, которой велели писать «значение —
подпись», сама чеканила «100%» из «полностью».
"""
import os

os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

import html
import re
from pathlib import Path

from htmlslides.library import TemplateLibrary
from webapp import glass

INDEX = Path(__file__).resolve().parents[1] / "webapp" / "static" / "index.html"


def _prompt() -> str:
    page = INDEX.read_text(encoding="utf-8")
    body = re.search(r'<pre class="lb-pre" id="promptText">(.*?)</pre>', page, re.S)
    assert body, "в index.html нет #promptText"
    return html.unescape(body.group(1))


def _named(prompt: str) -> list[str]:
    """Имена макетов из каталога: «…» сразу после стрелки, в том числе «А» или «Б»."""
    names: list[str] = []
    for first, second in re.findall(r"→ «([^»]+)»(?: или «([^»]+)»)?", prompt):
        names += [n for n in (first, second) if n]
    return names


def test_prompt_names_every_template_a_section_can_get():
    """Макеты, которые ставит система (обложка, контакты, разделители), и цветовые
    двойники разделу не достаются — их в каталоге и не должно быть."""
    prompt = _prompt()
    reachable = [t for t in TemplateLibrary.load().templates
                 if t.id not in glass._EXCLUDED and t.id not in glass._TWINS]
    missing = [t.display_name for t in reachable if f"«{t.display_name}»" not in prompt]
    assert not missing, f"промпт не знает макеты: {missing}"


def test_prompt_names_only_real_templates():
    names = _named(_prompt())
    known = {t.display_name for t in TemplateLibrary.load().templates}
    assert names, "в промпте не нашлось каталога макетов"
    assert not [n for n in names if n not in known], "промпт называет несуществующие макеты"


def test_prompt_forbids_numbers_the_document_does_not_have():
    prompt = _prompt()
    assert "Ни одного числа, которого нет в документе" in prompt
    assert "«полностью» — не «100%»" in prompt          # тот самый случай из исследования
    assert "не складывай разные единицы" in prompt       # stacked-bar сложил рубли со штуками


def test_prompt_matches_how_the_parser_cuts_slides():
    """Любой заголовок — отдельный слайд, заголовок без текста пропадает, раздел
    размером в слайд — ~700 символов (замер на 88 деках)."""
    prompt = _prompt()
    assert "«###»" in prompt and "Часть 2" in prompt and "700" in prompt
    assert "только сам markdown" in prompt
