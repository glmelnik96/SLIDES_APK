"""Каждый мастер должен помечать верхнеуровневые слоты атрибутом data-slot
(хук подсветки редактируемого поля) и рендериться без ошибок слот-контракта."""
import json
from importlib import resources

from htmlslides.assembler import assemble
from htmlslides.models import DeckPlan, SlidePlan


def _library():
    node = resources.files("htmlslides") / "templates" / "library.json"
    return json.loads(node.read_text("utf-8"))


def _sample_content(slots):
    """Минимальный валидный контент: каждый текст — 'x', каждый список — один пункт
    со всеми под-слотами '1'. Хватает, чтобы отрисовались все опциональные слоты."""
    content = {}
    for name, spec in slots.items():
        if spec["kind"] == "text":
            content[name] = "x"
        elif spec["kind"] == "list":
            content[name] = [{sub: "1" for sub in spec.get("item_slots", {})}]
    return content


def test_every_master_marks_top_level_slots():
    for tpl in _library()["templates"]:
        slots = tpl["slots"]
        content = _sample_content(slots)
        plan = DeckPlan(title="t", slides=[SlidePlan(
            index=1, type=tpl["type"], template_id=tpl["id"], content=content)])
        html = assemble(plan, theme="dark")  # также проверяет, что рендер не падает
        for slot in slots:
            assert f'data-slot="{slot}"' in html, (
                f'{tpl["id"]}: нет data-slot="{slot}"')
