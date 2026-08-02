"""GET /internal/stats — машинный эндпоинт статистики для админки платформы.

Контракт согласован с App1 (COORDINATION, 2026-08-02): заголовок `X-Ingest-Token`,
`?since=&until=&limit=`, полуинтервал `[since, until)`, `limit` — только лента.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

import webapp.app as appmod
import webapp.config as cfg
from fastapi.testclient import TestClient

TOKEN = "s3cr3t"
T0 = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def _client(monkeypatch, tmp_path, token=TOKEN):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path / "sessions"))
    monkeypatch.setattr(cfg.settings, "db_url",
                        f"sqlite+aiosqlite:///{tmp_path / 'stats.db'}")
    monkeypatch.setattr(cfg.settings, "data_dir", tmp_path)
    monkeypatch.setattr(cfg.settings, "dev_user_id", "")
    monkeypatch.setattr(cfg.settings, "stats_token", token)
    return TestClient(appmod.app)


def _seed(events):
    """events: (offset_minutes, status, meta)."""
    from webapp.db import models

    async def go():
        async with appmod.app.state.sessionmaker() as s:
            for i, status, meta in events:
                s.add(models.UsageEvent(
                    app="slides", gateway_user_id="gw", email="u@x.ru",
                    event="render", workflow=meta.get("mode"), status=status,
                    duration_ms=1000, created_at=T0 + timedelta(minutes=i),
                    meta=meta))
            await s.commit()

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(go())
    finally:
        loop.close()


def test_requires_the_shared_token(monkeypatch, tmp_path):
    """Loopback — не граница: на общей VM любой процесс ходит на 127.0.0.1:8012,
    а метрики несут email'ы. Заголовок обязателен."""
    with _client(monkeypatch, tmp_path) as c:
        assert c.get("/internal/stats").status_code == 401
        assert c.get("/internal/stats",
                     headers={"X-Ingest-Token": "wrong"}).status_code == 401
        assert c.get("/internal/stats",
                     headers={"X-Ingest-Token": TOKEN}).status_code == 200


def test_disabled_without_configured_token(monkeypatch, tmp_path):
    """Пустой секрет = эндпоинт выключен (нормальное состояние локалки)."""
    with _client(monkeypatch, tmp_path, token="") as c:
        assert c.get("/internal/stats",
                     headers={"X-Ingest-Token": "any"}).status_code == 404


def test_no_user_header_required(monkeypatch, tmp_path):
    """Шлюз ходит машинным запросом — X-User-Id у него нет, 401 быть не должно."""
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/internal/stats", headers={"X-Ingest-Token": TOKEN})
        assert r.status_code == 200
        assert r.json()["app"] == "slides"


def test_window_is_half_open_and_limit_cuts_only_feed(monkeypatch, tmp_path):
    with _client(monkeypatch, tmp_path) as c:
        _seed([(i, "done", {"mode": "htmlnew"}) for i in range(10)])
        r = c.get("/internal/stats",
                  params={"since": "2026-08-02T12:00:00Z",
                          "until": "2026-08-02T12:05:00Z", "limit": 2},
                  headers={"X-Ingest-Token": TOKEN})
        body = r.json()
        assert body["total"] == 5            # [12:00, 12:05) — пять минут-строк
        assert len(body["recent"]) == 2      # limit режет только ленту
        assert body["since"] == "2026-08-02T12:00:00Z"


def test_bad_timestamp_is_client_error(monkeypatch, tmp_path):
    """Молча игнорировать кривой since нельзя: шлюз покажет чужой период."""
    with _client(monkeypatch, tmp_path) as c:
        r = c.get("/internal/stats", params={"since": "вчера"},
                  headers={"X-Ingest-Token": TOKEN})
        assert r.status_code == 400


def test_failure_is_5xx_not_zeroed_200(monkeypatch, tmp_path):
    """Условие App1: нули читаются как «работы не было» — на сбой честный 5xx."""
    with _client(monkeypatch, tmp_path) as c:
        monkeypatch.setattr(appmod.stats, "aggregate",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError))
        r = c.get("/internal/stats", headers={"X-Ingest-Token": TOKEN})
        assert r.status_code >= 500


def test_response_never_leaks_filenames_or_error_text(monkeypatch, tmp_path):
    """Запрет из нашего же описания метрик, пинится тестом."""
    with _client(monkeypatch, tmp_path) as c:
        _seed([(0, "failed", {"mode": "exact",
                              "error_code": "provider_timeout"})])
        r = c.get("/internal/stats", headers={"X-Ingest-Token": TOKEN})
        body = r.json()
        assert body["by_error"] == [{"key": "provider_timeout", "count": 1}]
        assert "source_filename" not in r.text
        # ни в агрегате, ни в ленте нет полей с сырым текстом сбоя
        assert "error" not in body
        assert all("error" not in row for row in body["recent"])
