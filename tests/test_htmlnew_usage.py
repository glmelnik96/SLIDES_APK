"""run_htmlnew читает расход из client.usage_total и кладёт стоимость в done().

Движок владеет клиентом (создаёт сам), чтобы после сборки прочитать
накопленный usage_total и посчитать рубли. В exact-режиме LLM нет —
клиент не создаётся, токены/стоимость остаются None.
"""
from pathlib import Path

from schemas.session import Mode, SessionInput, SessionState


def _input(**kw):
    base = dict(session_id="s1", user_id=1, chat_id=0, progress_message_id=0,
                mode=Mode.HTMLNEW, input_s3_key="/tmp/x.pptx",
                source_filename="x.pptx")
    base.update(kw)
    return SessionInput(**base)


def _fake_build(out):
    Path(out).write_text("<html></html>", encoding="utf-8")
    return Path(out)


def test_run_htmlnew_reports_usage_and_cost(monkeypatch, tmp_path):
    import worker.tasks.htmlnew as htmlnew

    class _FakeClient:
        def __init__(self, *a, **k):
            self.usage_total = {"prompt_tokens": 12_400, "completion_tokens": 8_900,
                                "cached_tokens": 0, "calls": 3}

    captured = {}
    monkeypatch.setattr("htmlslides.pipeline.build.build_deck",
                        lambda inp, out, **kw: _fake_build(out))
    monkeypatch.setattr("htmlslides.pipeline.client.KimiClient", _FakeClient)
    monkeypatch.setattr(htmlnew.progress, "stage", lambda *a, **k: None)
    monkeypatch.setattr(htmlnew.progress, "done", lambda *a, **k: captured.update(k))
    monkeypatch.setattr(htmlnew, "session_dir", lambda sid: tmp_path)

    state = SessionState.from_input(
        _input(input_s3_key=str(tmp_path / "d.pptx"), source_filename="d.pptx"))
    htmlnew.run_htmlnew(state)

    assert captured["prompt_tokens"] == 12_400
    assert captured["completion_tokens"] == 8_900
    assert captured["cost_rub"] == 11.96   # pricing.cost_rub(12400, 8900)


def test_run_htmlnew_exact_creates_no_client_and_no_cost(monkeypatch, tmp_path):
    import worker.tasks.htmlnew as htmlnew

    class _NoClient:
        def __init__(self, *a, **k):
            raise AssertionError("exact-режим не должен создавать KimiClient")

    captured = {}
    monkeypatch.setattr("htmlslides.pipeline.build.build_deck",
                        lambda inp, out, **kw: _fake_build(out))
    monkeypatch.setattr("htmlslides.pipeline.client.KimiClient", _NoClient)
    monkeypatch.setattr(htmlnew.progress, "stage", lambda *a, **k: None)
    monkeypatch.setattr(htmlnew.progress, "done", lambda *a, **k: captured.update(k))
    monkeypatch.setattr(htmlnew, "session_dir", lambda sid: tmp_path)

    state = SessionState.from_input(
        _input(input_s3_key=str(tmp_path / "d.pptx"),
               source_filename="d.pptx", exact_transfer=True))
    htmlnew.run_htmlnew(state)

    assert captured["prompt_tokens"] is None
    assert captured["completion_tokens"] is None
    assert captured["cost_rub"] is None
