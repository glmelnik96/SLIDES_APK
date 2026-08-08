"""«Стеклянная сборка»: скоринг кандидатов, пороги сомнения, степпер, ответы.

Fake-клиент вместо LLM (паттерн test_diagram_filler.py) и fake fill_slide
вместо заполнения: проверяем обвязку — фильтрацию кандидатов, фолбэк как
«место сомнения» (confidence 0.0), пропуск needs_input степпером, доводку
слайда по ответу и API-контракты /glass/*.
"""
import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

import pytest
from fastapi.testclient import TestClient

import webapp.app as appmod
import webapp.config as cfg
from htmlslides.library import SlotValidationError, TemplateLibrary
from htmlslides.parsers.base import ListBlock, Section, TextBlock
from webapp import draft, glass
from webapp.glass import Candidate, SectionChoice


def _client_app(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(cfg.settings, "db_url",
                        f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cfg.settings, "dev_user_id", "")
    return TestClient(appmod.app)


def H(uid="u1"):
    return {"X-User-Id": uid}


@pytest.fixture(scope="module")
def library():
    return TemplateLibrary.load()


class FakeClient:
    """Отдаёт заготовленные ответы по очереди; Exception в очереди — бросается."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat_json(self, messages, model_cls, *, max_tokens=4096,
                  retries=2, extra_body=None):
        self.calls.append({"messages": messages, "extra_body": extra_body})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _section(heading="Процесс", text="Заявка проходит согласование и запуск."):
    return Section(heading=heading, level=2, blocks=[TextBlock(text=text)])


def _fake_fill(client, library, sp, *, deck_title="", extra=""):
    return sp.model_copy(update={
        "content": {"title": f"Слайд {sp.index}", "brief": sp.content.get("brief", "")}})


# ── plan_section_candidates ──────────────────────────────────────────────────
def test_candidates_valid_reply_filtered_to_known(library):
    fake = FakeClient([SectionChoice(
        candidates=[Candidate(template_id="three-col", confidence=0.9),
                    Candidate(template_id="bogus", confidence=0.8),
                    Candidate(template_id="timeline", confidence=0.5)],
        question="  Что важнее?  ")])
    out = glass.plan_section_candidates(fake, library, _section())
    assert [c.template_id for c in out.candidates] == ["three-col", "timeline"]
    assert out.question == "Что важнее?"
    # скоринг — текст-в-JSON без reasoning, как слот-филл
    assert fake.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_candidates_failure_falls_back_with_zero_confidence(library):
    """Сбой сети/формата → эвристика planner с confidence 0.0 — честное
    «место сомнения», а не тихий уверенный выбор."""
    for fake in (FakeClient([RuntimeError("boom")]),
                 FakeClient([SectionChoice(          # все id чужие
                     candidates=[Candidate(template_id="ghost", confidence=1.0)])])):
        out = glass.plan_section_candidates(fake, library, _section())
        assert len(out.candidates) == 1
        assert out.candidates[0].confidence == 0.0
        assert out.candidates[0].template_id in {t.id for t in library.templates}


def test_candidates_menu_excludes_system_templates(library):
    """Обложки/контакты/дивайдеры ставит система — в меню раздела их нет."""
    menu = glass._menu(library)
    for tid in ("cover", "contacts", "back-cover", "blank"):
        assert f"- {tid} " not in menu
    assert "- three-col " in menu and "- diagram " in menu


def test_doubtful_thresholds():
    ok = lambda *pairs: SectionChoice(candidates=[
        Candidate(template_id="x", confidence=c) for c in pairs])
    assert glass._doubtful(ok(0.9)) is False
    assert glass._doubtful(ok(0.5)) is True              # ниже пола 0.6
    assert glass._doubtful(ok(0.8, 0.7)) is True         # разрыв < 0.15
    assert glass._doubtful(ok(0.9, 0.5)) is False
    assert glass._doubtful(SectionChoice()) is True      # пусто = сомнение


# ── start_glass ──────────────────────────────────────────────────────────────
def test_start_glass_builds_outline_without_filling(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    src = tmp_path / "doc.md"
    src.write_text("# Отчёт\n\n## Уверенный раздел\n\nТекст про метрики.\n\n"
                   "## Спорный раздел\n\nМало контента.\n", encoding="utf-8")
    fake = FakeClient([
        SectionChoice(candidates=[Candidate(template_id="stats-row",
                                            confidence=0.9)]),
        SectionChoice(candidates=[Candidate(template_id="three-col",
                                            confidence=0.4),
                                  Candidate(template_id="timeline",
                                            confidence=0.35)],
                      question="Что показать?"),
    ])
    plan = glass.start_glass("s1", src, client=fake, workers=1)

    assert plan.title == "Отчёт"
    cover = plan.slides[0]
    assert cover.template_id == "cover" and cover.filled
    sure = plan.slides[1]
    assert sure.template_id == "stats-row" and not sure.filled
    assert sure.status is None and sure.question is None
    assert sure.brief and sure.candidates == ["stats-row"]
    doubt = plan.slides[2]
    assert doubt.status == "needs_input"
    assert doubt.question == "Что показать?"
    assert doubt.candidates == ["three-col", "timeline"]
    # DeckPlan-as-truth: план сохранён, дека отрисована из него
    assert draft.load_plan("s1").model_dump() == plan.model_dump()
    from webapp.paths import session_dir
    assert (session_dir("s1") / "deck.html").is_file()


def test_start_glass_default_question_on_silent_doubt(monkeypatch, tmp_path):
    """Сомнение без вопроса от модели (фолбэк) → дефолтный вопрос, не пустота."""
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    src = tmp_path / "doc.md"
    src.write_text("# Т\n\n## Раздел\n\nТекст.\n", encoding="utf-8")
    plan = glass.start_glass("s2", src, client=FakeClient([RuntimeError("boom")]),
                             workers=1)
    s = plan.slides[1]
    assert s.status == "needs_input"
    assert s.question == glass._DEFAULT_QUESTION


# ── step_fill ────────────────────────────────────────────────────────────────
def _outline_plan():
    return draft.DraftPlan(title="Т", slides=[
        draft.DraftSlide(template_id="cover", content={"title": "Т"}, filled=True),
        draft.DraftSlide(template_id="three-col", brief="спорный",
                         status="needs_input", question="Какой макет?",
                         candidates=["three-col", "timeline"]),
        draft.DraftSlide(template_id="stats-row", brief="метрики"),
        draft.DraftSlide(template_id="timeline", brief="этапы"),
    ])


def test_step_fill_skips_needs_input_and_finishes(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    import htmlslides.pipeline.filler as filler
    monkeypatch.setattr(filler, "fill_slide", _fake_fill)
    draft.save_plan("s3", _outline_plan())

    out1 = glass.step_fill("s3", client=FakeClient([]))
    assert out1["index"] == 3 and out1["done"] is False   # needs_input пропущен
    assert out1["open_questions"] == [2]
    out2 = glass.step_fill("s3", client=FakeClient([]))
    assert out2["index"] == 4 and out2["done"] is True
    plan = draft.load_plan("s3")
    assert plan.slides[2].filled and plan.slides[2].content["title"] == "Слайд 3"
    assert plan.slides[1].filled is False                 # вопрос всё ещё ждёт
    # шаги кончились, но вопрос остался открыт
    out3 = glass.step_fill("s3", client=FakeClient([]))
    assert out3["done"] is True and out3["index"] is None
    assert out3["open_questions"] == [2]


def test_step_fill_survives_fill_failure(monkeypatch, tmp_path):
    """Осечка модели не роняет шаг: слайд остаётся аутлайном с макетом."""
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    import htmlslides.pipeline.filler as filler

    def _boom(client, library, sp, **kw):
        raise filler.FillError("slide 3: сеть упала")
    monkeypatch.setattr(filler, "fill_slide", _boom)
    draft.save_plan("s4", _outline_plan())

    out = glass.step_fill("s4", client=FakeClient([]))
    assert out["index"] == 3
    s = draft.load_plan("s4").slides[2]
    assert s.filled and s.template_id == "stats-row" and s.content == {}


# ── answer ───────────────────────────────────────────────────────────────────
def test_answer_chip_and_message_refill(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    import htmlslides.pipeline.filler as filler
    seen = {}

    def _spy(client, library, sp, **kw):
        seen["brief"] = sp.content.get("brief", "")
        seen["template_id"] = sp.template_id
        return _fake_fill(client, library, sp, **kw)
    monkeypatch.setattr(filler, "fill_slide", _spy)
    draft.save_plan("s5", _outline_plan())

    out = glass.answer("s5", 2, template_id="timeline",
                       message="покажи даты", client=FakeClient([]))
    assert out["open_questions"] == []
    s = draft.load_plan("s5").slides[1]
    assert s.template_id == "timeline" and s.filled
    assert s.status is None and s.question is None
    assert "Уточнение автора: покажи даты" in seen["brief"]
    assert seen["template_id"] == "timeline"


def test_answer_rejects_bad_index_and_unknown_template(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    draft.save_plan("s6", _outline_plan())
    with pytest.raises(IndexError):
        glass.answer("s6", 99, client=FakeClient([]))
    with pytest.raises(SlotValidationError):
        glass.answer("s6", 2, template_id="ghost", client=FakeClient([]))
    # план не испорчен отказом
    assert draft.load_plan("s6").slides[1].status == "needs_input"


def test_old_plan_json_valid_without_glass_fields():
    """Старые plan.json без status/question/candidates валидны без миграции."""
    plan = draft.DraftPlan.model_validate_json(
        '{"title":"Т","slides":[{"template_id":"cover","content":{}}]}')
    s = plan.slides[0]
    assert s.status is None and s.question is None and s.candidates is None


# ── endpoints /glass/* ───────────────────────────────────────────────────────
def _new_draft(c):
    return c.post("/api/drafts", json={"mode": "manual"},
                  headers=H()).json()["session_id"]


def test_glass_start_endpoint(monkeypatch, tmp_path):
    with _client_app(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        seen = {}

        def _fake_start(session_id, source):
            seen["args"] = (session_id, source.name, source.read_bytes())
            plan = draft.DraftPlan(title="Т", slides=[
                draft.DraftSlide(template_id="cover", filled=True)])
            draft.save_plan(session_id, plan)   # как настоящий start_glass
            return plan
        monkeypatch.setattr(glass, "start_glass", _fake_start)

        r = c.post(f"/api/drafts/{sid}/glass/start", headers=H(),
                   files={"file": ("doc.md", "# Т\n\nтекст".encode(), "text/markdown")})
        assert r.status_code == 200
        assert r.json()["title"] == "Т"
        assert seen["args"][0] == sid and seen["args"][1] == "input.md"

        # непустой черновик → 409 (стекло стартует с чистой сессии)
        assert c.post(f"/api/drafts/{sid}/glass/start", headers=H(),
                      files={"file": ("doc.md", b"# T", "text/markdown")}
                      ).status_code == 409


def test_glass_start_validation(monkeypatch, tmp_path):
    with _client_app(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        assert c.post(f"/api/drafts/{sid}/glass/start", headers=H(),
                      files={"file": ("doc.exe", b"x", "application/x-msdownload")}
                      ).status_code == 400
        assert c.post(f"/api/drafts/{sid}/glass/start", headers=H(),
                      files={"file": ("doc.md", b"   \n", "text/markdown")}
                      ).status_code == 400
        # сбой parse → 500 с человеческой причиной, не голый traceback
        def _boom(session_id, source):
            raise ValueError("bad doc")
        monkeypatch.setattr(glass, "start_glass", _boom)
        r = c.post(f"/api/drafts/{sid}/glass/start", headers=H(),
                   files={"file": ("doc.md", b"# T\n\ntext", "text/markdown")})
        assert r.status_code == 500
        assert "не удалось разобрать" in r.json()["detail"]
        # чужая сессия → 404 (изоляция владения)
        assert c.post(f"/api/drafts/{sid}/glass/start", headers=H("intruder"),
                      files={"file": ("doc.md", b"# T\n\ntext", "text/markdown")}
                      ).status_code == 404


def test_glass_step_and_answer_endpoints(monkeypatch, tmp_path):
    with _client_app(monkeypatch, tmp_path) as c:
        sid = _new_draft(c)
        monkeypatch.setattr(glass, "step_fill", lambda session_id: {
            "done": True, "index": None, "open_questions": [], "plan": {}})
        r = c.post(f"/api/drafts/{sid}/glass/step", headers=H())
        assert r.status_code == 200 and r.json()["done"] is True

        seen = {}

        def _fake_answer(session_id, index, *, template_id=None, message=""):
            if index < 1 or index == 99:      # как настоящий answer
                raise IndexError(f"slide {index} out of range")
            if template_id == "ghost":
                raise SlotValidationError("unknown template: ghost")
            seen["call"] = (session_id, index, template_id, message)
            return {"index": index, "open_questions": [], "plan": {}}
        monkeypatch.setattr(glass, "answer", _fake_answer)

        r = c.post(f"/api/drafts/{sid}/glass/answer", headers=H(),
                   json={"index": 2, "template_id": "timeline",
                         "message": "покажи даты"})
        assert r.status_code == 200
        assert seen["call"] == (sid, 2, "timeline", "покажи даты")
        assert c.post(f"/api/drafts/{sid}/glass/answer", headers=H(),
                      json={"message": "без индекса"}).status_code == 400
        assert c.post(f"/api/drafts/{sid}/glass/answer", headers=H(),
                      json={"index": 99}).status_code == 400
        assert c.post(f"/api/drafts/{sid}/glass/answer", headers=H(),
                      json={"index": 2, "template_id": "ghost"}).status_code == 400
