"""Полнота эталонной деки: перебирает ли она все ветвления каталога.

Зачем именно такая проверка. Промах по таблице сегодня означает потерянный
слайд: convert.add_slide() на неизвестном ключе просто выходит. А таблица
снимается с refdeck.candidates(), то есть с НАШЕГО перебора осей — и если ось
забыта, то забыта она одинаково и в таблице, и в любом тесте, который сверяет
таблицу с перебором. Такой тест сошёлся бы сам с собой и ничего не поймал.

Поэтому проверка идёт от другого источника — от самих шаблонов. Ветвление в
каталоге выглядит как `{% if content.X %}`, и это единственный способ получить
другое дерево из того же шаблона. Тест вычитывает все такие X прямо из разметки
и требует, чтобы каждый был осью перебора. Именно так и находится пропуск:
слот image был исключён из осей вручную, и timeline со service-table со своей
картинкой пользователя пропадали из PPTX молча.
"""
import os
import re

os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from pathlib import Path

import pytest

from htmlslides.topptx import refdeck

SLIDES = Path(__file__).resolve().parents[1] / "htmlslides" / "templates" / "slides"

# `{% if content.x %}`, `{%- if content.x %}`, а также внутри уже открытого
# выражения (`{% if content.a and content.b %}`) — отсюда поиск по всему тексту
# тега, а не только по его началу.
_COND = re.compile(r"{%-?\s*(?:el)?if\s[^%]*?\bcontent\.(\w+)")


@pytest.fixture(scope="module")
def picked():
    """Отбор деревьев — один раз на модуль: это полный проход Jinja по каталогу."""
    return refdeck.unique_variants("dark")


def _branching_slots() -> dict[str, set[str]]:
    """{id шаблона: слоты, на которых он ветвится} — прямо из разметки."""
    out: dict[str, set[str]] = {}
    for path in sorted(SLIDES.glob("*.html")):
        if path.name.startswith("_"):
            continue                       # партиал, а не шаблон слайда
        found = set(_COND.findall(path.read_text(encoding="utf-8")))
        if found:
            out[path.stem] = found
    return out


def test_the_markup_does_branch_on_content():
    """Страховка от «тест ничего не нашёл и потому прошёл»."""
    slots = _branching_slots()
    assert len(slots) >= 10
    # Два случая, из-за которых тест и появился.
    assert "image" in slots["timeline"]
    assert "image" in slots["service-table"]


def test_every_branching_slot_is_an_axis_of_the_reference_deck():
    """Каждое ветвление шаблона перебирается, иначе дерева нет в таблице.

    Три законных способа покрыть ветвление:
      * слот обязателен — тогда он всегда непуст и ветка константна;
      * слот списочный, и в его длинах есть ноль — пустой список даёт ту же
        ветку `{% else %}`, что и отсутствие слота;
      * слот необязательный и верхнего уровня — его перебирает _optional_top().
    """
    from htmlslides.library import TemplateLibrary

    lib = TemplateLibrary.load()
    specs = {s.id: s for s in lib.templates}
    missed: list[str] = []

    for template, slots in _branching_slots().items():
        spec = specs.get(template)
        if spec is None:
            continue              # шаблон вне каталога — не наша забота
        axis = set(refdeck._optional_top(spec))
        for name in slots:
            slot = spec.slots.get(name)
            if slot is None:
                continue          # ветвление по чему-то, чего в контракте нет
            if slot.required:
                continue
            if slot.kind == "list":
                assert 0 in refdeck._cardinalities(slot), (
                    f"{template}.{name}: списочный слот без нулевой длины — "
                    "ветка `{% else %}` не калибруется")
                continue
            if name not in axis:
                missed.append(f"{template}.{name}")

    assert not missed, (
        "ветвление шаблона не перебирается, значит такого дерева нет в "
        f"geometry.json и слайд потеряется: {sorted(missed)}")


def test_the_image_placeholder_is_a_readable_picture():
    """Заглушка image должна быть настоящей картинкой, а не любой строкой.

    У timeline значение идёт прямо в src, и на битой ссылке браузер померил бы
    рамку сломанного изображения — то есть калибровка записала бы не ту
    геометрию, причём молча.
    """
    import base64
    import io

    from PIL import Image

    head, _, payload = refdeck._IMAGE.partition(",")
    assert head == "data:image/png;base64"
    with Image.open(io.BytesIO(base64.b64decode(payload))) as im:
        assert im.format == "PNG"
        assert im.size == (1, 1)


