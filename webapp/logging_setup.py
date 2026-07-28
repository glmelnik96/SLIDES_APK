"""Логи сервиса с уровнями, видимыми journald.

structlog (пайплайн) и stdlib logging (webapp) писали всё в stdout без пометки
уровня, поэтому systemd метил КАЖДУЮ строку как info: `journalctl -u app2
-p warning` отдавал «No entries», хотя и предупреждения планировщика, и трейсбеки
упавших сборок в логе были. Из-за этого пост-деплойная проверка «journal чист»
ничего не гарантировала.

Лечение — ведущий префикс `<N>` (syslog severity) на каждой строке: journald
разбирает его сам (`SyslogLevelPrefix` включён по умолчанию) и проставляет
настоящий приоритет записи.
"""
from __future__ import annotations

import logging
import sys

import structlog

_SYSLOG_PRIORITY = {
    "critical": 2, "exception": 3, "error": 3,
    "warning": 4, "warn": 4, "info": 6, "debug": 7,
}
_STDLIB_PRIORITY = {
    logging.CRITICAL: 2, logging.ERROR: 3, logging.WARNING: 4,
    logging.INFO: 6, logging.DEBUG: 7,
}


class _PriorityFormatter(logging.Formatter):
    """stdlib-формат + `<N>`; многострочный трейсбек journald метит целиком."""

    def format(self, record: logging.LogRecord) -> str:
        return f"<{_STDLIB_PRIORITY.get(record.levelno, 6)}>{super().format(record)}"


def configure_service_logging(level: str = "INFO") -> None:
    """Настроить оба логгера (stdlib + structlog) на stdout с приоритетами."""
    lvl = getattr(logging, level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_PriorityFormatter("%(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]      # uvicorn держит свои логгеры с propagate=False
    root.setLevel(lvl)

    console = structlog.dev.ConsoleRenderer(colors=False)

    def render_with_priority(logger, name, event_dict):
        # level читаем ДО рендера: ConsoleRenderer забирает ключ из event_dict
        prio = _SYSLOG_PRIORITY.get(str(event_dict.get("level", "info")), 6)
        return f"<{prio}>{console(logger, name, event_dict)}"

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            render_with_priority,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
