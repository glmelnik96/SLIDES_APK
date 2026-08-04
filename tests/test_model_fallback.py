"""Недоступность модели: быстрый отказ, автофолбэк на резерв, честный провал.

Регрессия прод-инцидента 2026-08-04: MiniMax-M3 перестала отвечать на стороне
Cloud.ru, и пайплайн этого НЕ заметил — каждый вызов висел до таймаута
(180 с × 3 ретрая), каждый раздел деградировал в заглушку, а фолбэка на другую
модель не существовало вовсе. Две сборки ползли по 17-18 минут и были отменены
пользователем. Проба с прода в тот же час: MiniMax — таймаут, Kimi-K2.6 — 1,9 с.
"""
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError

from htmlslides.pipeline import client as clientmod
from htmlslides.pipeline.client import (FALLBACK_MODEL, KimiClient,
                                        ProviderUnavailable)

REQ = httpx.Request("POST", "http://x")


def _resp(text='{"ok": true}'):
    choice = SimpleNamespace(message=SimpleNamespace(content=text),
                             finish_reason="stop")
    return SimpleNamespace(choices=[choice], usage=None)


class _Transport:
    """Дублёр openai-клиента: любая модель из `dead` отвечает таймаутом.

    Помнит, с какими опциями его просили клонировать (`with_options`) — так тест
    видит, что проба идёт с коротким таймаутом, а не с боевым 180 × 3.
    """

    def __init__(self, dead=(), fail_first=0):
        self.dead = set(dead)
        self.fail_first = fail_first       # сколько первых вызовов уронить
        self.models = []                   # модели по порядку вызовов
        self.options = []                  # аргументы with_options

    def with_options(self, **kw):
        self.options.append(kw)
        return self

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, *, model, **_kw):
        self.models.append(model)
        if model in self.dead:
            raise APITimeoutError(request=REQ)
        if self.fail_first > 0:
            self.fail_first -= 1
            raise APITimeoutError(request=REQ)
        return _resp()


def _client(transport, **kw):
    return KimiClient(transport=transport, model="primary/M", **kw)


# ── preflight: узнать о мёртвой модели за секунды, а не за 40 минут ───────────

def test_preflight_is_quiet_and_keeps_model_when_provider_is_healthy():
    t = _Transport()
    c = _client(t)
    notices = []
    c.preflight(notice=notices.append)
    assert c.model == "primary/M"
    assert notices == []
    assert t.models == ["primary/M"]        # ровно одна дешёвая проба


def test_preflight_switches_to_fallback_when_primary_is_dead():
    """Главный сценарий инцидента: основная молчит, резерв жив."""
    t = _Transport(dead={"primary/M"})
    c = _client(t)
    notices = []
    c.preflight(notice=notices.append)
    assert c.model == FALLBACK_MODEL
    assert t.models == ["primary/M", FALLBACK_MODEL]
    # Пользователь обязан узнать о подмене: префикс notice: UI показывает дословно.
    assert len(notices) == 1
    assert notices[0].startswith("notice:")
    assert FALLBACK_MODEL in notices[0]


def test_preflight_probe_is_short_and_without_retries():
    """Проба обязана быть дешёвой: боевые 180 с × 3 ретрая на пробе означали бы
    9 минут ожидания ровно там, где мы хотим узнать о сбое мгновенно."""
    t = _Transport()
    _client(t).preflight()
    assert t.options, "проба должна клонировать клиент со своими опциями"
    assert t.options[0]["timeout"] <= 60
    assert t.options[0]["max_retries"] == 0


def test_preflight_raises_when_both_models_are_dead():
    """Обе модели легли — честный отказ ДО планирования, а не 40 минут впустую."""
    t = _Transport(dead={"primary/M", FALLBACK_MODEL})
    with pytest.raises(ProviderUnavailable):
        _client(t).preflight()


def test_preflight_raises_when_fallback_disabled():
    t = _Transport(dead={"primary/M"})
    with pytest.raises(ProviderUnavailable):
        _client(t, fallback_model="").preflight()


def test_preflight_probe_budget_survives_reasoning_models():
    """MiniMax всегда эмитит reasoning перед content: проба с max_tokens<128
    вернула бы пустой ответ и объявила живую модель мёртвой."""
    assert clientmod.PROBE_MAX_TOKENS >= 256


# ── переключение по ходу сборки: деградация НАЧАЛАСЬ после пробы ──────────────

def test_streak_of_transient_failures_switches_model_mid_build():
    t = _Transport(fail_first=clientmod.SWITCH_AFTER)
    c = _client(t)
    notices = []
    c.on_notice = notices.append
    for _ in range(clientmod.SWITCH_AFTER):
        with pytest.raises(APITimeoutError):
            c.chat([{"role": "user", "content": "hi"}])
    assert c.model == FALLBACK_MODEL
    assert len(notices) == 1


def test_single_blip_does_not_switch_the_model():
    """Одиночный блип штатно стоит одного слайда — менять из-за него модель на
    всю деку было бы хуже проблемы."""
    t = _Transport(fail_first=1)
    c = _client(t)
    with pytest.raises(APITimeoutError):
        c.chat([{"role": "user", "content": "hi"}])
    c.chat([{"role": "user", "content": "hi"}])          # успех — серия сброшена
    assert c.model == "primary/M"


