"""Шаблон «Схема» (diagram): сборка деки, экранированный JSON, инлайн движка."""
import html
import json

import pytest

from htmlslides.assembler import AssembleError, assemble
from htmlslides.diagrams import sample_spec, spec_json
from htmlslides.library import TemplateLibrary
from htmlslides.models import DeckPlan, SlidePlan


def _plan(diagram_json: str) -> DeckPlan:
    return DeckPlan(title="Тест", slides=[SlidePlan(
        index=1, type="content", template_id="diagram",
        content={"title": "Процесс обработки", "subtitle": "От заявки до продажи",
                 "diagram": diagram_json})])


def test_library_has_diagram_template():
    spec = TemplateLibrary.load().get("diagram")
    assert spec.display_name == "Схема"
    assert spec.slots["title"].required
    assert spec.slots["diagram"].required
    assert spec.slots["diagram"].max_chars == 8000


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_assemble_diagram_slide(theme):
    page = assemble(_plan(spec_json(sample_spec("flowchart"))), theme=theme)
    assert 'data-template="diagram"' in page
    assert 'class="diagram-host"' in page
    assert "DiagramEngine" in page          # движок заинлайнен вторым скриптом
    assert "Процесс обработки" in page


def test_data_diagram_json_roundtrips_through_escaping():
    """Autoescape Jinja экранирует кавычки JSON в сущности; браузер при чтении
    атрибута декодирует их обратно — эмулируем это html.unescape."""
    raw = sample_spec("flowchart")
    page = assemble(_plan(spec_json(raw)))
    start = page.index('data-diagram="') + len('data-diagram="')
    frag = page[start:page.index('"', start)]
    assert '"' not in frag                  # сырых кавычек в атрибуте нет
    decoded = json.loads(html.unescape(frag))
    assert decoded["kind"] == "flowchart"
    assert [n["id"] for n in decoded["nodes"]] == \
        [n["id"] for n in raw["nodes"]]


def test_data_diagram_is_not_touched_by_typography():
    """JSON диаграммы — машинные данные, а не проза: типографика _glue_nbsp здесь
    ПОРТИТ payload. «Задание на комплектацию» превращалось в «Задание на\u00a0…»,
    браузер такое слово (15 символов) разорвать не мог, движок же считал ширину
    по /\\s+/ (а \\s в JS ловит и NBSP) и видел два коротких слова — выбирал
    крупный кегль, обрезку не применял, и подпись молча срезалась overflow:hidden
    до «Задание на комплектацис». Плюс редактор читает атрибут обратно и сохраняет
    NBSP в план — порча расползается."""
    raw = {"version": 1, "kind": "flowchart", "direction": "right",
           "nodes": [{"id": "n1", "label": "Задание на комплектацию",
                      "shape": "process"},
                     {"id": "n2", "label": "Отгрузка за 180 млн", "shape": "end"}],
           "edges": [{"from": "n1", "to": "n2"}]}
    page = assemble(_plan(spec_json(raw)))
    start = page.index('data-diagram="') + len('data-diagram="')
    decoded = json.loads(html.unescape(page[start:page.index('"', start)]))
    assert [n["label"] for n in decoded["nodes"]] == \
        [n["label"] for n in raw["nodes"]]
    assert "\u00a0" not in json.dumps(decoded, ensure_ascii=False)
    # проза слайда типографику по-прежнему получает
    assert "От\u00a0заявки" in page


def test_missing_diagram_slot_fails_contract():
    plan = DeckPlan(title="Т", slides=[SlidePlan(
        index=1, type="content", template_id="diagram",
        content={"title": "Без данных"})])
    with pytest.raises(AssembleError):
        assemble(plan)


def test_sample_content_renders_preview():
    """Превью мастера «Схема» в пикере — реальная блок-схема, не заглушка."""
    from webapp.templates_api import sample_content
    content = sample_content("diagram")
    spec = json.loads(content["diagram"])
    assert spec["kind"] == "flowchart" and len(spec["nodes"]) >= 4
    plan = DeckPlan(title="Т", slides=[SlidePlan(
        index=1, type="content", template_id="diagram", content=content)])
    assert "diagram-host" in assemble(plan)
