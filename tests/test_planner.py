"""Planner map→reduce: текстовый вход планируется по разделам параллельно, затем
структура собирается в коде. Регрессия для прод-падения `LLMFormatError: no JSON
object`, которое роняло ВЕСЬ билд: теперь сбой раздела изолирован (эвристический
фолбэк), а cover/разнообразие/accent детерминированы кодом."""
import pytest

from htmlslides.library import TemplateLibrary
from htmlslides.models import DeckPlan
from htmlslides.parsers.base import (InputDoc, ListBlock, Section, TableBlock,
                                     TextBlock)
from htmlslides.pipeline import planner
from htmlslides.pipeline.client import LLMFormatError
from htmlslides.pipeline.planner import (_fallback_template, _SectionPlan,
                                         _SectionSlide)


def _doc():
    return InputDoc(title="Тест-дека", sections=[
        Section(heading="Преимущества", level=2, blocks=[
            ListBlock(items=["A — раз", "B — два", "C — три"])]),
        Section(heading="Безопасность", level=2, blocks=[
            TextBlock(text="Соответствие 152-ФЗ и 187-ФЗ.")]),
    ])


class FakeClient:
    """chat_json возвращает заранее заданные _SectionPlan по очереди вызовов;
    значение-исключение бросается (эмуляция транзиентного сбоя LLM)."""
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0

    def chat_json(self, messages, model_cls, *, max_tokens=4096, retries=2,
                  extra_body=None):
        self.calls += 1
        r = self._replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _sp(template_id, brief="факт"):
    return _SectionPlan(slides=[_SectionSlide(template_id=template_id, brief=brief)])


def test_text_path_maps_sections_and_wraps_cover():
    lib = TemplateLibrary.load()
    client = FakeClient([_sp("three-col"), _sp("statement")])
    plan = planner.plan_deck(client, _doc(), lib)
    ids = [s.template_id for s in plan.slides]
    assert ids[0] == "cover"
    assert "contacts" not in ids            # контакты больше не прикрепляются автоматически
    assert "three-col" in ids and "statement" in ids
    assert [s.index for s in plan.slides] == list(range(1, len(plan.slides) + 1))
    assert client.calls == 2                      # один вызов на раздел


def test_section_failure_falls_back_not_aborts():
    """Сбойный раздел не роняет деку — деградирует на эвристический шаблон."""
    lib = TemplateLibrary.load()
    client = FakeClient([LLMFormatError("no JSON object"), _sp("statement")])
    plan = planner.plan_deck(client, _doc(), lib)
    # деки собралась: cover + 2 контентных (без авто-contacts)
    assert len(plan.slides) == 3
    assert plan.slides[0].template_id == "cover"
    # упавший раздел (3 пункта списка) ушёл на эвристику three-col, брифом — текст раздела
    assert plan.slides[1].template_id == "three-col"
    assert "раз" in plan.slides[1].content["brief"]


def test_variety_swaps_adjacent_duplicate():
    lib = TemplateLibrary.load()
    # оба раздела просят cards-6 подряд → второй должен свапнуться на альтернативу
    client = FakeClient([_sp("cards-6"), _sp("cards-6")])
    plan = planner.plan_deck(client, _doc(), lib)
    content = [s.template_id for s in plan.slides if s.template_id != "cover"]
    assert content[0] == "cards-6"
    assert content[1] != "cards-6"                # разнообразие
    assert content[1] in planner._VARIETY_SWAP["cards-6"]


def test_accent_on_first_statement_only():
    lib = TemplateLibrary.load()
    client = FakeClient([_sp("statement"), _sp("statement")])
    plan = planner.plan_deck(client, _doc(), lib)
    accented = [s for s in plan.slides if isinstance(s.content, dict)
                and s.content.get("accent")]
    assert len(accented) == 1
    assert accented[0].template_id == "statement"


def test_unknown_template_id_falls_back():
    lib = TemplateLibrary.load()
    client = FakeClient([_sp("totally-made-up"), _sp("statement")])
    plan = planner.plan_deck(client, _doc(), lib)
    # неизвестный id заменён эвристикой (3 пункта → three-col), деки валидна
    assert plan.slides[1].template_id in {t.id for t in lib.templates}


def test_empty_plan_is_impossible():
    """DeckPlan всегда непуст: даже без разделов есть хотя бы cover."""
    lib = TemplateLibrary.load()
    client = FakeClient([])
    plan = planner.plan_deck(client, InputDoc(title="Пусто", sections=[]), lib)
    assert [s.template_id for s in plan.slides] == ["cover"]
    assert isinstance(plan, DeckPlan)


def _sec(blocks):
    return Section(heading="Раздел", level=2, blocks=blocks)


def test_fallback_is_data_aware():
    """Регрессия: при фолбэке раздела чарты/диаграммы/таблицы НЕ должны пропадать —
    эвристика выбирает профильный data-шаблон по содержимому, а не только текст."""
    lib = TemplateLibrary.load()
    assert _fallback_template(
        _sec([TableBlock(rows=[["a", "b"], ["1", "2"]])]), lib) == "service-table"
    assert _fallback_template(
        _sec([TextBlock(text="Аптайм 99.9%, покрытие 80%, рост 50%")]), lib) == "donut-chart"
    assert _fallback_template(
        _sec([TextBlock(text="18 млрд выручка, 500 ГБ, 3 раза рост")]), lib) == "stats-row"
    assert _fallback_template(
        _sec([TextBlock(text="было 10 млрд, стало 18 млрд")]), lib) == "bar-chart"
    # без данных — прежнее текстовое поведение
    assert _fallback_template(
        _sec([ListBlock(items=["a", "b", "c"])]), lib) == "three-col"
    assert _fallback_template(
        _sec([TextBlock(text="Просто мысль без чисел.")]), lib) == "statement"


