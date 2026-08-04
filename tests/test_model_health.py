"""Плашка доступности ИИ: GET /api/models/health и кэш проб.

Проба стоит запроса к провайдеру и токенов, поэтому главное здесь — не «какой
цвет показать», а «как часто мы спрашиваем»: ответ отдаётся мгновенно по
последнему известному состоянию, а сама проба одна на все вкладки и не чаще TTL.
"""
import asyncio
import os
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

import pytest

from webapp import model_health as mh


class FakeClient:
    """Двойник KimiClient: считает пробы и отвечает по заданной карте."""
    calls: list[str] = []
    up: dict[str, bool] = {}
    fallback = "backup-model"
    delay = 0.0

    def __init__(self):
        self.model = "primary-model"

    @property
    def fallback_model(self):
        return type(self).fallback

    def probe(self, name):
        type(self).calls.append(name)
        if type(self).delay:
            import time
            time.sleep(type(self).delay)
        return type(self).up.get(name, False)


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    import htmlslides.pipeline.client as cl
    FakeClient.calls = []
    FakeClient.up = {"primary-model": True, "backup-model": True}
    FakeClient.fallback = "backup-model"
    FakeClient.delay = 0.0
    monkeypatch.setattr(cl, "KimiClient", FakeClient)
    mh.reset()
    yield
    mh.reset()


async def _quiet():
    """Дождаться, пока фоновая проба отработает (иначе она переживёт свой loop)."""
    for _ in range(200):
        await asyncio.sleep(0.01)
        if not mh._refreshing:
            return
    raise AssertionError("проба не завершилась")


async def _settled():
    await _quiet()
    assert mh._checked_at is not None


async def test_cold_start_is_unknown_and_does_not_block_the_response():
    """Первый заход не ждёт провайдера: честное «пока не знаю» лучше, чем
    страница, которая висит секунды на HTTP-пробе."""
    snap = mh.snapshot()
    assert snap["state"] == "unknown"
    assert snap["checked_ago_sec"] is None
    await _settled()
    assert mh.snapshot()["state"] == "ok"


@pytest.mark.parametrize("primary,backup,state", [
    (True, True, "ok"),
    (True, False, "ok"),          # основная жива — резерв не важен
    (False, True, "fallback"),
    (False, False, "down"),
])
async def test_state_maps_from_two_probes(primary, backup, state):
    FakeClient.up = {"primary-model": primary, "backup-model": backup}
    mh.snapshot()
    await _settled()
    snap = mh.snapshot()
    assert snap["state"] == state
    assert snap["primary_up"] is primary
    assert snap["fallback_up"] is backup


async def test_without_a_fallback_the_second_indicator_has_no_data():
    """CLOUDRU_FALLBACK_MODEL пуст — резерва нет; выдумывать ему «упал» нельзя,
    иначе плашка покажет несуществующую поломку."""
    FakeClient.fallback = ""
    FakeClient.up = {"primary-model": False}
    mh.snapshot()
    await _settled()
    snap = mh.snapshot()
    assert snap["fallback_up"] is None
    assert snap["state"] == "down"
    assert FakeClient.calls == ["primary-model"]


async def test_fresh_cache_is_not_reprobed():
    mh.snapshot()
    await _settled()
    n = len(FakeClient.calls)
    for _ in range(5):
        mh.snapshot()
    await _quiet()
    assert len(FakeClient.calls) == n


async def test_stale_cache_is_reprobed():
    mh.snapshot()
    await _settled()
    n = len(FakeClient.calls)
    mh._checked_at -= mh.TTL_SEC + 1
    mh.snapshot()
    await _quiet()
    assert len(FakeClient.calls) > n


async def test_parallel_snapshots_share_one_probe():
    """Десять открытых вкладок — не десять запросов к провайдеру."""
    FakeClient.delay = 0.05
    for _ in range(10):
        mh.snapshot()
    await _settled()
    assert FakeClient.calls == ["primary-model", "backup-model"]


async def test_probe_failure_leaves_state_unknown(monkeypatch):
    """Нет ключа API — состояние остаётся неизвестным, а не «всё лежит»:
    красная плашка по своей же поломке отпугнёт пользователя зря."""
    def boom(self, name):
        raise RuntimeError("no api key")

    FakeClient.fallback = ""
    monkeypatch.setattr(FakeClient, "probe", boom)
    mh.snapshot()
    await _quiet()
    snap = mh.snapshot()
    assert snap["state"] == "unknown"
    assert snap["checked_ago_sec"] is None
    await _quiet()  # снимок выше запустил ещё одну пробу — дожидаемся и её


def test_endpoint_answers_roles_not_model_names(monkeypatch, tmp_path):
    """Имена моделей наружу не отдаём: это деталь реализации (менялась дважды),
    а пользователю нужна роль — «основная»/«резервная»."""
    from fastapi.testclient import TestClient
    import webapp.app as appmod
    import webapp.config as cfg

    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(cfg.settings, "db_url",
                        f"sqlite+aiosqlite:///{tmp_path / 'h.db'}")
    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cfg.settings, "dev_user_id", "")
    with TestClient(appmod.app) as c:
        r = c.get("/api/models/health", headers={"X-User-Id": "u1"})
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"state", "primary_up", "fallback_up",
                             "checked_ago_sec"}
        assert "primary-model" not in r.text and "backup-model" not in r.text
        assert c.get("/api/models/health").status_code == 401
