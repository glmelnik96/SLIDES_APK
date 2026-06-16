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


def test_apply_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("CLOUDRU_API_KEY", raising=False)
    shim = importlib.import_module("webapp.config_shim")
    import pytest
    with pytest.raises(SystemExit):
        shim.apply()
