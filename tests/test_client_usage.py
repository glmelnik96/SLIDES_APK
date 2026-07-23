"""KimiClient._record_usage накапливает расход НЕЗАВИСИМО от формы ответа.

Cloud.ru через openai-SDK отдаёт usage typed-объектом (CompletionUsage) — его
код читал верно и раньше. Но часть OpenAI-совместимых шлюзов и model_dump-пути
отдают usage плоским словарём, на котором getattr молча вернул бы 0. Тест
фиксирует контракт на обе формы: usage объектом, словарём и None прогоняется
через настоящий chat() (а не подменой usage_total, как в test_htmlnew_usage),
чтобы обе формы копились, а отсутствие usage не роняло счётчик calls.
"""
from types import SimpleNamespace

from htmlslides.pipeline.client import KimiClient


def _resp(usage):
    choice = SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'),
                             finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=usage)


class _Transport:
    """Дублёр openai-клиента: client.chat.completions.create(...) -> ответ."""
    def __init__(self, usage):
        self._usage = usage

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **_kw):
        return _resp(self._usage)


def _client(usage):
    c = KimiClient(transport=_Transport(usage))
    c.chat([{"role": "user", "content": "hi"}])
    return c.usage_total


def test_usage_as_object_accumulates():
    usage = SimpleNamespace(prompt_tokens=1200, completion_tokens=800,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=100))
    total = _client(usage)
    assert total["prompt_tokens"] == 1200
    assert total["completion_tokens"] == 800
    assert total["cached_tokens"] == 100
    assert total["calls"] == 1


def test_usage_as_dict_accumulates():
    # Ключевой случай регрессии: usage плоским словарём (шлюз/ model_dump).
    usage = {"prompt_tokens": 1200, "completion_tokens": 800,
             "prompt_tokens_details": {"cached_tokens": 100}}
    total = _client(usage)
    assert total["prompt_tokens"] == 1200
    assert total["completion_tokens"] == 800
    assert total["cached_tokens"] == 100
    assert total["calls"] == 1


def test_usage_dict_without_details():
    total = _client({"prompt_tokens": 500, "completion_tokens": 300})
    assert total["prompt_tokens"] == 500
    assert total["completion_tokens"] == 300
    assert total["cached_tokens"] == 0
    assert total["calls"] == 1


def test_usage_missing_stays_zero_but_counts_call():
    # Провайдер не вернул usage — токенов нет, но факт вызова фиксируем (calls),
    # чтобы диагностика в htmlnew могла отличить «не считали» от «нечего считать».
    total = _client(None)
    assert total["prompt_tokens"] == 0
    assert total["completion_tokens"] == 0
    assert total["calls"] == 1
