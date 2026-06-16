import types
import webapp.pipeline_bridge as pb


class _FakeInp:
    def __init__(self, mode):
        self.mode = mode
        self.session_id = "s1"


def test_run_htmlnew_path(monkeypatch):
    calls = {}
    monkeypatch.setattr(pb, "_state_from_input", lambda inp: "STATE")
    fake_htmlnew = types.SimpleNamespace(run_htmlnew=lambda s: calls.setdefault("htmlnew", s))
    monkeypatch.setitem(pb._ENGINE, "htmlnew", lambda: fake_htmlnew.run_htmlnew)
    pb.run(_FakeInp("htmlnew"))
    assert calls["htmlnew"] == "STATE"


def test_run_verstai_compiles_and_invokes(monkeypatch):
    invoked = {}

    class _Graph:
        def invoke(self, payload):
            invoked["payload"] = payload

    class _Builder:
        def compile(self):
            return _Graph()

    monkeypatch.setattr(pb, "_state_from_input", lambda inp: types.SimpleNamespace(
        model_dump=lambda: {"k": "v"}))
    monkeypatch.setitem(pb._ENGINE, "verstai", lambda: _Builder())
    pb.run(_FakeInp("verstai"))
    assert invoked["payload"] == {"k": "v"}


def test_unknown_mode_raises():
    import pytest
    with pytest.raises(ValueError):
        pb.run(_FakeInp("nope"))
