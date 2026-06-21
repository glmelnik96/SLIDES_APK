"""fill_deck resilience: a transient API error on one slide must degrade that
slide to a blank fallback, not abort the whole deck (regression for live repro
where one rate-limited slide crashed the entire build before assembly)."""
import httpx
import pytest
from openai import APIConnectionError

from htmlslides.library import TemplateLibrary
from htmlslides.models import DeckPlan, SlidePlan
from htmlslides.pipeline import filler


def _plan():
    return DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="content", template_id="statement"),
        SlidePlan(index=2, type="content", template_id="statement"),
    ])


def test_transient_api_error_degrades_one_slide_not_whole_deck():
    library = TemplateLibrary.load()
    calls = {"n": 0}

    def fake_fill(client, lib, slide, *, deck_title=""):
        calls["n"] += 1
        if slide.index == 1:
            raise APIConnectionError(request=httpx.Request("POST", "http://x"))
        return slide.model_copy(update={"content": {"title": "ok"}})

    # Patch fill_slide so we exercise fill_deck's per-slide error handling.
    orig = filler.fill_slide
    filler.fill_slide = fake_fill
    try:
        out = filler.fill_deck(object(), library, _plan(), workers=1)
    finally:
        filler.fill_slide = orig

    assert len(out.slides) == 2
    by_index = {s.index: s for s in out.slides}
    # Slide 1 degraded to blank; slide 2 filled normally — deck NOT aborted.
    assert by_index[1].template_id == "blank"
    assert by_index[2].content == {"title": "ok"}


def test_template_fill_disables_reasoning_for_speed():
    """Filler must call Kimi with thinking disabled — reasoning adds 1-4 min per
    slide and isn't needed for contract-bound text. Regression for build speed."""
    library = TemplateLibrary.load()
    seen = {}

    class FakeClient:
        def chat_json(self, messages, model_cls, *, max_tokens=4096,
                      retries=2, extra_body=None):
            seen["extra_body"] = extra_body
            return filler.SlideContent(content={"title": "ok"})

    slide = SlidePlan(index=1, type="content", template_id="statement")
    filler._fill_template(FakeClient(), library, slide, deck_title="D", extra="")
    assert seen["extra_body"] == {"thinking": {"type": "disabled"}}


def test_unknown_exception_still_aborts_deck():
    """A non-transient, unexpected error must still abort (fail loud, not blank)."""
    library = TemplateLibrary.load()

    def boom(client, lib, slide, *, deck_title=""):
        raise RuntimeError("unexpected")

    orig = filler.fill_slide
    filler.fill_slide = boom
    try:
        with pytest.raises(RuntimeError):
            filler.fill_deck(object(), library, _plan(), workers=1)
    finally:
        filler.fill_slide = orig
