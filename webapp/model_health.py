"""Доступность моделей ИИ для плашки на главной.

Отвечает на один вопрос: «есть ли смысл запускать сборку прямо сейчас». Инцидент
2026-08-04 научил пайплайн быстро отказывать (см. docs/.../2026-08-04-model-fallback.md),
но узнавал об отказе пользователь всё равно постфактум — потратив загрузку файла
и место в очереди.

Меряем ТОЙ ЖЕ пробой, что и сборка (``KimiClient.probe``): свой HTTP-клиент
продублировал бы знание о провайдере и со временем разъехался бы с движком.
Именно ``probe``, а не ``preflight``, — последний липко переводит клиент на резерв,
а опрос для UI обязан быть read-only.

Кэш живёт в памяти процесса: TTL 60 с, stale-while-revalidate. Ответ отдаём
мгновенно по последнему известному состоянию, а протухшие данные обновляем фоном —
поэтому проба никогда не задерживает запрос и одна на все открытые вкладки.
Фонового поллинга нет: простаивающий сервис не должен жечь токены.
"""
from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

TTL_SEC = 60.0

_checked_at: float | None = None      # time.monotonic() последней успешной пробы
_primary_up: bool | None = None
_fallback_up: bool | None = None      # None = резерв не настроен
_refreshing = False                   # проба уже в полёте (одна на все вкладки)


def _state() -> str:
    if _primary_up is None:
        return "unknown"
    if _primary_up:
        return "ok"
    return "fallback" if _fallback_up else "down"


def snapshot() -> dict:
    """Текущее состояние + запуск фонового обновления, если данные протухли.

    Наружу отдаём РОЛИ, а не имена моделей: имя — деталь реализации (менялось уже
    дважды), а роль стабильна.
    """
    if _checked_at is None or (time.monotonic() - _checked_at) > TTL_SEC:
        _schedule_refresh()
    return {
        "state": _state(),
        "primary_up": _primary_up,
        "fallback_up": _fallback_up,
        "checked_ago_sec": (None if _checked_at is None
                            else int(time.monotonic() - _checked_at)),
    }


def _schedule_refresh() -> None:
    global _refreshing
    if _refreshing:
        return
    _refreshing = True
    asyncio.create_task(_refresh())


async def _refresh() -> None:
    global _checked_at, _primary_up, _fallback_up, _refreshing
    try:
        from htmlslides.pipeline.client import KimiClient
        client = KimiClient()
        fallback = client.fallback_model
        # Параллельно: последовательные пробы стоили бы двух PROBE_TIMEOUT подряд
        # ровно в том случае, когда лежит всё и ответ нужен быстрее всего.
        t0 = time.monotonic()
        primary, backup = await asyncio.gather(
            asyncio.to_thread(client.probe, client.model),
            (asyncio.to_thread(client.probe, fallback) if fallback
             else _none()))
        # Вердикт плашки обязан быть виден в журнале: иначе «у пользователя горит
        # красное» нечем проверить — проба ходит наружу и её отказ не наш баг.
        logger.info("model health: primary=%s fallback=%s in %.1fs", primary,
                    backup, time.monotonic() - t0)
        _primary_up, _fallback_up, _checked_at = primary, backup, time.monotonic()
    except Exception:  # noqa: BLE001
        # Нет ключа API / клиент не собрался — состояние честно остаётся
        # неизвестным (плашка покажет нейтральное «проверяю»), а не «всё лежит».
        logger.warning("model health probe failed", exc_info=True)
    finally:
        _refreshing = False


async def _none() -> None:
    return None


def reset() -> None:
    """Сбросить кэш (тесты; в проде состояние живёт до рестарта процесса)."""
    global _checked_at, _primary_up, _fallback_up, _refreshing
    _checked_at = _primary_up = _fallback_up = None
    _refreshing = False
