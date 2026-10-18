"""Модель без зрения: картинки ей не отправляются, а не роняют сборку.

С 2026-10-16 проекту Cloud.ru закрыт вызов MiniMax-M3 и Kimi-K2.6 (403), и прод
временно собирает на MiniMax-M2.5. Она, как и GLM-4.7, принимает только текст: на
картинку шлюз отвечает 400 «… is not a multimodal model». Проверка слайда по
скриншоту ловила только осечки формата и транзиентные сбои — 400 ронял «Улучшить
слайд» и сборку через API; планировщик pptx-ребрендинга падал на первом же разделе.

Защита в два слоя: известная текстовая модель картинок не получает вовсе (не
тратим LibreOffice и скриншоты впустую), а отказ шлюза от картинки у модели, про
которую мы не знали, деградирует так же, как недоступное «зрение».
"""
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError, BadRequestError

from htmlslides.library import TemplateLibrary
from htmlslides.models import DeckPlan, SlidePlan
from htmlslides.parsers.base import InputDoc, Section, TextBlock
from htmlslides.pipeline import build, planner
from htmlslides.pipeline.client import KimiClient, is_not_multimodal
from htmlslides.pipeline.linter import LintIssue
from htmlslides.pipeline.planner import _SectionPlan, _SectionSlide

REQ = httpx.Request("POST", "http://x")
TEXT_ONLY = "MiniMaxAI/MiniMax-M2.5"
SEES = "MiniMaxAI/MiniMax-M3"


def _refusal(model=TEXT_ONLY):
    """Ровно то, что шлюз Cloud.ru вернул 2026-10-18 на картинку для M2.5."""
    body = {"message": f"{model} is not a multimodal model",
            "type": "BadRequestError", "param": None, "code": 400}
    return BadRequestError(f"Error code: 400 - {body}",
                           response=httpx.Response(400, request=REQ, json=body),
                           body=body)


def _boom(*_a, **_kw):
    raise AssertionError("модели без зрения картинки не готовим и не шлём")


# ── способность модели ──────────────────────────────────────────────────────
@pytest.mark.parametrize("model", [TEXT_ONLY, "zai-org/GLM-4.7"])
def test_text_only_model_does_not_see_images(model):
    assert KimiClient(transport=object(), model=model).sees_images is False


def test_multimodal_model_sees_images():
    assert KimiClient(transport=object(), model=SEES).sees_images is True


class _Transport:
    """Дублёр openai-клиента: модели из `dead` отвечают таймаутом."""

    def __init__(self, dead=()):
        self.dead = set(dead)

    def with_options(self, **_kw):
        return self

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, *, model, **_kw):
        if model in self.dead:
            raise APITimeoutError(request=REQ)
        choice = SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")
        return SimpleNamespace(choices=[choice], usage=None)


def test_capability_follows_the_switch_to_fallback():
    """Способность — свойство ТЕКУЩЕЙ модели: после переезда на текстовый резерв
    картинки слать уже нельзя, хотя основная модель их принимала."""
    c = KimiClient(transport=_Transport(dead={SEES}), model=SEES,
                   fallback_model="zai-org/GLM-4.7")
    assert c.sees_images is True
    c.preflight()
    assert c.model == "zai-org/GLM-4.7" and c.sees_images is False


def test_gateway_refusal_is_recognized():
    assert is_not_multimodal(_refusal())
    other = {"message": "context length exceeded", "code": 400}
    assert not is_not_multimodal(BadRequestError(
        f"Error code: 400 - {other}", response=httpx.Response(400, request=REQ, json=other),
        body=other))
    assert not is_not_multimodal(ValueError("not a multimodal model"))


# ── проверка слайда по скриншоту ────────────────────────────────────────────
def _plan():
    return DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="content", template_id="kpi", content={})])


_HTML = '<section class="slide" data-template="kpi"><p>текст</p></section>'


def test_slide_review_is_skipped_when_model_cannot_see(monkeypatch):
    """«Улучшить слайд» и хвост сборки: замечания линтера остаются, скриншоты и
    вызов модели с картинкой — нет."""
    from htmlslides.pipeline import screenshot, vision_qa
    monkeypatch.setattr(build, "lint_html", lambda html: [LintIssue(1, "overflow", "вылез")])
    monkeypatch.setattr(screenshot, "screenshot_slides", _boom)
    monkeypatch.setattr(vision_qa, "review_slide", _boom)
    said = []

    notes = build._qa_notes(_plan(), _HTML, vision=True,
                            client=KimiClient(transport=object(), model=TEXT_ONLY),
                            artifacts=None, progress=said.append, measure=False)

    assert notes == {1: ["overflow: вылез"]}
    assert any("картинк" in m for m in said)


