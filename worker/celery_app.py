"""Celery application — sync prefork, single queue, conservative defaults.

See PLAN.md §1 (Approach A) for why this is sync.
"""
from __future__ import annotations

from celery import Celery

from bot.config import get_settings
from bot.logging_setup import configure_logging
from storage.redis_client import DB, url_for

_settings = get_settings()
configure_logging(_settings.log_level)

app = Celery(
    "slides_bot",
    broker=url_for(DB.BROKER),
    backend=url_for(DB.RESULTS),
    include=["worker.tasks.pipeline"],
)

app.conf.update(
    task_default_queue="default",
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    # Hard cap so a runaway pipeline can't hold a slot forever.
    task_time_limit=60 * 60,        # 1h
    task_soft_time_limit=55 * 60,
    broker_connection_retry_on_startup=True,
    result_expires=60 * 60 * 24 * 7,  # 7 days — matches session bundle TTL
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)
