"""Общие фикстуры тестов.

Главное здесь — дренаж пулов JobRunner. Без него сюита теряет изоляцию:
`JobRunner.start()` кладёт работу в ThreadPoolExecutor, а `work()` читает
`runner._pipeline_run` ЛЕНИВО — уже внутри потока пула (webapp/runner.py).
Тест, закончившийся раньше своего воркера, оставляет поток, который стартует
позже: monkeypatch к тому моменту откатил подмену или подставил подделку
СЛЕДУЮЩЕГО теста. Все тесты шлют события с одним `session_id` "s1", поэтому
чужое событие попадало в очередь чужого раннера — второй `q.get()` возвращал
лишний `parsing` вместо `cancelled`.

Ловится только под конкуренцией за CPU: на 2-ядерной прод-VM — 5 падений на
100 прогонов, последовательно — ни одного. Хуже падений было зависание: если
осиротевший воркер крутит цикл ожидания, non-daemon поток пула не даёт
интерпретатору выйти, и pytest виснет уже ПОСЛЕ подсчёта итога (реально
провисел 5,5 часов на VM, пока его не убили).
"""
import time
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest

import webapp.runner as runner


def drain_pool(pool: ThreadPoolExecutor, timeout: float = 10.0) -> list:
    """Останавливает пул и дожидается его воркеров, но НЕ дольше `timeout`.

    Возвращает список потоков, не завершившихся за отведённое время. Штатный
    `shutdown(wait=True)` тут не годится: у него нет таймаута, и один зависший
    воркер повесил бы весь прогон — ровно то, от чего мы уходим. Лучше упереться
    в таймаут и назвать виновника."""
    pool.shutdown(wait=False)
    deadline = time.monotonic() + timeout
    stuck = []
    for t in list(getattr(pool, "_threads", ())):
        t.join(timeout=max(0.0, deadline - time.monotonic()))
        if t.is_alive():
            stuck.append(t)
    return stuck


@pytest.fixture
def pool_drainer():
    """Отдаёт `drain_pool` тестам: conftest в pytest 9 грузится под уникальным
    именем модуля и по `import conftest` недоступен."""
    return drain_pool


@pytest.fixture(autouse=True)
def _drain_runner_pools(monkeypatch):
    """Дожидается воркеров всех JobRunner, созданных тестом, ДО отката подмен.

    Зависимость от `monkeypatch` не косметическая: она задаёт порядок разборки.
    Фикстура поднимается ПОСЛЕ monkeypatch, значит её teardown идёт РАНЬШЕ
    отката — и осиротевший воркер гарантированно видит подделку своего теста,
    а не следующего.

    Глобальный раннер `webapp.app.runner` создан на импорте модуля и сюда не
    попадает намеренно: его пул переиспользуют другие тесты, а закрытый пул
    больше не принимает задачи."""
    created = []
    orig_init = runner.JobRunner.__init__

    def tracking_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        created.append(weakref.ref(self))

    monkeypatch.setattr(runner.JobRunner, "__init__", tracking_init)
    yield

    stuck = []
    for ref in created:
        r = ref()
        if r is None:
            continue
        for sid in list(r._timers):        # снять взведённые watchdog-таймеры
            r._disarm_watchdog(sid)
        stuck.extend(drain_pool(r._pool))
    if stuck:
        pytest.fail(
            "тест оставил незавершённые воркеры JobRunner: "
            f"{[t.name for t in stuck]}. Такой поток переживает тест и ломает "
            "изоляцию соседних (см. модульный docstring conftest).")
