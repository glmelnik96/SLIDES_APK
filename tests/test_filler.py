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
    """Filler must call the LLM with thinking disabled — reasoning adds 1-4 min per
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


def test_cancel_checkpoint_prevents_new_fills_after_stop():
    """Variant A (мгновенный стоп без лишних токенов): once a stop is requested,
    no NEW fill_slide (LLM) call may start. fill_deck must call check_cancel() at
    the top of EACH slide, BEFORE fill_slide, so slides that haven't begun bail at
    the checkpoint instead of spending tokens. In-flight calls still finish — but
    nothing new is launched."""
    library = TemplateLibrary.load()
    filled = []
    stop = {"on": False}

    def fake_fill(client, lib, slide, *, deck_title=""):
        filled.append(slide.index)
        stop["on"] = True          # a stop is requested right after slide 1 fills
        return slide.model_copy(update={"content": {"title": "ok"}})

    class _Stop(Exception):
        pass

    def check_cancel():
        if stop["on"]:
            raise _Stop()

    plan = DeckPlan(title="T", slides=[
        SlidePlan(index=i, type="content", template_id="statement")
        for i in range(1, 6)])      # 5 slides, 1 worker → strictly sequential

    orig = filler.fill_slide
    filler.fill_slide = fake_fill
    try:
        with pytest.raises(_Stop):
            filler.fill_deck(object(), library, plan, workers=1,
                             check_cancel=check_cancel)
    finally:
        filler.fill_slide = orig

    # Only slide 1 was actually filled; slides 2-5 hit the checkpoint and never
    # called fill_slide — no tokens spent after the stop.
    assert filled == [1]


def test_fallback_title_strips_image_markers():
    """Пометки парсера «[картинка: …]» не должны уезжать в заголовок заглушки.

    Прод-прогон «О Cloud.ru для МНС» (rest-слайды 6/7/12): бриф из pptx
    начинается картинкой, и blank-фолбэк получал заголовок вида
    «IT-Разработка [картинка: без подписи] softline 48…» — служебный маркер
    дословно в деке. Маркеры вырезаются, полезный текст остаётся."""
    title = filler._fallback_title(
        "IT-Разработка\n\n[картинка: без подписи]\n\nsoftline\n\n48")
    assert "[картинка" not in title
    assert title.startswith("IT-Разработка")
    # маркер с подписью тоже вырезается, а пустой бриф из одних маркеров
    # деградирует в нейтральный «Слайд»
    assert "[картинка" not in filler._fallback_title(
        "[картинка: схема архитектуры] Награды")
    assert filler._fallback_title("[картинка: без подписи]") == "Слайд"
