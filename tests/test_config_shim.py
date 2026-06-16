import os
import importlib


def test_apply_sets_placeholder_when_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("CLOUDRU_API_KEY", "k")
    shim = importlib.import_module("webapp.config_shim")
    shim.apply()
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "unused"


def test_apply_does_not_overwrite_existing(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "real")
    monkeypatch.setenv("CLOUDRU_API_KEY", "k")
    shim = importlib.import_module("webapp.config_shim")
    shim.apply()
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "real"


def test_apply_raises_without_api_key(monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUDRU_API_KEY", raising=False)
    shim = importlib.import_module("webapp.config_shim")
    import pytest
    # Point at a non-existent .env so the loader is skipped and the missing-key
    # path is exercised regardless of any real .env in the repo root.
    with pytest.raises(SystemExit):
        shim.apply(env_path=tmp_path / "missing.env")


def test_apply_loads_env_file(monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUDRU_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text("CLOUDRU_API_KEY=from_env_file\n", encoding="utf-8")
    import importlib
    shim = importlib.import_module("webapp.config_shim")
    shim.apply(env_path=env)
    assert os.environ["CLOUDRU_API_KEY"] == "from_env_file"
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "unused"
