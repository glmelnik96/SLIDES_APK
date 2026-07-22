"""progress.done() переносит токены/стоимость в терминальное событие.

duration_ms — поле схемы, но выставляет его раннер (он знает старт задачи для
любого исхода: done/failed/cancelled), поэтому здесь проверяем только дефолт.
"""
from schemas.session import ProgressEvent
from worker import progress


def test_done_carries_usage_and_cost(monkeypatch):
    captured = {}
    monkeypatch.setattr(progress, "publish",
                        lambda ev: captured.__setitem__("ev", ev))
    progress.done("s1", detail="готово", result_path="/tmp/x.html",
                  prompt_tokens=12_400, completion_tokens=8_900, cost_rub=11.96)
    ev = captured["ev"]
    assert isinstance(ev, ProgressEvent)
    assert ev.terminal is True
    assert ev.prompt_tokens == 12_400
    assert ev.completion_tokens == 8_900
    assert ev.cost_rub == 11.96


def test_done_usage_fields_default_to_none(monkeypatch):
    captured = {}
    monkeypatch.setattr(progress, "publish",
                        lambda ev: captured.__setitem__("ev", ev))
    progress.done("s1", detail="готово")
    ev = captured["ev"]
    assert ev.prompt_tokens is None
    assert ev.completion_tokens is None
    assert ev.cost_rub is None
    assert ev.duration_ms is None  # поле есть в схеме; раннер выставит отдельно