def test_the_image_axis_yields_two_trees_where_the_markup_branches(picked):
    """Ось работает: у ветвящихся шаблонов появляется второе дерево.

    Проверка на результат, а не на наличие кода: ось можно объявить и при этом
    получить одно и то же дерево (так и выходит у cover-image, где разметка на
    картинке не ветвится, — там второго дерева быть и не должно).
    """
    from collections import Counter

    trees = Counter(k.split("|")[0] for k, _, _ in picked)
    assert trees["timeline"] == 20          # было 10 до появления оси
    assert trees["service-table"] == 24     # было 12
    assert trees["cover-image"] == 1        # разметка не ветвится — дерево одно


def test_levers_reach_sub_slots_and_not_just_top_level():
    """Рычаг обязан быть на под-слот: на слоте целиком подбор не работает.

    У two-col-cards ветвление читает `card.body`, а ширину вкладки задаёт
    `card.heading`. Тронув `cards` целиком, подбор менял бы оба сразу и всегда
    уезжал бы в другое дерево — то есть не находил бы ничего.
    """
    levers = refdeck._levers({"title": "Заголовок",
                              "cards": [{"heading": "А", "body": "Б"},
                                        {"heading": "В"}]})
    assert ("title",) in levers
    assert ("cards", "heading") in levers
    assert ("cards", "body") in levers
    assert ("cards",) not in levers, "рычаг на слоте целиком меняет всё сразу"


def test_pull_swaps_only_the_named_sub_slot_in_every_item():
    """Перенос точечный и неразрушающий: базовый контент остаётся как был."""
    base = {"title": "кор", "cards": [{"heading": "кор", "body": "кор"},
                                      {"heading": "кор", "body": "кор"}]}
    donor = {"title": "длинный", "cards": [{"heading": "длинный", "body": "длинный"},
                                           {"heading": "длинный", "body": "длинный"}]}
    got = refdeck._pull(base, donor, [("cards", "heading")])

    assert got["title"] == "кор"                      # не тронут
    assert [c["heading"] for c in got["cards"]] == ["длинный", "длинный"]
    assert [c["body"] for c in got["cards"]] == ["кор", "кор"]
    # База не изменилась — иначе следующий рычаг пробовался бы на испорченном.
    assert base["cards"][0]["heading"] == "кор"


def test_no_tree_is_left_without_a_partner(picked):
    """Без партнёра рамку по тексту не отличить от растянутой — ни одной такой.

    Это не косметика. У дерева без партнёра content_sized делает
    `if not peers: continue`, в таблицу не уходит ни `aw`, ни `fh`, и промах не
    видит даже --check-reflow: он тоже сравнивает пару. На четырёх таких
    деревьях two-col-cards fidelity давал расхождения до 192px.
    """
    lonely = [(key, head.label) for key, head, rest in picked if not rest]
    assert lonely == [], f"деревья без партнёра: {lonely[:5]}"


def test_the_pairing_really_fires_and_keeps_the_tree_intact(picked):
    """Партнёр подобран, у него ТОТ ЖЕ ключ и ДРУГОЙ текст.

    Страховка от двух способов пройти впустую: подбор мог ни разу не
    вызваться (тогда предыдущий тест говорит лишь, что профилей хватило), и
    подбор мог вернуть копию головы (ключ совпал бы, а второго замера нет).
    """
    paired = [(key, head, v) for key, head, rest in picked
              for v in rest if v.profile == "пара"]
    assert paired, "подбор партнёра ни разу не сработал — тест выше пустой"

    keys = refdeck._keys_of([v for _k, _h, v in paired], "dark")
    for (key, head, var), got in zip(paired, keys):
        assert got == key, f"партнёр уехал в другое дерево: {head.label}"
        assert var.content != head.content, f"партнёр — копия головы: {head.label}"


def test_the_partner_lengthens_the_heading_but_not_the_branching_body(picked):
    """Тот самый случай, из-за которого подбор появился.

    Стрелка в карточке two-col-cards рисуется только при коротком теле, и это
    дерево существует ровно в одном профиле. Нужное дерево — полный заголовок
    ПРИ коротком теле — равномерной стрижкой не получается по построению.
    """
    for key, head, rest in picked:
        if not key.startswith("two-col-cards|"):
            continue
        pair = [v for v in rest if v.profile == "пара"]
        if not pair:
            continue
        cards = zip(head.content["cards"], pair[0].content["cards"])
        for was, now in cards:
            assert now["heading"] != was["heading"], "заголовок не удлинён"
            assert now["body"] == was["body"], (
                "тело удлинено — стрелка исчезнет и дерево сменится")
        return
    pytest.fail("ни один вариант two-col-cards не получил партнёра")
