"""Агрегация usage_events под блок «Слайды» в админке платформы.

Форма ответа согласована с App1 (COORDINATION, 2026-08-02): агрегаты считаются по
ВСЕМУ периоду, `limit` режет только ленту последних событий. Наружу уходят только
безопасные поля — ни имён файлов, ни содержимого, ни дословных текстов ошибок
(класс сбоя — код-перечисление из `build_errors.error_code`).

Считаем в Python по строкам периода, а не SQL-выражениями по JSON: `meta` — это
JSON-колонка, и её SQL-разбор привязал бы нас к диалекту БД, а объём микроскопический
(десятки строк в месяц).
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

APP_NAME = "slides"
TOP_USERS = 5


def _iso(dt: datetime | None) -> str | None:
    """ISO-8601 UTC с `Z`. SQLite отдаёт naive-datetime — считаем их UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z")


def _percentile(values: list[int], frac: float) -> int | None:
    """Ближайший ранг (nearest-rank) — без интерполяции: на десятках точек она
    создаёт видимость точности, которой нет."""
    if not values:
        return None
    idx = max(0, min(len(values) - 1, round(frac * (len(values) - 1))))
    return values[idx]


def _pairs(counter: Counter, limit: int | None = None) -> list[dict]:
    items = counter.most_common(limit) if limit else counter.most_common()
    return [{"key": k, "count": n} for k, n in items]


def _mode_of(row: Any) -> str | None:
    """Сценарий: из обогащённой meta, иначе из колонки workflow (строки, записанные
    до обогащения, знают его только там)."""
    return (row.meta or {}).get("mode") or row.workflow


def _user_key(row: Any) -> str | None:
    return row.email or row.gateway_user_id


def aggregate(rows: Iterable[Any], *, since: datetime | None = None,
              until: datetime | None = None, limit: int = 8) -> dict:
    rows = list(rows)
    durations = sorted(r.duration_ms for r in rows if r.duration_ms is not None)
    metas = [r.meta or {} for r in rows]
    statuses = Counter(r.status or "unknown" for r in rows)
    # Ключ пользователя — email (его показывает админка), но у аккаунта без email
    # в шапке шлюза он пуст: тогда падаем на gateway_user_id, иначе такой юзер
    # выпал бы и из счётчика, и из топа.
    emails = Counter(_user_key(r) for r in rows if _user_key(r))
    by_time = sorted(rows, key=lambda r: (r.created_at or datetime.min.replace(
        tzinfo=timezone.utc)), reverse=True)

    def _sum(key: str) -> float:
        return sum(m[key] for m in metas if isinstance(m.get(key), (int, float)))

    return {
        "app": APP_NAME,
        "since": _iso(since), "until": _iso(until),
        "generated_at": _iso(datetime.now(timezone.utc)),
        "total": len(rows),
        "done": statuses.get("done", 0),
        "failed": statuses.get("failed", 0),
        "cancelled": statuses.get("cancelled", 0),
        "truncated": sum(1 for m in metas if m.get("truncated")),
        "users": len(emails),
        "last_used": _iso(by_time[0].created_at) if by_time else None,
        # Среднее оставляем, но рядом с медианой и p95: одна долгая сборка задирает
        # avg в разы (урок App1 на данных App3 — 65 мин против медианы 9,3).
        "avg_ms": int(sum(durations) / len(durations)) if durations else None,
        "median_ms": _percentile(durations, 0.5),
        "p95_ms": _percentile(durations, 0.95),
        "slides_total": int(_sum("slides_count")),
        "cost_rub": round(_sum("cost_rub"), 2),
        "tokens_in": int(_sum("in_tokens")),
        "tokens_out": int(_sum("out_tokens")),
        "by_status": _pairs(statuses),
        "by_mode": _pairs(Counter(m for m in map(_mode_of, rows) if m)),
        "by_error": _pairs(Counter(
            m["error_code"] for m in metas if m.get("error_code"))),
        "top_users": _pairs(emails, TOP_USERS),
        "recent": [_feed_row(r) for r in by_time[:max(0, limit)]],
    }


async def collect(session: Any, *, since: datetime | None = None,
                  until: datetime | None = None, limit: int = 8) -> dict:
    """Строки нашего приложения за полуинтервал [since, until) → агрегаты."""
    from sqlalchemy import select

    from webapp.db import models
    q = select(models.UsageEvent).where(models.UsageEvent.app == APP_NAME)
    if since is not None:
        q = q.where(models.UsageEvent.created_at >= since.astimezone(timezone.utc))
    if until is not None:
        q = q.where(models.UsageEvent.created_at < until.astimezone(timezone.utc))
    rows = (await session.execute(q)).scalars().all()
    return aggregate(rows, since=since, until=until, limit=limit)


def _feed_row(row: Any) -> dict:
    meta = row.meta or {}
    return {
        "created_at": _iso(row.created_at),
        "email": _user_key(row),
        "mode": _mode_of(row),
        "status": row.status,
        "slides": meta.get("slides_count"),
        "duration_ms": row.duration_ms,
        "cost_rub": meta.get("cost_rub"),
        "truncated": bool(meta.get("truncated")),
    }
