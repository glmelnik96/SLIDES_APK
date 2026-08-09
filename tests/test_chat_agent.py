import json
import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from webapp import chat_agent, draft


class FakeClient:
    """Scripts model replies. chat_json is used by the intent classifier (returns
    Intent JSON); chat is used for template pick / planner-talk / slide fill."""
    def __init__(self, intent: dict, *, template="cards-6", text="ответ",
                 proposed=None):
        self._intent = intent
        self._template = template
        self._text = text
        self._proposed = proposed  # dict for ProposedContent, or None

    def chat_json(self, messages, model_cls, **kw):
        # the classifier asks for Intent; the filler asks for SlideContent
        name = model_cls.__name__
        if name == "Intent":
            return model_cls.model_validate(self._intent)
        if name == "ProposedContent":
            return model_cls.model_validate(self._proposed or {"slides": []})
        # SlideContent for fill_slide → minimal valid-ish content
        return model_cls.model_validate({"content": {"title": "T",
                                                      "cards": [{"text": "x"}]}})

    def chat(self, messages, **kw):
        # template pick returns an id; planner/chat returns prose
        sys = messages[0]["content"] if messages else ""
        if "id макета" in sys:
            return self._template
        return self._text


def _seed(tmp_path, monkeypatch, slides=()):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    plan = draft.DraftPlan(title="Демо", slides=list(slides))
    draft.save_plan("s", plan)
    return plan


