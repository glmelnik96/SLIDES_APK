"""Planner robustness: a flaky Kimi reply (no/empty JSON under reasoning runaway)
must not abort the whole build. plan_deck retries once with reasoning disabled.
Regression for the live failure `LLMFormatError: no JSON object after 2 retries`
that aborted a run (the user perceived it as a crash mid-navigation)."""
from htmlslides.library import TemplateLibrary
from htmlslides.models import DeckPlan, SlidePlan
from htmlslides.parsers.base import InputDoc, Section, TextBlock
from htmlslides.pipeline import planner
from htmlslides.pipeline.client import LLMFormatError


def _doc():
    return InputDoc(title="T", sections=[
        Section(heading="Раздел", level=2, blocks=[TextBlock(text="факт")])])


def _good_plan():
    return DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="title", template_id="cover"),
        SlidePlan(index=2, type="content", template_id="statement"),
        SlidePlan(index=3, type="contacts", template_id="contacts")])


def test_planner_default_keeps_reasoning():
    """Happy path: a valid plan on the first (reasoning ON) attempt — no fallback,
    no second call."""
    library = TemplateLibrary.load()
    calls = []

    class FakeClient:
        def chat_json(self, messages, model_cls, *, max_tokens=4096,
                      retries=2, extra_body=None):
            calls.append(extra_body)
            return _good_plan()

    plan = planner.plan_deck(FakeClient(), _doc(), library)
    assert len(plan.slides) == 3
    assert calls == [None]   # only the reasoning-ON attempt, no fallback


def test_planner_falls_back_to_no_think_on_format_error():
    library = TemplateLibrary.load()
    calls = []

    class FakeClient:
        def chat_json(self, messages, model_cls, *, max_tokens=4096,
                      retries=2, extra_body=None):
            calls.append(extra_body)
            if len(calls) == 1:               # reasoning-ON attempt flakes
                raise LLMFormatError("no JSON object")
            return _good_plan()               # no-think retry succeeds

    plan = planner.plan_deck(FakeClient(), _doc(), library)
    assert [s.index for s in plan.slides] == [1, 2, 3]
    assert calls[0] is None                                # first: reasoning ON
    assert calls[1] == {"thinking": {"type": "disabled"}}  # retry: reasoning OFF


def test_planner_propagates_when_both_attempts_fail():
    """If even the no-think retry can't produce JSON, fail loud (rare)."""
    import pytest
    library = TemplateLibrary.load()

    class FakeClient:
        def chat_json(self, messages, model_cls, *, max_tokens=4096,
                      retries=2, extra_body=None):
            raise LLMFormatError("no JSON object")

    with pytest.raises(LLMFormatError):
        planner.plan_deck(FakeClient(), _doc(), library)
