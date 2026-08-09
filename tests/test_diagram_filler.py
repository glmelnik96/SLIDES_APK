"""Diagram Filler: строгий DiagramSpec по брифу — валидация, ретрай, деградация.

Fake-клиент вместо LLM (паттерн test_filler.py): проверяем контрактную обвязку,
а не модель. Ключевые ветки: валидный ответ → канонический JSON в слоте diagram;
невалидная схема → ретрай с перечнем претензий → DiagramFillError; fill_slide
переводит её в FillError (fill_deck уже умеет фолбэк на blank); скип-путь для
готового контента из редактора не зовёт LLM.
"""
import json
import re

import pytest

from htmlslides.diagrams import AVAILABLE_KINDS
from htmlslides.library import TemplateLibrary
from htmlslides.models import SlidePlan
from htmlslides.pipeline import diagram_filler, filler
from htmlslides.pipeline.diagram_filler import (DiagramFillError,
                                                DiagramSlideReply,
                                                fill_diagram)


@pytest.fixture(scope="module")
def library():
    return TemplateLibrary.load()


def _slide(content=None):
    return SlidePlan(index=3, type="content", template_id="diagram",
                     freeform=False,
                     content=content or {"brief": "Процесс подключения услуги"})


def _reply(**over):
    base = {
        "title": "Как проходит заявка",
        "subtitle": "",
        "diagram": {
            "kind": "flowchart",
            "nodes": [{"id": "a", "label": "Заявка", "shape": "start"},
                      {"id": "b", "label": "Готово", "shape": "end"}],
            "edges": [{"from": "a", "to": "b"}],
        },
    }
    base.update(over)
    return DiagramSlideReply(**base)


