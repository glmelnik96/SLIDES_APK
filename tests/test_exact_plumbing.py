from pathlib import Path

from schemas.session import Mode, SessionInput, SessionState


def _input(**kw):
    base = dict(session_id="s1", user_id=1, chat_id=0, progress_message_id=0,
                mode=Mode.HTMLNEW, input_s3_key="/tmp/x.pptx",
                source_filename="x.pptx")
    base.update(kw)
    return SessionInput(**base)


def test_session_input_default_false():
    assert _input().exact_transfer is False


def test_from_input_carries_exact():
    state = SessionState.from_input(_input(exact_transfer=True))
    assert state.exact_transfer is True


def test_run_htmlnew_picks_exact_mode(monkeypatch, tmp_path):
    import worker.tasks.htmlnew as htmlnew

    captured = {}

    def fake_build_deck(inp, out, **kw):
        captured["mode"] = kw.get("mode")
        Path(out).write_text("<html></html>", encoding="utf-8")
        return Path(out)

    monkeypatch.setattr("htmlslides.pipeline.build.build_deck", fake_build_deck)
    monkeypatch.setattr(htmlnew.progress, "stage", lambda *a, **k: None)
    monkeypatch.setattr(htmlnew.progress, "done", lambda *a, **k: None)
    monkeypatch.setattr(htmlnew, "session_dir", lambda sid: tmp_path)

    state = SessionState.from_input(
        _input(input_s3_key=str(tmp_path / "d.pptx"),
               source_filename="d.pptx", exact_transfer=True))
    htmlnew.run_htmlnew(state)
    assert captured["mode"] == "exact"


def test_run_htmlnew_default_mode_unchanged(monkeypatch, tmp_path):
    import worker.tasks.htmlnew as htmlnew

    captured = {}

    def fake_build_deck(inp, out, **kw):
        captured["mode"] = kw.get("mode")
        Path(out).write_text("<html></html>", encoding="utf-8")
        return Path(out)

    monkeypatch.setattr("htmlslides.pipeline.build.build_deck", fake_build_deck)
    monkeypatch.setattr(htmlnew.progress, "stage", lambda *a, **k: None)
    monkeypatch.setattr(htmlnew.progress, "done", lambda *a, **k: None)
    monkeypatch.setattr(htmlnew, "session_dir", lambda sid: tmp_path)

    state = SessionState.from_input(
        _input(input_s3_key=str(tmp_path / "d.pptx"), source_filename="d.pptx"))
    htmlnew.run_htmlnew(state)
    assert captured["mode"] == "rebrand"   # pptx по умолчанию
