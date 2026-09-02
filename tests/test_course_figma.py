from htmlslides.assembler import assemble
from htmlslides.figma_spec import deck_to_figma_spec
from htmlslides.models import DeckPlan, SlidePlan


def test_course_templates_assemble():
    plan = DeckPlan(title="Курс", slides=[
        SlidePlan(index=1, type="title", template_id="course-toc", content={
            "title": "Содержание",
            "items": [{"label": "Введение"}, {"label": "Практика"}],
        }),
        SlidePlan(index=2, type="section", template_id="course-section", content={
            "label": "Практика",
            "number": "02",
            "sections": [
                {"label": "Раздел 1", "active": ""},
                {"label": "Раздел 2", "active": "1"},
            ],
        }),
        SlidePlan(index=3, type="content", template_id="course-quiz", content={
            "title": "Что такое облако?",
            "lead": "Выберите один ответ",
            "options": [
                {"text": "Чужой компьютер", "mark": "ok"},
                {"text": "Погода", "mark": "bad"},
            ],
        }),
    ])
    html = assemble(plan, theme="dark")
    assert 'data-template="course-toc"' in html
    assert 'data-template="course-section"' in html
    assert 'data-template="course-quiz"' in html
    assert "is-active" in html
    assert "cquiz-opt--ok" in html


def test_figma_spec_has_design_frames():
    plan = DeckPlan(title="Курс", slides=[
        SlidePlan(index=1, type="title", template_id="course-toc", content={
            "title": "Содержание",
            "items": [{"label": "Модуль"}],
        }),
    ])
    spec = deck_to_figma_spec(plan, theme="dark")
    assert spec["editorType"] == "design"
    assert spec["slides"][0]["width"] == 1920
    assert any(layer["name"] == "item-1" for layer in spec["slides"][0]["layers"])