class FakeClient:
    """Отдаёт заготовленные ответы по очереди; хранит все запросы."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat_json(self, messages, model_cls, *, max_tokens=4096,
                  retries=2, extra_body=None):
        self.calls.append({"messages": messages, "extra_body": extra_body,
                           "max_tokens": max_tokens})
        return self.replies.pop(0)


def test_valid_reply_becomes_canonical_slot_content(library):
    client = FakeClient([_reply()])
    out = fill_diagram(client, library, _slide(), deck_title="Дека")
    assert out.content["title"] == "Как проходит заявка"
    spec = json.loads(out.content["diagram"])          # канонический JSON-текст
    assert spec["kind"] == "flowchart"
    assert spec["edges"][0]["from"] == "a"             # by_alias, не from_
    # слот-контракт шаблона diagram выполнен — деку можно собирать
    assert not library.validate_content("diagram", out.content)
    # reasoning выключен — это текст-в-JSON, как у слот-филла
    assert client.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_invalid_diagram_retries_with_error_list_then_ok(library):
    bad = _reply(diagram={
        "kind": "flowchart",
        "nodes": [{"id": "a", "label": "Один"}, {"id": "b", "label": "Два"}],
        "edges": [{"from": "a", "to": "ghost"}],       # ребро в никуда
    })
    client = FakeClient([bad, _reply()])
    out = fill_diagram(client, library, _slide(), deck_title="Дека")
    assert json.loads(out.content["diagram"])["kind"] == "flowchart"
    assert len(client.calls) == 2
    # ретрай несёт перечень претензий и прошлый ответ модели
    retry_msgs = client.calls[1]["messages"]
    assert retry_msgs[-1]["role"] == "user"
    assert "ghost" in retry_msgs[-1]["content"]


def test_still_invalid_after_retry_raises_diagram_fill_error(library):
    bad = _reply(diagram={"kind": "cycle",
                          "nodes": [{"id": "a", "label": "Один"}]})
    client = FakeClient([bad, bad])
    with pytest.raises(DiagramFillError) as err:
        fill_diagram(client, library, _slide(), deck_title="Дека")
    assert "от 3 до" in str(err.value) and "стадий" in str(err.value)


def test_title_cap_violation_also_retried(library):
    long_title = _reply(title="х" * 80)                # слот title: max 54
    client = FakeClient([long_title, _reply()])
    out = fill_diagram(client, library, _slide())
    assert len(client.calls) == 2
    assert out.content["title"] == "Как проходит заявка"


def test_ready_content_skips_llm(library):
    """Пикер редактора уже положил валидный спек — LLM не зовём (симметрично
    скип-пути _fill_template для детерминированных слайдов)."""
    from htmlslides.diagrams.catalog import sample_spec
    content = {"title": "Готовая схема", "subtitle": "",
               "diagram": json.dumps(sample_spec("cycle"), ensure_ascii=False)}
    client = FakeClient([])                            # любой вызов уронит pop
    out = fill_diagram(client, library, _slide(content))
    assert out.content == content
    assert client.calls == []


def test_fill_slide_routes_diagram_and_maps_error_to_fill_error(library):
    """Единая точка входа: fill_slide ветвится на diagram-филлер, а его отказ
    переводит в FillError — fill_deck дальше сам деградирует слайд на blank."""
    bad = _reply(diagram={"kind": "cycle",
                          "nodes": [{"id": "a", "label": "Один"}]})
    client = FakeClient([bad, bad])
    with pytest.raises(filler.FillError) as err:
        filler.fill_slide(client, library, _slide(), deck_title="Дека")
    assert "slide 3" in str(err.value)

    ok = FakeClient([_reply()])
    out = filler.fill_slide(ok, library, _slide(), deck_title="Дека")
    assert out.content["title"] == "Как проходит заявка"


def test_system_prompt_menu_is_exactly_available_kinds():
    """Меню промпта собирается из каталога: только реализованные типы. Тип со
    снятым available обязан из меню исчезать — иначе модель выберет то, чего
    движок не нарисует."""
    s = diagram_filler.DIAGRAM_SYSTEM
    # только блок меню: ниже идёт контракт полей, там строки того же вида
    menu_block = s.split("Контракт diagram:")[0]
    assert tuple(re.findall(r"^- (\w+): ", menu_block, re.M)) == AVAILABLE_KINDS
    assert "- sankey:" not in s


def test_manual_offsets_survive_a_refill(library):
    """Ручные сдвиги узлов переживают перезаполнение схемы.

    Человек растащил узлы на слайде, потом нажал «Проверить и улучшить слайды»
    (или попросил правку в чате): autofix зовёт филлер заново, а модели запрещено
    присылать offsets — схема молча возвращалась к автораскладке, и вся ручная
    работа пропадала без единого слова.
    """
    old = {"kind": "flowchart",
           "nodes": [{"id": "a", "label": "Заявка", "shape": "start"},
                     {"id": "b", "label": "Готово", "shape": "end"}],
           "edges": [{"from": "a", "to": "b"}],
           "offsets": {"a": {"dx": 40, "dy": -20}, "gone": {"dx": 5, "dy": 5}}}
    content = {"title": "Схема", "subtitle": "",
               "diagram": json.dumps(old, ensure_ascii=False)}
    client = FakeClient([_reply()])
    out = fill_diagram(client, library, _slide(content), extra="замечания QA")
    spec = json.loads(out.content["diagram"])
    assert spec["offsets"] == {"a": {"dx": 40, "dy": -20}}  # «gone» отсеян: узла нет
    assert not library.validate_content("diagram", out.content)


def test_refill_without_previous_offsets_is_untouched(library):
    content = {"title": "Схема", "subtitle": "",
               "diagram": json.dumps({
                   "kind": "flowchart",
                   "nodes": [{"id": "a", "label": "Заявка", "shape": "start"},
                             {"id": "b", "label": "Готово", "shape": "end"}],
                   "edges": [{"from": "a", "to": "b"}]}, ensure_ascii=False)}
    client = FakeClient([_reply()])
    out = fill_diagram(client, library, _slide(content), extra="замечания QA")
    assert json.loads(out.content["diagram"]).get("offsets") in (None, {})
