import json
import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from webapp import chat_agent, draft


class FakeClient:
    """Scripts model replies. chat_json is used by the intent classifier (returns
    Intent JSON); chat is used for template pick / planner-talk / slide fill."""
    def __init__(self, intent: dict, *, template="cards-6", text="ответ"):
        self._intent = intent
        self._template = template
        self._text = text

    def chat_json(self, messages, model_cls, **kw):
        # the classifier asks for Intent; the filler asks for SlideContent
        name = model_cls.__name__
        if name == "Intent":
            return model_cls.model_validate(self._intent)
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


def test_intent_add_picks_template_and_fills(monkeypatch, tmp_path):
    _seed(tmp_path, monkeypatch)
    c = FakeClient({"action": "add", "topic": "наши сервисы"}, template="cards-6")
    res = chat_agent.run_turn("s", "добавь слайд про наши сервисы", 1, client=c)
    plan = draft.load_plan("s")
    assert res.changed and len(plan.slides) == 1
    assert plan.slides[0].template_id == "cards-6"
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