def test_intent_retitle(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    c = FakeClient({"action": "retitle", "topic": "Новый тайтл"})
    res = chat_agent.run_turn("s", "назови презентацию Новый тайтл", 1, client=c)
    assert res.changed and "Новый тайтл" in res.reply
    assert draft.load_plan("s").title == "Новый тайтл"


def test_intent_add_appends_light_outline_slide(monkeypatch, tmp_path):
    # add — лёгкий аутлайн: тема в brief, без синхронной сборки/шаблона.
    _seed(tmp_path, monkeypatch)
    c = FakeClient({"action": "add", "topic": "наши сервисы"}, template="cards-6")
    res = chat_agent.run_turn("s", "добавь слайд про наши сервисы", 1, client=c)
    plan = draft.load_plan("s")
    assert res.changed and len(plan.slides) == 1
    assert plan.slides[0].brief == "наши сервисы"
    assert plan.slides[0].filled is False
    assert plan.slides[0].template_id is None
    assert res.go_to == 1


def test_intent_delete(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch,
          slides=[draft.DraftSlide(template_id="cover"),
                  draft.DraftSlide(template_id="cards-6")])
    c = FakeClient({"action": "delete"})
    res = chat_agent.run_turn("s", "удали этот слайд", 2, client=c)
    assert res.changed and len(draft.load_plan("s").slides) == 1


def test_intent_move(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch,
          slides=[draft.DraftSlide(template_id="cover"),
                  draft.DraftSlide(template_id="cards-6")])
    c = FakeClient({"action": "move", "to": 1})
    res = chat_agent.run_turn("s", "перемести на первое место", 2, client=c)
    assert res.changed
    assert [s.template_id for s in draft.load_plan("s").slides] == ["cards-6", "cover"]


def test_rewrite_of_a_diagram_sends_the_current_schema(monkeypatch, tmp_path):
    """Схема правится в панели узлов (PUT /fields), и content слайда при этом
    остаётся с момента заполнения. В чат уезжал именно он — просьба «упрости»
    молча откатывала всё, что автор наменял руками."""
    from webapp import slide_types
    import htmlslides.pipeline.filler as filler
    fields = slide_types.validate_fields("diagram", {
        "heading": "Как проходит заявка", "subtitle": "",
        "diagram": {"kind": "process", "nodes": [
            {"id": "a", "label": "Заявка"}, {"id": "b", "label": "Запуск"}],
            "offsets": {"a": {"dx": 40, "dy": -20}}}})
    _seed(tmp_path, monkeypatch, slides=[draft.DraftSlide(
        template_id="diagram", slide_type="diagram", fields=fields,
        content={"title": "Старое", "subtitle": "",
                 "diagram": '{"kind":"process","nodes":['
                            '{"id":"z","label":"Позавчерашний узел"}]}'})])
    seen = {}

    def _spy(client, library, sp, *, deck_title="", extra=""):
        seen["content"] = dict(sp.content)
        seen["template_id"] = sp.template_id
        return sp.model_copy(update={"content": {
            "title": "Новое", "subtitle": "",
            "diagram": '{"kind":"process","nodes":['
                       '{"id":"a","label":"Заявка"}]}'}})
    monkeypatch.setattr(filler, "fill_slide", _spy)

    res = chat_agent.run_turn("s", "упрости схему", 1,
                              client=FakeClient({"action": "rewrite"}))
    assert res.changed
    assert seen["template_id"] == "diagram"
    assert "Позавчерашний узел" not in seen["content"]["diagram"]
    assert '"a"' in seen["content"]["diagram"]      # схема из панели, не из content
    assert seen["content"]["title"] == "Как проходит заявка"
    s = draft.load_plan("s").slides[0]
    assert s.fields["heading"] == "Новое"           # правка доехала до typed-полей
    assert s.fields["diagram"]["nodes"][0]["id"] == "a"


def test_intent_plan_is_conversational(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    c = FakeClient({"action": "plan"}, text="Предлагаю 5 слайдов: …")
    res = chat_agent.run_turn("s", "давай спланируем структуру", 1, client=c)
    assert not res.changed and "слайд" in res.reply.lower()
    # conversation history persisted
    convo = chat_agent.load_chat("s")
    assert convo.turns[-1].role == "assistant" and convo.turns[0].role == "user"


def test_classify_failure_degrades_to_chat(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)

    class Boom:
        def chat_json(self, *a, **k):
            raise RuntimeError("bad json")
        def chat(self, *a, **k):
            return "ладно"
    res = chat_agent.run_turn("s", "что-то непонятное", 1, client=Boom())
    assert not res.changed  # safe fallback, no crash


def test_context_brief_includes_slides_and_history(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch,
          slides=[draft.DraftSlide(template_id="cover", content={"title": "Привет"})])
    plan = draft.load_plan("s")
    convo = chat_agent.Conversation(turns=[chat_agent.Turn(role="user", text="hi")])
    brief = chat_agent.context_brief(plan, convo, 1)
    assert "cover" in brief and "Привет" in brief and "hi" in brief


def test_intent_propose_content_fills_typed_fields(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, slides=[
        draft.DraftSlide(brief="строение гриба", filled=False),
        draft.DraftSlide(brief="в цифрах", filled=False)])
    proposed = {"slides": [
        {"index": 1, "slide_type": "bullets",
         "fields": {"heading": "Строение", "bullets": ["шляпка", "ножка"]}},
        {"index": 2, "slide_type": "stats",
         "fields": {"heading": "Цифры", "stats": [{"value": "90%", "label": "лес"}]}},
    ]}
    c = FakeClient({"action": "propose_content"}, proposed=proposed)
    res = chat_agent.run_turn("s", "разложи слайды по полям", 1, client=c)
    plan = draft.load_plan("s")
    assert res.changed
    assert plan.slides[0].slide_type == "bullets"
    assert plan.slides[0].fields["bullets"] == ["шляпка", "ножка"]
    assert plan.slides[1].slide_type == "stats"


def test_propose_content_skips_invalid_item(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch, slides=[draft.DraftSlide(brief="x", filled=False)])
    # missing heading → invalid → slide stays raw
    proposed = {"slides": [{"index": 1, "slide_type": "bullets",
                            "fields": {"bullets": ["a"]}}]}
    c = FakeClient({"action": "propose_content"}, proposed=proposed)
    chat_agent.run_turn("s", "разложи", 1, client=c)
    assert draft.load_plan("s").slides[0].slide_type is None


def test_propose_content_no_targets(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)  # empty plan
    c = FakeClient({"action": "propose_content"}, proposed={"slides": []})
    res = chat_agent.run_turn("s", "разложи", 1, client=c)
    assert res.changed is False


def test_propose_content_thin_brief_suggests_enrich(monkeypatch, tmp_path):
    # A too-general one-line brief → model returns nothing → guide to enrich first.
    _seed(tmp_path, monkeypatch,
          slides=[draft.DraftSlide(brief="наши сервисы", filled=False)])
    c = FakeClient({"action": "propose_content"}, proposed={"slides": []})
    res = chat_agent.run_turn("s", "разложи", 1, client=c)
    assert res.changed is False
    assert "дополни" in res.reply.lower()


def test_build_outline_skips_typed_slides(monkeypatch, tmp_path):
    # a typed slide must NOT be sent through the LLM fill during build.
    _seed(tmp_path, monkeypatch, slides=[draft.DraftSlide(
        brief="строение", slide_type="bullets",
        fields={"heading": "Строение", "bullets": ["a", "b"]})])

    class Boom:
        def chat_json(self, *a, **k):
            raise AssertionError("classifier/fill must not run for typed slides")

        def chat(self, *a, **k):
            raise AssertionError("template pick must not run for typed slides")

    chat_agent.build_outline("s", client=Boom())
    plan = draft.load_plan("s")
    assert plan.slides[0].slide_type == "bullets"   # unchanged
    assert plan.slides[0].filled is False           # never LLM-filled