def test_slide_review_survives_gateway_refusal(monkeypatch, tmp_path):
    """Модель, про которую мы не знали: шлюз отказался от картинки — слайд остаётся
    без ревью, а не роняет улучшение или сборку."""
    from htmlslides.pipeline import screenshot, vision_qa
    shot = tmp_path / "s1.png"
    shot.write_bytes(b"png")
    monkeypatch.setattr(build, "lint_html", lambda html: [LintIssue(1, "overflow", "вылез")])
    monkeypatch.setattr(screenshot, "screenshot_slides", lambda *a, **k: {1: shot})

    def _refuse(*_a, **_kw):
        raise _refusal("some/new-model")
    monkeypatch.setattr(vision_qa, "review_slide", _refuse)
    said = []

    notes = build._qa_notes(_plan(), _HTML, vision=True,
                            client=KimiClient(transport=object(), model="some/new-model"),
                            artifacts=None, progress=said.append, measure=False)

    assert notes == {1: ["overflow: вылез"]}
    assert any("картинк" in m for m in said)


# ── сборка: рендер исходных слайдов ─────────────────────────────────────────
def test_source_slides_are_not_rendered_for_text_only_model(monkeypatch, tmp_path):
    """LibreOffice на деку стоит десятки секунд — платить их за картинки, которые
    модель не примет, незачем."""
    monkeypatch.setattr(build, "_render_source_slides", _boom)
    said = []
    images = build._source_images(tmp_path / "deck.pptx", "rebrand",
                                  KimiClient(transport=object(), model=TEXT_ONLY),
                                  None, said.append)
    assert images == []
    assert any("картинк" in m for m in said)


def test_source_slides_render_as_before_for_model_that_sees(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(build, "_render_source_slides",
                        lambda src, mode, artifacts, progress: seen.append(mode) or ["p"])
    images = build._source_images(tmp_path / "deck.pptx", "rebrand",
                                  KimiClient(transport=object(), model=SEES),
                                  None, lambda m: None)
    assert images == ["p"] and seen == ["rebrand"]


# ── планировщик pptx-ребрендинга ────────────────────────────────────────────
class _Planner:
    """Отвечает планом раздела; помнит сообщения; может первым ответом отказать."""

    def __init__(self, sees_images, first=None):
        self.sees_images = sees_images
        self.first = first
        self.seen = []

    def chat_json(self, messages, model_cls, *, max_tokens=4096, retries=2,
                  extra_body=None):
        self.seen.append(messages)
        if self.first is not None:
            exc, self.first = self.first, None
            raise exc
        return _SectionPlan(slides=[_SectionSlide(template_id="statement", brief="тезис")])


def _sends_image(messages) -> bool:
    content = messages[-1]["content"]
    return isinstance(content, list) and any(p.get("type") == "image_url" for p in content)


def _doc():
    return InputDoc(title="Дека", sections=[
        Section(heading="Раздел", level=2, blocks=[TextBlock(text="Факты раздела.")])])


def test_planner_plans_by_text_when_model_cannot_see(tmp_path):
    png = tmp_path / "s1.png"
    png.write_bytes(b"png")
    client = _Planner(sees_images=False)

    plan = planner.plan_deck(client, _doc(), TemplateLibrary.load(), slide_images=[png])

    assert len(plan.slides) >= 2                       # обложка + раздел
    assert client.seen and not any(_sends_image(m) for m in client.seen)


def test_planner_retries_by_text_when_gateway_refuses_image(tmp_path):
    png = tmp_path / "s1.png"
    png.write_bytes(b"png")
    client = _Planner(sees_images=True, first=_refusal("some/new-model"))

    plan = planner.plan_deck(client, _doc(), TemplateLibrary.load(), slide_images=[png])

    assert [s.template_id for s in plan.slides][1] == "statement"   # план модели, не эвристика
    assert _sends_image(client.seen[0]) and not _sends_image(client.seen[1])


def test_planner_monolith_falls_back_to_text_when_gateway_refuses(tmp_path):
    """Монолитный vision-вызов (скриншотов не столько, сколько разделов) у модели,
    про которую мы не знали: отказ шлюза — план по тексту, а не падение сборки."""
    pngs = [tmp_path / "s1.png", tmp_path / "s2.png"]
    for p in pngs:
        p.write_bytes(b"png")
    client = _Planner(sees_images=True, first=_refusal("some/new-model"))

    plan = planner.plan_deck(client, _doc(), TemplateLibrary.load(), slide_images=pngs)

    assert [s.template_id for s in plan.slides][1] == "statement"
    assert _sends_image(client.seen[0]) and not any(_sends_image(m) for m in client.seen[1:])


# ── стеклянная сборка: «слепой» раздел pptx ─────────────────────────────────
def test_glass_does_not_render_blind_section_for_text_only_model(monkeypatch, tmp_path):
    """Раздел pptx, где весь смысл в картинке, модель без зрения не прочитает —
    раздел остаётся вопросом автору, без рендера исходника."""
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    from htmlslides.parsers import render
    from webapp import glass
    from webapp.paths import session_dir
    src = session_dir("g1") / "input.pptx"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"pptx")
    # Счётчик, а не исключение: _read_source_slide глотает любой сбой рендера,
    # и падающий дублёр прошёл бы этот тест и без правки.
    renders = []
    monkeypatch.setattr(render, "render_pptx_pngs",
                        lambda source, out_dir: renders.append(source) or [])

    assert glass._read_source_slide(
        "g1", 0, KimiClient(transport=object(), model=TEXT_ONLY)) == ""
    assert renders == []
