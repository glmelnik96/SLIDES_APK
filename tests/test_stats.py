"""Агрегация usage_events под блок «Слайды» в админке платформы (webapp.stats).

Форма ответа зафиксирована App1 (COORDINATION, 2026-08-02): агрегаты по всему
периоду + топ-5 пользователей + лента (её и только её режет `limit`).
"""
from datetime import datetime, timedelta, timezone

from webapp import stats
from webapp.db import models

T0 = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def _ev(i=0, *, status="done", email="a@b.c", duration_ms=1000, **meta):
    return models.UsageEvent(
        app="slides", gateway_user_id=email, email=email, event="render",
        workflow=meta.get("mode"), status=status, duration_ms=duration_ms,
        created_at=T0 + timedelta(minutes=i), meta=meta)


def test_terminal_counters_and_truncated():
    rows = [_ev(0), _ev(1, status="failed"), _ev(2, status="cancelled"),
            _ev(3, truncated=True)]
    out = stats.aggregate(rows)
    assert out["app"] == "slides"
    assert (out["total"], out["done"], out["failed"], out["cancelled"]) == (4, 2, 1, 1)
    assert out["truncated"] == 1     # «успех с оговоркой» считается отдельно


def test_duration_percentiles_are_not_just_average():
    """App1 отдельно просил медиану: одна долгая сборка задирает среднее."""
    rows = [_ev(i, duration_ms=d) for i, d in
            enumerate([60_000, 60_000, 60_000, 60_000, 6_000_000])]
    out = stats.aggregate(rows)
    assert out["median_ms"] == 60_000
    assert out["avg_ms"] > out["median_ms"]
    assert out["p95_ms"] == 6_000_000


def test_money_and_volume_sums_skip_unknown():
    """Старые строки без обогащения не должны считаться нулями — их просто нет."""
    rows = [_ev(0, slides_count=45, cost_rub=61.2, in_tokens=100, out_tokens=20),
            _ev(1, slides_count=5, cost_rub=1.3, in_tokens=7, out_tokens=3),
            _ev(2)]
    out = stats.aggregate(rows)
    assert out["slides_total"] == 50
    assert out["cost_rub"] == 62.5
    assert (out["tokens_in"], out["tokens_out"]) == (107, 23)


def test_breakdowns_are_key_count_pairs_sorted():
    rows = [_ev(0, mode="htmlnew"), _ev(1, mode="htmlnew"), _ev(2, mode="exact"),
            _ev(3, status="failed", error_code="provider_timeout")]
    out = stats.aggregate(rows)
    assert out["by_mode"][0] == {"key": "htmlnew", "count": 2}
    assert {"key": "exact", "count": 1} in out["by_mode"]
    assert out["by_error"] == [{"key": "provider_timeout", "count": 1}]
    assert {"key": "done", "count": 3} in out["by_status"]


def test_top_users_capped_at_five():
    rows = [_ev(i, email=f"u{i % 7}@x.ru") for i in range(20)]
    out = stats.aggregate(rows)
    assert out["users"] == 7
    assert len(out["top_users"]) == 5
    counts = [u["count"] for u in out["top_users"]]
    assert counts == sorted(counts, reverse=True)


def test_user_without_email_still_counts():
    """Локальный dev-юзер и любой аккаунт без email в шапке шлюза не должны
    выпадать из счётчика пользователей (поймано на локальном e2e)."""
    row = _ev(0, email="")
    row.gateway_user_id = "gw-42"
    out = stats.aggregate([row])
    assert out["users"] == 1
    assert out["top_users"] == [{"key": "gw-42", "count": 1}]


def test_limit_cuts_only_the_feed():
    """Условие App1: `limit` режет ленту, агрегаты — по всему периоду."""
    rows = [_ev(i) for i in range(20)]
    out = stats.aggregate(rows, limit=8)
    assert out["total"] == 20
    assert len(out["recent"]) == 8
    # лента — новейшие сверху
    assert out["recent"][0]["created_at"] > out["recent"][1]["created_at"]


def test_recent_row_shape_has_no_filenames_or_error_text():
    """Запрет из нашего же описания метрик: наружу не уходят имена файлов,
    содержимое и дословные тексты ошибок."""
    rows = [_ev(0, mode="htmlnew", slides_count=45, cost_rub=61.2)]
    row = stats.aggregate(rows)["recent"][0]
    assert set(row) == {"created_at", "email", "mode", "status", "slides",
                        "duration_ms", "cost_rub", "truncated"}
    assert row["created_at"].endswith("Z")
    assert row["truncated"] is False


def test_empty_period_is_zeroed_but_well_formed():
    out = stats.aggregate([])
    assert out["total"] == 0 and out["users"] == 0
    assert out["last_used"] is None
    assert out["median_ms"] is None
    assert out["recent"] == [] and out["by_mode"] == []


def test_generated_at_and_window_are_iso_utc():
    out = stats.aggregate([_ev(0)], since=T0, until=T0 + timedelta(hours=1))
    assert out["since"].endswith("Z") and out["until"].endswith("Z")
    assert out["generated_at"].endswith("Z")
    assert out["last_used"] == "2026-08-02T12:00:00Z"


def test_naive_timestamps_from_sqlite_are_treated_as_utc():
    """SQLite отдаёт datetime без tzinfo — нельзя выдавать их без `Z`."""
    row = _ev(0)
    row.created_at = datetime(2026, 8, 2, 12, 0)     # naive, как из SQLite
    out = stats.aggregate([row])
    assert out["last_used"] == "2026-08-02T12:00:00Z"


def test_mode_falls_back_to_workflow_column():
    """Строки до обогащения meta знают сценарий только в колонке workflow."""
    row = models.UsageEvent(app="slides", email="a@b.c", event="render",
                            workflow="rebrand", status="done", duration_ms=1,
                            created_at=T0, meta={})
    out = stats.aggregate([row])
    assert out["by_mode"] == [{"key": "rebrand", "count": 1}]
