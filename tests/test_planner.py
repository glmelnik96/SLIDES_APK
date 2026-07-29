"""Planner map→reduce: текстовый вход планируется по разделам параллельно, затем
структура собирается в коде. Регрессия для прод-падения `LLMFormatError: no JSON
object`, которое роняло ВЕСЬ билд: теперь сбой раздела изолирован (эвристический
фолбэк), а cover/разнообразие/accent детерминированы кодом."""
import base64

import pytest

from htmlslides.library import TemplateLibrary
from htmlslides.models import DeckPlan, SlidePlan
from htmlslides.parsers.base import (InputDoc, ListBlock, Section, TableBlock,
                                     TextBlock)
from htmlslides.pipeline import planner
from htmlslides.pipeline.client import LLMFormatError
from htmlslides.pipeline.planner import (_fallback_template, _SectionPlan,
                                         _SectionSlide)


def _doc():
    return InputDoc(title="Тест-дека", sections=[
        Section(heading="Преимущества", level=2, blocks=[
            ListBlock(items=["A — раз", "B — два", "C — три"])]),
        Section(heading="Безопасность", level=2, blocks=[
            TextBlock(text="Соответствие 152-ФЗ и 187-ФЗ.")]),
    ])


class FakeClient:
    """chat_json возвращает заранее заданные _SectionPlan по очереди вызовов;
    значение-исключение бросается (эмуляция транзиентного сбоя LLM)."""
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0

    def chat_json(self, messages, model_cls, *, max_tokens=4096, retries=2,
                  extra_body=None):
        self.calls += 1
        r = self._replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _sp(template_id, brief="факт"):
    return _SectionPlan(slides=[_SectionSlide(template_id=template_id, brief=brief)])


def test_text_path_maps_sections_and_wraps_cover():
    lib = TemplateLibrary.load()
    client = FakeClient([_sp("three-col"), _sp("statement")])
    plan = planner.plan_deck(client, _doc(), lib)
    ids = [s.template_id for s in plan.slides]
    assert ids[0] == "cover"
    assert "contacts" not in ids            # контакты больше не прикрепляются автоматически
    assert "three-col" in ids and "statement" in ids
    assert [s.index for s in plan.slides] == list(range(1, len(plan.slides) + 1))
    assert client.calls == 2                      # один вызов на раздел


def test_section_failure_falls_back_not_aborts():
    """Сбойный раздел не роняет деку — деградирует на эвристический шаблон."""
    lib = TemplateLibrary.load()
    client = FakeClient([LLMFormatError("no JSON object"), _sp("statement")])
    plan = planner.plan_deck(client, _doc(), lib)
    # деки собралась: cover + 2 контентных (без авто-contacts)
    assert len(plan.slides) == 3
    assert plan.slides[0].template_id == "cover"
    # упавший раздел (3 пункта списка) ушёл на эвристику three-col, брифом — текст раздела
    assert plan.slides[1].template_id == "three-col"
    assert "раз" in plan.slides[1].content["brief"]


def test_variety_swaps_adjacent_duplicate():
    lib = TemplateLibrary.load()
    # оба раздела просят cards-6 подряд → второй должен свапнуться на альтернативу
    client = FakeClient([_sp("cards-6"), _sp("cards-6")])
    plan = planner.plan_deck(client, _doc(), lib)
    content = [s.template_id for s in plan.slides if s.template_id != "cover"]
    assert content[0] == "cards-6"
    assert content[1] != "cards-6"                # разнообразие
    assert content[1] in planner._VARIETY_SWAP["cards-6"]


def test_accent_on_first_statement_only():
    lib = TemplateLibrary.load()
    client = FakeClient([_sp("statement"), _sp("statement")])
    plan = planner.plan_deck(client, _doc(), lib)
    accented = [s for s in plan.slides if isinstance(s.content, dict)
                and s.content.get("accent")]
    assert len(accented) == 1
    assert accented[0].template_id == "statement"


def test_unknown_template_id_falls_back():
    lib = TemplateLibrary.load()
    client = FakeClient([_sp("totally-made-up"), _sp("statement")])
    plan = planner.plan_deck(client, _doc(), lib)
    # неизвестный id заменён эвристикой (3 пункта → three-col), деки валидна
    assert plan.slides[1].template_id in {t.id for t in lib.templates}


