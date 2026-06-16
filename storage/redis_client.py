"""Redis URL composition and connection helpers.

We keep a sync client (for workers / Celery / LangGraph) and an async client
(for the PTB-side progress subscriber). Both target the same Redis Stack
instance with logical db-split (see PLAN.md §1).
"""
from __future__ import annotations

from enum import IntEnum
from functools import lru_cache
from urllib.parse import urlparse, urlunparse

import redis
import redis.asyncio as redis_async

from bot.config import get_settings


class DB(IntEnum):
    # Redis Stack modules (RediSearch / JSON) work only on db 0 — so anything
    # that needs FT.CREATE indices must live there. We co-locate Celery broker
    # and LangGraph checkpoints on db 0: keyspaces don't overlap
    # (`celery`, `_kombu.*` vs `checkpoint:*`).
    BROKER = 0
    LANGGRAPH = 0
    RESULTS = 1
    PUBSUB = 3
    TG_STATE = 4


def url_for(db: DB) -> str:
    """Compose a redis:// URL with password + db number from settings."""
    s = get_settings()
    p = urlparse(s.redis_url)
    netloc = p.hostname or "localhost"
    if p.port:
        netloc = f"{netloc}:{p.port}"
    if s.redis_password:
        netloc = f"default:{s.redis_password}@{netloc}"
    return urlunparse((p.scheme or "redis", netloc, f"/{db.value}", "", "", ""))


@lru_cache(maxsize=8)
def sync_client(db: DB) -> redis.Redis:
    """Return a cached sync client for the given DB."""
    return redis.Redis.from_url(url_for(db), decode_responses=True)


def async_client(db: DB) -> redis_async.Redis:
    """Return a fresh async client. Don't cache — caller owns the lifetime."""
    return redis_async.Redis.from_url(url_for(db), decode_responses=True)