def test_success_resets_the_failure_streak():
    t = _Transport()
    c = _client(t)
    for _ in range(clientmod.SWITCH_AFTER - 1):
        t.fail_first = 1
        with pytest.raises(APITimeoutError):
            c.chat([{"role": "user", "content": "hi"}])
        c.chat([{"role": "user", "content": "hi"}])
    assert c.model == "primary/M"


def test_switch_happens_once_no_flapping():
    """Липкое переключение: назад не возвращаемся и второй раз не объявляем —
    иначе на деградирующем шлюзе клиент метался бы между моделями."""
    t = _Transport(dead={"primary/M", FALLBACK_MODEL})
    c = _client(t)
    notices = []
    c.on_notice = notices.append
    for _ in range(clientmod.SWITCH_AFTER * 3):
        with pytest.raises(APITimeoutError):
            c.chat([{"role": "user", "content": "hi"}])
    assert c.model == FALLBACK_MODEL
    assert len(notices) == 1


# ── правило «дека из заглушек» ────────────────────────────────────────────────

def test_degradation_is_total_needs_more_than_half():
    assert not clientmod.degradation_is_total(1, 2)   # блип на одном слайде — норма
    assert clientmod.degradation_is_total(2, 3)
    assert clientmod.degradation_is_total(1, 1)
    assert not clientmod.degradation_is_total(0, 0)


def test_planner_total_api_outage_fails_instead_of_stub_deck():
    """Если разделы деградировали ИЗ-ЗА сбоя API, планировать дальше нечего:
    впереди фаза fill (ещё вызов на слайд), и она отдаст дека-заглушку молча."""
    from htmlslides.library import TemplateLibrary
    from htmlslides.parsers.base import InputDoc, Section, TextBlock
    from htmlslides.pipeline import planner
    from tests.test_planner import FakeClient

    doc = InputDoc(title="T", sections=[
        Section(heading=f"Раздел {i}", level=2, blocks=[TextBlock(text="текст")])
        for i in range(3)])
    client = FakeClient([APITimeoutError(request=REQ)] * 3)
    with pytest.raises(ProviderUnavailable):
        planner.plan_deck(client, doc, TemplateLibrary.load())


def test_planner_bad_json_still_degrades_quietly():
    """Модель ОТВЕЧАЕТ, просто мусором — это не отказ провайдера. Эвристический
    фолбэк планировщика несёт дословный текст раздела, дека остаётся осмысленной."""
    from htmlslides.library import TemplateLibrary
    from htmlslides.parsers.base import InputDoc, Section, TextBlock
    from htmlslides.pipeline import planner
    from htmlslides.pipeline.client import LLMFormatError
    from tests.test_planner import FakeClient

    doc = InputDoc(title="T", sections=[
        Section(heading=f"Раздел {i}", level=2, blocks=[TextBlock(text="текст")])
        for i in range(3)])
    client = FakeClient([LLMFormatError("no JSON")] * 3)
    plan = planner.plan_deck(client, doc, TemplateLibrary.load())
    assert len(plan.slides) == 4                       # cover + 3 раздела
    assert "текст" in plan.slides[1].content["brief"]


def test_filler_total_api_outage_fails_instead_of_blank_deck():
    """Заглушка филлера — пустой blank с одним заголовком. Отдать деку из таких
    под видом успеха нельзя: пользователь не узнает, что случилось."""
    from htmlslides.library import TemplateLibrary
    from htmlslides.models import DeckPlan, SlidePlan
    from htmlslides.pipeline import filler

    plan = DeckPlan(title="T", slides=[
        SlidePlan(index=i, type="content", template_id="statement")
        for i in range(1, 4)])

    def boom(client, lib, slide, *, deck_title=""):
        raise APITimeoutError(request=REQ)

    orig = filler.fill_slide
    filler.fill_slide = boom
    try:
        with pytest.raises(ProviderUnavailable):
            filler.fill_deck(object(), TemplateLibrary.load(), plan, workers=1)
    finally:
        filler.fill_slide = orig


# ── сборка зовёт пробу ДО работы и показывает подмену пользователю ────────────

class _PreflightClient:
    """Клиент, у которого работает только preflight — до остального дело не дойдёт."""

    model = "primary/M"

    def __init__(self, dead=False):
        self.dead = dead
        self.notice = None

    def preflight(self, notice=None):
        self.notice = notice
        if self.dead:
            raise ProviderUnavailable("нет живых моделей")


def test_build_preflights_before_touching_the_document():
    """Проба обязана быть ПЕРВЫМ действием: смысл её в том, чтобы отказ стоил
    полминуты. После парса и рендера исходника экономить уже нечего."""
    from htmlslides.pipeline import build

    with pytest.raises(ProviderUnavailable):
        build.build_deck("нет-такого-файла.md", "out.html",
                         client=_PreflightClient(dead=True))


def test_build_wires_notice_to_progress():
    """Подмену модели пользователь видит в прогрессе сборки, а не в журнале."""
    from htmlslides.pipeline import build

    c = _PreflightClient()
    with pytest.raises(Exception):            # дальше упадёт на отсутствующем файле
        build.build_deck("нет-такого-файла.md", "out.html", client=c,
                         progress=lambda m: None)
    assert callable(c.notice), "preflight должен получить progress как notice"