def test_failed_numeric_section_yields_chart_not_text():
    """Сквозной: раздел с числами, у которого LLM-вызов упал, всё равно даёт чарт."""
    lib = TemplateLibrary.load()
    doc = InputDoc(title="D", sections=[
        _sec([TextBlock(text="Доступность 99.95%, покрытие 100%, готовность 70%")])])
    client = FakeClient([LLMFormatError("no JSON object")])
    plan = planner.plan_deck(client, doc, lib)
    assert plan.slides[1].template_id == "donut-chart"   # не текстовый шаблон


# ===================== Разделители секций (дивайдеры) =====================

def test_part_title_sections_become_dividers():
    """Раздел-«шапка части» (заголовок без тела) -> детерминированный разделитель,
    а не титул; LLM для него не зовётся, нумерация 01/02, макеты чередуются."""
    lib = TemplateLibrary.load()
    doc = InputDoc(title="D", sections=[
        Section(heading="Часть 1", level=1, blocks=[]),
        Section(heading="Обзор", level=2, blocks=[TextBlock(text="Обзор рынка.")]),
        Section(heading="Часть 2", level=1, blocks=[]),
        Section(heading="Итоги", level=2, blocks=[TextBlock(text="Выводы по проекту.")]),
    ])
    client = FakeClient([_sp("statement"), _sp("statement")])
    plan = planner.plan_deck(client, doc, lib)
    ids = [s.template_id for s in plan.slides]
    assert client.calls == 2                       # шапки частей LLM не трогают
    assert ids[0] == "cover" and "contacts" not in ids
    dots = next(s for s in plan.slides if s.template_id == "section-dots")
    frame = next(s for s in plan.slides if s.template_id == "section-frame")
    assert dots.content == {"label": "ЧАСТЬ 1", "number": "01"}
    assert frame.content == {"label": "ЧАСТЬ 2", "number": "02"}
    # разделитель заполнен детерминированно — без ключа brief (филлер его пропустит)
    assert "brief" not in dots.content
    assert not lib.validate_content("section-dots", dots.content)


def test_top_level_sections_with_subsections_get_dividers():
    """Главы верхнего уровня, у которых есть вложенные подразделы, получают
    разделитель перед своим контентом — распределение по структуре."""
    lib = TemplateLibrary.load()
    doc = InputDoc(title="D", sections=[
        Section(heading="Инфраструктура", level=1, blocks=[TextBlock(text="Введение.")]),
        Section(heading="Сеть", level=2, blocks=[TextBlock(text="Про сеть.")]),
        Section(heading="Хранилище", level=2, blocks=[TextBlock(text="Про диски.")]),
        Section(heading="Безопасность", level=1, blocks=[TextBlock(text="Введение 2.")]),
        Section(heading="Доступ", level=2, blocks=[TextBlock(text="Про доступ.")]),
    ])
    client = FakeClient([_sp("statement")] * 5)
    plan = planner.plan_deck(client, doc, lib)
    ids = [s.template_id for s in plan.slides]
    assert ids.count("section-dots") + ids.count("section-frame") == 2
    assert client.calls == 5                       # у всех 5 разделов есть контент
    # дивайдер стоит ПЕРЕД контентом своей главы
    assert ids[1] in ("section-dots", "section-frame")


def test_flat_doc_gets_no_dividers():
    """Плоский документ (все разделы одного уровня с контентом) — без разделителей."""
    lib = TemplateLibrary.load()
    doc = InputDoc(title="D", sections=[
        Section(heading="A", level=2, blocks=[TextBlock(text="Текст A.")]),
        Section(heading="B", level=2, blocks=[TextBlock(text="Текст B.")]),
        Section(heading="C", level=2, blocks=[TextBlock(text="Текст C.")]),
    ])
    client = FakeClient([_sp("statement")] * 3)
    plan = planner.plan_deck(client, doc, lib)
    ids = [s.template_id for s in plan.slides]
    assert "section-dots" not in ids and "section-frame" not in ids


def test_single_divider_is_suppressed_and_section_survives():
    """Один-единственный кандидат подавляется (одинокий дивайдер бессмыслен), но
    сам раздел не теряется — уходит в контент как раньше."""
    lib = TemplateLibrary.load()
    doc = InputDoc(title="D", sections=[
        Section(heading="Часть 1", level=1, blocks=[]),
        Section(heading="Обзор", level=2, blocks=[TextBlock(text="Обзор.")]),
    ])
    client = FakeClient([_sp("statement"), _sp("statement")])
    plan = planner.plan_deck(client, doc, lib)
    ids = [s.template_id for s in plan.slides]
    assert "section-dots" not in ids and "section-frame" not in ids
    assert client.calls == 2                       # оба раздела ушли в body
    assert len(plan.slides) == 3                   # cover + 2, ничего не пропало


def test_divider_label_trims_on_word_boundary():
    lib = TemplateLibrary.load()
    # section-frame label max_chars=14 → режем по слову, капсом
    assert planner._divider_label("Информационная безопасность", 14) == "ИНФОРМАЦИОННАЯ"
    assert planner._divider_label("Часть 1", 16) == "ЧАСТЬ 1"