def test_empty_plan_is_impossible():
    """DeckPlan всегда непуст: даже без разделов есть хотя бы cover."""
    lib = TemplateLibrary.load()
    client = FakeClient([])
    plan = planner.plan_deck(client, InputDoc(title="Пусто", sections=[]), lib)
    assert [s.template_id for s in plan.slides] == ["cover"]
    assert isinstance(plan, DeckPlan)


def _sec(blocks):
    return Section(heading="Раздел", level=2, blocks=blocks)


def test_fallback_is_data_aware():
    """Регрессия: при фолбэке раздела чарты/диаграммы/таблицы НЕ должны пропадать —
    эвристика выбирает профильный data-шаблон по содержимому, а не только текст."""
    lib = TemplateLibrary.load()
    assert _fallback_template(
        _sec([TableBlock(rows=[["a", "b"], ["1", "2"]])]), lib) == "service-table"
    assert _fallback_template(
        _sec([TextBlock(text="Аптайм 99.9%, покрытие 80%, рост 50%")]), lib) == "donut-chart"
    assert _fallback_template(
        _sec([TextBlock(text="18 млрд выручка, 500 ГБ, 3 раза рост")]), lib) == "stats-row"
    assert _fallback_template(
        _sec([TextBlock(text="было 10 млрд, стало 18 млрд")]), lib) == "bar-chart"
    # без данных — прежнее текстовое поведение
    assert _fallback_template(
        _sec([ListBlock(items=["a", "b", "c"])]), lib) == "three-col"
    assert _fallback_template(
        _sec([TextBlock(text="Просто мысль без чисел.")]), lib) == "statement"


def test_failed_numeric_section_yields_chart_not_text():
    """Сквозной: раздел с числами, у которого LLM-вызов упал, всё равно даёт чарт."""
    lib = TemplateLibrary.load()
    doc = InputDoc(title="D", sections=[
        _sec([TextBlock(text="Доступность 99.95%, покрытие 100%, готовность 70%")])])
    client = FakeClient([LLMFormatError("no JSON object")])
    plan = planner.plan_deck(client, doc, lib)
    assert plan.slides[1].template_id == "donut-chart"   # не текстовый шаблон


# ===================== Разделители секций (дивайдеры) =====================

def test_part_title_sections_become_dividers():
    """Раздел-«шапка части» (заголовок без тела) -> детерминированный разделитель,
    а не титул; LLM для него не зовётся, нумерация 01/02, макеты чередуются."""
    lib = TemplateLibrary.load()
    doc = InputDoc(title="D", sections=[
        Section(heading="Часть 1", level=1, blocks=[]),
        Section(heading="Обзор", level=2, blocks=[TextBlock(text="Обзор рынка.")]),
        Section(heading="Часть 2", level=1, blocks=[]),
        Section(heading="Итоги", level=2, blocks=[TextBlock(text="Выводы по проекту.")]),
    ])
    client = FakeClient([_sp("statement"), _sp("statement")])
    plan = planner.plan_deck(client, doc, lib)
    ids = [s.template_id for s in plan.slides]
    assert client.calls == 2                       # шапки частей LLM не трогают
    assert ids[0] == "cover" and "contacts" not in ids
    dots = next(s for s in plan.slides if s.template_id == "section-dots")
    frame = next(s for s in plan.slides if s.template_id == "section-frame")
    assert dots.content == {"label": "ЧАСТЬ 1", "number": "01"}
    assert frame.content == {"label": "ЧАСТЬ 2", "number": "02"}
    # разделитель заполнен детерминированно — без ключа brief (филлер его пропустит)
    assert "brief" not in dots.content
    assert not lib.validate_content("section-dots", dots.content)


def test_top_level_sections_with_subsections_get_dividers():
    """Главы верхнего уровня, у которых есть вложенные подразделы, получают
    разделитель перед своим контентом — распределение по структуре."""
    lib = TemplateLibrary.load()
    doc = InputDoc(title="D", sections=[
        Section(heading="Инфраструктура", level=1, blocks=[TextBlock(text="Введение.")]),
        Section(heading="Сеть", level=2, blocks=[TextBlock(text="Про сеть.")]),
        Section(heading="Хранилище", level=2, blocks=[TextBlock(text="Про диски.")]),
        Section(heading="Безопасность", level=1, blocks=[TextBlock(text="Введение 2.")]),
        Section(heading="Доступ", level=2, blocks=[TextBlock(text="Про доступ.")]),
    ])
    client = FakeClient([_sp("statement")] * 5)
    plan = planner.plan_deck(client, doc, lib)
    ids = [s.template_id for s in plan.slides]
    assert ids.count("section-dots") + ids.count("section-frame") == 2
    assert client.calls == 5                       # у всех 5 разделов есть контент
    # дивайдер стоит ПЕРЕД контентом своей главы
    assert ids[1] in ("section-dots", "section-frame")


