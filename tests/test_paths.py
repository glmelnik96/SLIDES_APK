from pathlib import Path
import webapp.paths as paths


def test_workdir_root_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    assert paths.workdir_root() == tmp_path


def test_session_dir_created(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    d = paths.session_dir("abc123")
    assert d == tmp_path / "abc123"
    assert d.is_dir()


def test_default_root_is_tempdir(monkeypatch):
    monkeypatch.delenv("SLIDESBOT_WORKDIR", raising=False)
    root = paths.workdir_root()
    assert root.name == "sessions"
    assert "slidesapp" in str(root)
