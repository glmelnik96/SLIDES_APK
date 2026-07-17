"""Фиксы качества ИИ-харнеса (аудит 2026-07-17).

Покрывает:
- client.chat_ex: обрыв по длине виден (finish_reason) и лечится эскалацией бюджета;
- client.chat_json: остаточный обрыв — явная ошибка, а не бесполезный ретрай;
- filler freeform: обрезанный/прозаический ответ не уезжает в деку как «HTML»;
- chat_edit: ответ с несколькими <section> (вся дека) не подменяет слайд первым;
- chat_agent.classify: rule-based фолбэк не теряет явные команды при сбое LLM;
- chat_agent: cap на размер аутлайна.
"""
import os

import pytest
from pydantic import BaseModel

os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

import htmlslides.pipeline.client as clientmod
from htmlslides.models import SlidePlan
from htmlslides.pipeline import filler
from webapp import chat_agent, chat_edit, draft


# ── helpers ──────────────────────────────────────────────────────────────────
def _resp(content, finish):
    class _M:
        pass
    _M.content = content

    class _C:
        pass
    _C.message = _M
    _C.finish_reason = finish

    class _R:
        pass
    _R.choices = [_C]
    return _R


def _transport(responses):
    """Transport-мок: очередь ответов (content, finish_reason) + лог max_tokens."""
    calls = []
    seq = iter(responses)

    class _T:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    calls.append(kw["max_tokens"])
                    content, finish = next(seq)
                    return _resp(content, finish)
    return _T(), calls


# ── client: видимость и эскалация обрыва ─────────────────────────────────────
def test_chat_escalates_budget_on_length_finish():
    t, calls = _transport([("полов", "length"), ("целый ответ", "stop")])
    c = clientmod.KimiClient(rps=1000, transport=t)
    assert c.chat([{"role": "user", "content": "x"}]) == "целый ответ"
    assert calls == [4096, 8192]  # бюджет удвоен после обрыва


def test_chat_ex_reports_residual_truncation():
    t, calls = _transport([("a", "length")] * 3)
    c = clientmod.KimiClient(rps=1000, transport=t)
    text, finish = c.chat_ex([{"role": "user", "content": "x"}])
    assert finish == "length"           # обрыв ВИДЕН вызывающему коду
    assert calls == [4096, 8192, 16384]  # 2 эскалации, не бесконечный цикл


def test_chat_respects_token_cap():
    t, calls = _transport([("a", "length")] * 3)
    c = clientmod.KimiClient(rps=1000, transport=t)
    c.chat([{"role": "user", "content": "x"}], max_tokens=20000)
    assert calls == [20000, 32768]  # второй вызов упёрся в потолок → стоп


def test_chat_json_raises_on_truncated_reply():
    class _Out(BaseModel):
        a: int

    t, _ = _transport([('{"a": 1', "length")] * 9)
    c = clientmod.KimiClient(rps=1000, transport=t)
    with pytest.raises(clientmod.LLMFormatError, match="обрезан"):
        c.chat_json([{"role": "user", "content": "x"}], _Out)


def test_chat_without_finish_reason_mock_still_works():
    """Старые моки без finish_reason (getattr → None) не должны эскалировать."""
    t, calls = _transport([("ok", None)])
    c = clientmod.KimiClient(rps=1000, transport=t)
    assert c.chat([{"role": "user", "content": "x"}]) == "ok"
    assert calls == [4096]


# ── filler: freeform не принимает обрезки/прозу ──────────────────────────────
class _ChatSeq:
    def __init__(self, replies):
        self._seq = iter(replies)
        self.n = 0

    def chat(self, messages, *, max_tokens=4096, extra_body=None):
        self.n += 1
        return next(self._seq)


def _ff_slide():
    return SlidePlan(index=1, type="content", template_id="freeform",
                     freeform=True, content={"brief": "тема"})


_VALID_FF = ('```html\n<section class="slide" data-template="freeform">'
             '<div class="content-head"><h3 class="content-head-title t-head-42">'
             'X</h3></div></section>\n```')


def test_freeform_truncated_reply_retried_then_ok():
    truncated = '```html\n<section class="slide" data-template="freeform"><div'
    c = _ChatSeq([truncated, _VALID_FF])
    out = filler._fill_freeform(c, _ff_slide(), deck_title="D", extra="")
    assert "</section>" in out.content["html"]
    assert c.n == 2


def test_freeform_prose_reply_fails_loud_not_silent():
    c = _ChatSeq(["Вот описание слайда...", "и снова проза без HTML"])
    with pytest.raises(filler.FillError, match="freeform невалиден"):
        filler._fill_freeform(c, _ff_slide(), deck_title="D", extra="")


# ── chat_edit: ровно один <section> ──────────────────────────────────────────
_DECK = ('<div class="deck-stage">'
         '<section class="slide">ONE</section>'
         '<section class="slide">TWO</section>'
         '</div>')


class _EditSeq:
    def __init__(self, replies):
        self._seq = iter(replies)
        self.n = 0

    def chat(self, messages, max_tokens=4096, temperature=0.3):
        self.n += 1
        return next(self._seq)


def test_rewrite_retries_when_model_returns_whole_deck():
    whole_deck = ('<section class="slide">ONE-EDITED</section>'
                  '<section class="slide">TWO</section>')
    c = _EditSeq([whole_deck, '<section class="slide">FIXED</section>'])
    out = chat_edit.rewrite_slide(_DECK, 2, "поправь", client=c)
    assert "FIXED" in out and "ONE</section>" in out
    assert c.n == 2


def test_rewrite_fails_if_whole_deck_persists():
    whole_deck = ('<section class="slide">A</section>'
                  '<section class="slide">B</section>')
    c = _EditSeq([whole_deck, whole_deck])
    with pytest.raises(ValueError, match="expected exactly one"):
        chat_edit.rewrite_slide(_DECK, 2, "поправь", client=c)


# ── chat_agent: rule-based фолбэк классификатора ─────────────────────────────
class _BoomJson:
    def chat_json(self, *a, **k):
        raise RuntimeError("bad json")


@pytest.mark.parametrize("message,action", [
    ("удали этот слайд", "delete"),
    ("убери слайд 3", "delete"),
    ("добавь слайд про тарифы", "add"),
    ("составь план презентации", "plan"),
    ("собери деку", "build_now"),
    ("что-то непонятное", "chat"),          # сомнительное — по-прежнему chat
])
def test_classify_rule_fallback_on_llm_failure(message, action):
    intent = chat_agent.classify(_BoomJson(), message, ctx="")
    assert intent.action == action


# ── chat_agent: cap аутлайна ─────────────────────────────────────────────────
def test_outline_capped_at_max_slides(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    draft.save_plan("s", draft.DraftPlan(title="Демо", slides=[]))

    class _C:
        def chat_json(self, messages, model_cls, **kw):
            if model_cls.__name__ == "Intent":
                return model_cls.model_validate({"action": "plan"})
            assert model_cls.__name__ == "OutlineDraft"
            return model_cls.model_validate({"slides": [
                {"title": f"S{i}", "brief": f"b{i}"} for i in range(30)]})

        def chat(self, *a, **k):
            return "ok"

    res = chat_agent.run_turn("s", "составь план", 1, client=_C())
    assert res.changed
    assert len(draft.load_plan("s").slides) == chat_agent._OUTLINE_MAX_SLIDES