def test_flat_doc_gets_no_dividers():
    """Плоский документ (все разделы одного уровня с контентом) — без разделителей."""
    lib = TemplateLibrary.load()
    doc = InputDoc(title="D", sections=[
        Section(heading="A", level=2, blocks=[TextBlock(text="Текст A.")]),
        Section(heading="B", level=2, blocks=[TextBlock(text="Текст B.")]),
        Section(heading="C", level=2, blocks=[TextBlock(text="Текст C.")]),
    ])
    client = FakeClient([_sp("statement")] * 3)
    plan = planner.plan_deck(client, doc, lib)
    ids = [s.template_id for s in plan.slides]
    assert "section-dots" not in ids and "section-frame" not in ids


def test_single_divider_is_suppressed_and_section_survives():
    """Один-единственный кандидат подавляется (одинокий дивайдер бессмыслен), но
    сам раздел не теряется — уходит в контент как раньше."""
    lib = TemplateLibrary.load()
    doc = InputDoc(title="D", sections=[
        Section(heading="Часть 1", level=1, blocks=[]),
        Section(heading="Обзор", level=2, blocks=[TextBlock(text="Обзор.")]),
    ])
    client = FakeClient([_sp("statement"), _sp("statement")])
    plan = planner.plan_deck(client, doc, lib)
    ids = [s.template_id for s in plan.slides]
    assert "section-dots" not in ids and "section-frame" not in ids
    assert client.calls == 2                       # оба раздела ушли в body
    assert len(plan.slides) == 3                   # cover + 2, ничего не пропало


def test_divider_label_trims_on_word_boundary():
    lib = TemplateLibrary.load()
    # section-frame label max_chars=14 → режем по слову, капсом
    assert planner._divider_label("Информационная безопасность", 14) == "ИНФОРМАЦИОННАЯ"
    assert planner._divider_label("Часть 1", 16) == "ЧАСТЬ 1"


# ============ Точность: brief несёт исходник, rebrand — map+reduce ============
#
# Замер прод-кейсов 2026-07-29: pptx-rebrand удерживал 31–66% фактов исходника.
# Две причины: (1) filler видел ТОЛЬКО пересказ-brief планировщика — исходный
# текст доезжал лишь при пустом brief; (2) rebrand планировался монолитным
# vision-вызовом на всю деку (16K токенов на 39 слайдов ≈ по абзацу на слайд).

class CapturingClient(FakeClient):
    """FakeClient, дополнительно запоминающий (messages, model_cls) вызовов."""
    def __init__(self, replies):
        super().__init__(replies)
        self.seen = []

    def chat_json(self, messages, model_cls, *, max_tokens=4096, retries=2,
                  extra_body=None):
        self.seen.append((messages, model_cls))
        return super().chat_json(messages, model_cls, max_tokens=max_tokens,
                                 retries=retries, extra_body=extra_body)


def _pngs(tmp_path, n):
    paths = []
    for i in range(n):
        p = tmp_path / f"slide{i}.png"
        p.write_bytes(b"PNG-BYTES-%d" % i)
        paths.append(p)
    return paths


def _b64(path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _image_urls(messages) -> list[str]:
    content = messages[-1]["content"]
    if isinstance(content, str):
        return []
    return [part["image_url"]["url"] for part in content
            if part.get("type") == "image_url"]


def test_brief_always_carries_source_text():
    """Brief = пересказ модели + ВСЕГДА дословный текст раздела: filler видит
    конкретику исходника, а не только сжатый пересказ планировщика."""
    lib = TemplateLibrary.load()
    client = FakeClient([_sp("three-col", brief="пересказ-1"),
                         _sp("statement", brief="пересказ-2")])
    plan = planner.plan_deck(client, _doc(), lib)
    briefs = " ".join(s.content["brief"] for s in plan.slides[1:])
    assert "пересказ-1" in briefs and "пересказ-2" in briefs
    assert "A — раз" in briefs                    # исходник доехал дословно
    assert "152-ФЗ" in briefs


def test_rebrand_aligned_images_use_map_reduce(tmp_path):
    """Скриншоты 1:1 с разделами → per-section map+reduce вместо монолита:
    вызов на раздел, модель _SectionPlan (не DeckPlan)."""
    lib = TemplateLibrary.load()
    imgs = _pngs(tmp_path, 2)
    client = CapturingClient([_sp("three-col"), _sp("statement")])
    plan = planner.plan_deck(client, _doc(), lib, slide_images=imgs)
    assert client.calls == 2
    assert all(mc is _SectionPlan for _, mc in client.seen)
    assert plan.slides[0].template_id == "cover"
    # каждый map-вызов несёт ровно один скриншот; оба скриншота использованы
    per_call = [_image_urls(m) for m, _ in client.seen]
    assert all(len(urls) == 1 for urls in per_call)
    assert {urls[0] for urls in per_call} == \
        {f"data:image/png;base64,{_b64(p)}" for p in imgs}


def test_rebrand_map_call_gets_its_own_screenshot(tmp_path):
    """Порядок сохранён: i-й раздел получает i-й скриншот (workers=1 для
    детерминизма), плюс rebrand-примечание в тексте вызова."""
    lib = TemplateLibrary.load()
    imgs = _pngs(tmp_path, 2)
    client = CapturingClient([_sp("three-col"), _sp("statement")])
    planner._plan_deck_text(client, _doc(), lib, freeform_ok=False,
                            workers=1, images=imgs)
    for (messages, _), img in zip(client.seen, imgs):
        urls = _image_urls(messages)
        assert urls == [f"data:image/png;base64,{_b64(img)}"]
        text = messages[-1]["content"][0]["text"]
        assert "скриншот" in text.lower()


def test_rebrand_empty_section_drops_its_screenshot_not_neighbours(tmp_path):
    """Пустой раздел выпадает вместе СО СВОИМ скриншотом — соседние не сдвигаются
    (zip ДО фильтра _has_content)."""
    lib = TemplateLibrary.load()
    doc = InputDoc(title="D", sections=[
        Section(heading="A", level=1, blocks=[TextBlock(text="Текст A.")]),
        Section(heading="", level=1, blocks=[]),           # пустой слайд
        Section(heading="B", level=1, blocks=[TextBlock(text="Текст B.")]),
    ])
    imgs = _pngs(tmp_path, 3)
    client = CapturingClient([_sp("statement"), _sp("statement")])
    planner._plan_deck_text(client, doc, lib, freeform_ok=False,
                            workers=1, images=imgs)
    assert client.calls == 2
    got = [_image_urls(m)[0] for m, _ in client.seen]
    assert got == [f"data:image/png;base64,{_b64(imgs[0])}",
                   f"data:image/png;base64,{_b64(imgs[2])}"]


def test_rebrand_heading_only_slides_are_planned_not_dividerized(tmp_path):
    """В rebrand дивайдеры не синтезируем: слайд-«шапка» исходника планируется
    как обычный слайд (структуру задаёт исходная дека, не наша эвристика)."""
    lib = TemplateLibrary.load()
    doc = InputDoc(title="D", sections=[
        Section(heading="Часть 1", level=1, blocks=[]),
        Section(heading="Обзор", level=1, blocks=[TextBlock(text="Обзор.")]),
        Section(heading="Часть 2", level=1, blocks=[]),
        Section(heading="Итоги", level=1, blocks=[TextBlock(text="Выводы.")]),
    ])
    imgs = _pngs(tmp_path, 4)
    client = CapturingClient([_sp("statement")] * 4)
    plan = planner._plan_deck_text(client, doc, lib, freeform_ok=False,
                                   workers=1, images=imgs)
    assert client.calls == 4                       # все 4 слайда планируются LLM
    ids = [s.template_id for s in plan.slides]
    assert "section-dots" not in ids and "section-frame" not in ids


def test_rebrand_misaligned_images_fall_back_to_monolith(tmp_path):
    """Скриншоты не 1:1 с разделами (рендер отдал не всё) → прежний монолитный
    vision-вызов со всеми скриншотами, а не молчаливый сдвиг визуального ряда."""
    lib = TemplateLibrary.load()
    imgs = _pngs(tmp_path, 3)                      # 3 картинки на 2 раздела
    deck = DeckPlan(title="D", slides=[SlidePlan(
        index=1, type="title", template_id="cover", content={"brief": "D"})])
    client = CapturingClient([deck])
    planner.plan_deck(client, _doc(), lib, slide_images=imgs)
    assert client.calls == 1
    assert client.seen[0][1] is DeckPlan
    assert len(_image_urls(client.seen[0][0])) == 3
