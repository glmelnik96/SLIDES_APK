"""Синтетическая эталонная дека: макеты каталога во всех вариантах раскладки.

Калибровке нужен не смысл, а геометрия, поэтому контент генерируется из самих
слот-контрактов library.json — без обращения к модели, детерминированно и
воспроизводимо.

Что перебираем и почему именно это. Раскладка меняется не только от длин
списков: шаблоны ветвятся на необязательных слотах (нет subtitle — нет и строки
подзаголовка, а значит всё ниже поднимается) и на длине текста (statement.html
при title+highlight > 75 символов добавляет .statement--wide и прячет стрелки).
Поэтому осей четыре:

  * длины списочных слотов — полный декартов перебор;
  * необязательные слоты верхнего уровня — есть/нет, по каждому отдельно
    (их не больше трёх на шаблон, перебор дешёвый). Слот image здесь на общих
    правах, и это не формальность: timeline по `{% if content.image %}` рисует
    совсем другое поддерево, а service-table добавляет .svc-dots — см.
    _optional_top();
  * необязательные слоты внутри элементов списка — есть/нет ОДНИМ флагом на все
    элементы сразу, а не по элементу: по элементу дало бы 2^6 на cards-6, а
    выигрыш покрыл бы случай «у третьей карточки нет заголовка, у остальных
    есть», которого в реальных деках практически не бывает;
  * профиль текста — три штуки, и каждый нужен (см. ниже).

Профили. Нужна пара замеров одного дерева с РАЗНОЙ шириной текста: только по
разнице рамок видно, какая рамка подогнана под текст. Один «короткий текст» на
это не годится, и промахивается он двумя разными способами.

  * `wide` — рыба в полный лимит слота: худший случай, именно он идёт в таблицу.
  * `narrow` — та же строка той же ДЛИНЫ, но из самых узких глифов. Нужен там,
    где шаблон ветвится по длине: statement включает .statement--wide при
    title+highlight > 75 символов, two-col-cards убирает стрелку при body > 200.
    Короткий текст попадает в ДРУГОЕ дерево, и пары у такого ключа не возникает
    вовсе — .statement-title и .tcc-tab оставались худшими расхождениями.
  * `tiny` — четыре символа на слот. Нужен там, где текста много: у .quote-text
    полоса 1620 px, и рыба переносится в ней и в широком, и в узком профиле —
    обе рамки равны полосе, разницы нет, и подогнанность не видна. Чтобы рамка
    сжалась, текст должен стать заведомо короче полосы.

Ни один профиль не лишний и ни один не покрывает другого: narrow сохраняет
ветвления, tiny показывает сжатие. Поэтому в браузер уходит до трёх вариантов
на ключ, а подогнанность считается по ОБЪЕДИНЕНИЮ пар.

Перебор заведомо избыточен: разные комбинации часто дают одно и то же дерево.
Резать его догадками не нужно — есть точная проверка. Вариант рендерится Jinja
(дешёво, без браузера), по разметке считается structure_key, и в браузер уходит
не больше двух вариантов на ключ. Второй оставлен нарочно: в паре «широкий» —
«узкий» ключ один, и проверка совпадения внутри ключа в calibrate.py печатает,
на сколько пикселей ширина текста двигает рамки. Это и есть замер погрешности
таблицы, а не рассуждение о ней.

Непокрытый вариант в рантайме — промах по таблице, и сегодня это значит, что
слайда в PPTX просто НЕ БУДЕТ: convert.add_slide() на неизвестном ключе выходит,
записав строку в stats["missing"]. Браузерный запас пока не сделан, так что
экономить на осях нельзя — цена промаха не «один запуск браузера», а потерянный
слайд. Именно так и пропадали timeline и service-table со своей картинкой
пользователя, пока image не стал осью.
"""
from __future__ import annotations

from itertools import combinations, product
from typing import NamedTuple


class Variant(NamedTuple):
    """Один вариант эталонной деки."""

    template_id: str
    label: str                 # для логов: по нему видно, какой вариант сломался
    content: dict
    profile: str               # wide / narrow / tiny — ось, а не подробность

# Осмысленная русская рыба: короткие частотные слова дают реалистичную плотность
# строки, а значит и реалистичный перенос.
_WORDS = ("облако", "миграция", "данные", "сервис", "платформа", "нагрузка",
          "отказоустойчивость", "резерв", "контур", "сеть", "кластер", "узел",
          "хранилище", "бэкап", "мониторинг", "поддержка", "лицензия", "команда",
          "внедрение", "аудит", "регламент", "контроль", "затраты", "экономия")

# Значения-«герои» для числовых слотов. Длинные диапазоны тут не случайны: это
# ровно тот случай, под который в deck.js:151 написан autofitStats(), и
# калибровка обязана его захватить.
_VALUES = ("41", "285", "17", "92", "3,4", "128")
_DISPLAYS = ("41%", "285 млн ₽", "17 мин", "92%", "3,4x", "−30–45%")

# Глифы узкого профиля: те же по счёту, самые узкие по ширине.
_NARROW_LETTER = "і"        # U+0456, 0.271 em против ~0.55 у средней кириллицы
_NARROW_DIGIT = "1"         # цифры в SB Sans пропорциональные: 0.42 против 0.62

# Профиль tiny: столько символов на слот. Четыре, а не ноль, потому что пустой
# слот — это другое дерево (шаблоны проверяют `{% if content.x %}`), а четыре
# символа гарантированно короче любой полосы в каталоге.
_TINY_CHARS = 4

PROFILES = ("wide", "narrow", "tiny")

# Числовые слоты: им нужна цифра, а не рыба, иначе autofitStats() мерил бы не то.
_NUMERIC = frozenset({"value", "v1", "v2", "v3", "v4", "num", "number"})

# Заглушка для слота image: валидный PNG 1x1 в data: URI. Именно валидный, а не
# любая строка, — у timeline значение идёт прямо в src, и на битой ссылке браузер
# померил бы рамку сломанного изображения. И именно 1x1, а не дежурный ассет
# каталога: ассет весит ~200 КБ, а в эталонной деке таких слайдов десятки.
#
# Размер исходника на замер не влияет, поэтому мельчить безопасно: и .tl-art-img
# (720x480), и .svc-image img (100% от 614x746) заданы в CSS жёстко. Калибруется
# тут ПРИСУТСТВИЕ картинки — то, на чём ветвятся шаблоны, — а пропорции считает
# в рантайме svgshapes.fit_image, по настоящему исходнику.
_IMAGE = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1Pe"
          "AAAADElEQVR42mNoaGgAAAMEAYF1LgG8AAAAAElFTkSuQmCC")


def _text(max_chars: int, seed: int) -> str:
    """Рыба, которая ЗАПОЛНЯЕТ лимит слота, а не просто в него влезает.

    Разница не косметическая, и вот почему. content_sized отличает рамку,
    подогнанную под текст, от растянутой по одному признаку: разошлась ли её
    ширина между профилями. У .tcc-tab стоит width:fit-content с
    min-width:299px, а слот heading разрешает 14 символов. Короткая рыба
    («Облако», 6 символов) прижимала вкладку к этому минимуму во ВСЕХ профилях
    — ширина не расходилась ни разу, рамка записывалась как растянутая, и `aw`
    в таблицу не попадал. Между тем настоящий заголовок в разрешённые 14
    символов занимает 304px, то есть минимум перебивает, и починить это
    рантайму было уже нечем: промах 95px по ширине вкладки и, через лишний
    перенос, 48px по высоте заголовка.

    Отсюда правило: слово, которое не влезло, набор не обрывает — ищем
    следующее, которое влезет. Недобор остаётся меньше самого короткого слова
    в пуле, то есть 4 символов. Обрывать на первом же промахе нельзя именно
    потому, что лимит слота — это и есть тот худший случай, под который надо
    калиброваться (ровно так уже работает _pick для значений-диапазонов).
    """
    limit = max_chars if max_chars > 0 else 60
    words, total = [], 0
    i, missed = seed, 0
    # Останов гарантирован: успех съедает минимум 4 символа конечного лимита, а
    # полный круг по пулу без единого попадания означает, что места больше нет.
    while missed < len(_WORDS):
        word = _WORDS[i % len(_WORDS)]
        need = len(word) + (1 if words else 0)
        if total + need <= limit:
            words.append(word)
            total += need
            missed = 0
        else:
            missed += 1
        i += 1
    if not words:                                 # лимит короче любого слова
        return _WORDS[seed % len(_WORDS)][:limit]
    text = " ".join(words)
    return text[0].upper() + text[1:]


def _pick(pool: tuple[str, ...], max_chars: int, seed: int) -> str:
    """Самое длинное значение из пула, которое влезает в лимит слота.

    Калибровке нужен худший случай, поэтому берём не первое подходящее, а самое
    длинное: именно на длинных значениях-диапазонах срабатывает autofitStats().
    """
    limit = max_chars if max_chars > 0 else max(len(v) for v in pool)
    fits = [v for v in pool if len(v) <= limit]
    if not fits:
        return pool[seed % len(pool)][:limit]
    rotated = fits[seed % len(fits):] + fits[:seed % len(fits)]
    return max(rotated, key=len)


def retext(value, profile: str):
    """Перевести готовый контент в другой профиль текста. Рекурсивно.

    Работаем по уже собранному дереву контента, а не по слот-контрактам: так
    профили отличаются ТОЛЬКО текстом, и ни один из них не может случайно
    оказаться другой раскладкой (другого числа элементов, другого набора
    слотов). Это важнее краткости — вся ценность пары в том, что дерево одно.

    narrow: буквы и цифры заменяются на самые узкие, длина сохраняется символ в
    символ, а с ней и все ветвления шаблонов по `| length`.
    tiny: строка режется до четырёх символов. Длина меняется — значит ветвление
    может перескочить, и тогда у ключа просто не будет этой половины пары;
    на этот случай и существует narrow.
    """
    if isinstance(value, str):
        if profile == "tiny":
            return value[:_TINY_CHARS].strip() or value[:_TINY_CHARS]
        return "".join(_NARROW_DIGIT if ch.isdigit()
                       else _NARROW_LETTER if ch.isalpha()
                       else ch for ch in value)
    if isinstance(value, list):
        return [retext(v, profile) for v in value]
    if isinstance(value, dict):
        return {k: retext(v, profile) for k, v in value.items()}
    return value


def _item(item_slots: dict, seed: int, *, extras: bool) -> dict:
    """Один элемент списка.

    extras=False выбрасывает необязательные слоты элемента — это отдельная ось
    раскладки: без .c6-name карточка внутри устроена иначе.
    """
    out = {}
    for pos, (name, spec) in enumerate(item_slots.items()):
        if not spec.required and not extras:
            continue
        if name in _NUMERIC:
            out[name] = _pick(_VALUES, spec.max_chars, seed + pos)
        elif name == "display":
            out[name] = _pick(_DISPLAYS, spec.max_chars, seed + pos)
        else:
            out[name] = _text(spec.max_chars, seed + pos)
    return out


def _cardinalities(spec) -> list[int]:
    """Длины, которые перебираем для одного списочного слота.

    Обязательный слот начинается с 1, необязательный — с 0: ноль у обязательного
    не пройдёт validate_content, а у необязательного это легальный вариант
    раскладки («убрать ряд»), и его геометрию калибровать надо.
    """
    lo = 1 if spec.required else 0
    return list(range(lo, max(spec.max_items, 1) + 1))


def _content_for(spec, counts: dict[str, int], *, extras: bool,
                 drop: frozenset[str]) -> dict:
    """Контент шаблона под одну комбинацию осей.

    drop — необязательные слоты верхнего уровня, которые в этом варианте пусты.
    """
    content: dict = {}
    for pos, (name, slot) in enumerate(spec.slots.items()):
        if name in drop:
            continue
        if slot.kind == "list":
            n = counts.get(name, 0)
            if n:
                content[name] = [
                    _item(slot.item_slots, seed=i * 3 + pos,
                          extras=extras) for i in range(n)]
        elif name == "image":
            content[name] = _IMAGE
        elif name == "number":
            content[name] = "04"
        else:
            content[name] = _text(slot.max_chars, seed=pos * 5)
    return content


def _optional_top(spec) -> list[str]:
    """Необязательные слоты верхнего уровня, кроме списочных.

    Списочные уже перебираются длинами (ноль = «нет слота»), поэтому второй раз
    их сюда брать нельзя.

    Слот image раньше был исключён — считалось, что шаблон всё равно подставит
    свой ассет и дерево от контента не зависит. Это неверно для двух шаблонов из
    двадцати пяти: timeline по `{% if content.image %}` рисует либо панели с
    картинкой, либо брендовый лайнарт (совсем другое поддерево), а service-table
    добавляет .svc-dots. Своей картинки калибровка не видела никогда, поэтому
    отпечатка такого дерева в таблице не было, и слайд, где пользователь
    подставил СВОЮ картинку, просто пропадал из PPTX — молча, одной строкой в
    «промахах по таблице». Проверено: ключ не находился у обоих шаблонов.

    Цена — вдвое больше вариантов у этих трёх шаблонов (у cover-image дерево не
    меняется, и unique_variants их схлопнет), то есть десятки деревьев из 1600.
    """
    return [n for n, s in spec.slots.items()
            if not s.required and s.kind != "list"]


def _has_optional_items(spec) -> bool:
    return any(not i.required
               for s in spec.slots.values() if s.kind == "list"
               for i in (s.item_slots or {}).values())


def candidates() -> list[Variant]:
    """Все варианты каталога по всем осям раскладки."""
    from ..library import TemplateLibrary

    out: list[Variant] = []
    for spec in TemplateLibrary.load().templates:
        lists = {n: s for n, s in spec.slots.items() if s.kind == "list"}
        if lists:
            axes = [_cardinalities(lists[n]) for n in lists]
            combos = [dict(zip(lists, sizes)) for sizes in product(*axes)]
        else:
            combos = [{}]

        opt = _optional_top(spec)
        # Каждое подмножество необязательных слотов верхнего уровня: их мало
        # (не больше трёх на шаблон), так что полный перебор дешевле догадок.
        drops = [frozenset(d) for r in range(len(opt) + 1)
                 for d in __import__("itertools").combinations(opt, r)]
        extra_axis = (True, False) if _has_optional_items(spec) else (True,)

        for counts in combos:
            for drop in drops:
                for extras in extra_axis:
                    content = _content_for(spec, counts, extras=extras,
                                           drop=drop)
                    base = ",".join(filter(None, [
                        "+".join(f"{n}={counts[n]}" for n in sorted(counts)),
                        "без:" + "+".join(sorted(drop)) if drop else "",
                        "" if extras else "без-доп"]))
                    for profile in PROFILES:
                        out.append(Variant(
                            spec.id,
                            ",".join(filter(None, [base, profile])),
                            content if profile == "wide"
                            else retext(content, profile),
                            profile))
    return out


def build_deck(items: list[Variant], theme: str) -> str:
    """Собрать деку из готовых вариантов — тем же сборщиком, что и настоящую.

    Именно «тем же»: если эталон собирать в обход assemble(), калибровка измерит
    не ту разметку, которую потом будет разбирать конвертер.
    """
    from ..assembler import assemble
    from ..library import TemplateLibrary
    from ..models import DeckPlan, SlidePlan

    library = TemplateLibrary.load()
    slides = []
    for index, v in enumerate(items, start=1):
        errors = library.validate_content(v.template_id, v.content)
        if errors:                                # рыба обязана быть валидной
            raise AssertionError(
                f"синтетический контент не прошёл контракт {v.template_id} "
                f"({v.label}): {errors}")
        slides.append(SlidePlan(index=index,
                                type=library.get(v.template_id).type,
                                template_id=v.template_id, freeform=False,
                                content=v.content))
    return assemble(DeckPlan(title="Калибровка PPTX", slides=slides), theme=theme)


def _levers(content: dict) -> list[tuple[str, ...]]:
    """Что можно изменить по отдельности: слот верхнего уровня или под-слот.

    Гранулярность именно такая, потому что грубее — не работает. У
    two-col-cards ветвление читает `card.body` (стрелка рисуется только при
    коротком теле), а ширину вкладки задаёт `card.heading`. Тронув слот `cards`
    целиком, мы изменили бы оба и уехали в другое дерево — то есть ровно в то,
    из-за чего партнёра и не нашлось.

    Под-слот берётся один на весь список, а не на каждый элемент: нужен другой
    текст в том же дереве, а не перебор элементов.
    """
    out: list[tuple[str, ...]] = []
    for name, value in content.items():
        if isinstance(value, list):
            for item in value:
                for iname in item:
                    if (name, iname) not in out:
                        out.append((name, iname))
        else:
            out.append((name,))
    return out


def _pull(base: dict, donor: dict, levers: list[tuple[str, ...]]) -> dict:
    """Копия base, где перечисленные слоты взяты у donor — с другим текстом."""
    out = {k: ([dict(i) for i in v] if isinstance(v, list) else v)
           for k, v in base.items()}
    for lever in levers:
        name = lever[0]
        if name not in donor or name not in out:
            continue
        if len(lever) == 1:
            out[name] = donor[name]
            continue
        for i, item in enumerate(out[name]):
            src = donor[name][i] if i < len(donor[name]) else {}
            if lever[1] in item and lever[1] in src:
                item[lever[1]] = src[lever[1]]
    return out


def _keys_of(variants: list[Variant], theme: str) -> list[str]:
    """Ключи деревьев для набора вариантов. Jinja + lxml, браузера нет."""
    from lxml import html as LH

    from .domkey import variant_key

    doc = LH.document_fromstring(build_deck(variants, theme))
    return [variant_key(s)
            for s in doc.xpath('//section[contains(@class,"slide")]')]


def _pair_up(lonely: list[tuple[str, Variant, dict]],
             theme: str) -> dict[str, Variant]:
    """Подобрать партнёра деревьям, у которых его нет. -> {ключ: партнёр}.

    Зачем. Профили режут текст РАВНОМЕРНО по всем слотам, и из-за этого дерево
    может существовать ровно в одном профиле: стрелка в карточке
    two-col-cards появляется только при коротком теле, то есть только в tiny.
    Партнёра у такого дерева нет, content_sized делает `if not peers: continue`
    — и в таблицу не уходит ни `aw`, ни `fh`. Измерено: 4 дерева из 1651, все
    two-col-cards; на них fidelity давал 10 расхождений, до 192px (вкладка
    прижата к min-width:299px, заголовок на строку выше, и это каскадом в
    высоту карточки). Контент при этом совершенно обычный: короткое тело
    карточки и заголовок в полный слот.

    Как. Равномерной стрижкой нужное дерево не получить по построению — надо
    менять слоты ПООДИНОЧКЕ. Берём голову и вариант противоположной длины того
    же сочетания осей и переносим слоты по одному, оставляя только те, от
    которых ключ не изменился. Ключ здесь и критерий приёмки: спарить два
    разных дерева нельзя в принципе, проверка этого не допустит.

    Два раунда. Первый отбирает годные рычаги по одному, второй включает все
    годные сразу — чем больше разница в тексте, тем вернее content_sized
    увидит расхождение рамок. Если вместе они дерево всё-таки меняют (рычаги
    могут влиять друг на друга), остаётся первый годный.

    Остаться без партнёра всё ещё можно: если текст слота нельзя изменить, не
    сменив дерева, у рамки действительно нет второго замера. Это предел
    измерения, а не недоделка — но теперь это редкий случай, а не правило.
    """
    probes: list[Variant] = []
    owner: list[tuple[str, tuple[str, ...]]] = []
    for key, head, donor in lonely:
        for lever in _levers(head.content):
            probes.append(Variant(head.template_id, f"{head.label},пара",
                                  _pull(head.content, donor, [lever]), "пара"))
            owner.append((key, lever))
    if not probes:
        return {}

    good: dict[str, list[tuple[str, ...]]] = {}
    for (key, lever), got in zip(owner, _keys_of(probes, theme)):
        if got == key:
            good.setdefault(key, []).append(lever)

    heads = {key: (head, donor) for key, head, donor in lonely}
    out: dict[str, Variant] = {}
    union: list[Variant] = []
    union_keys: list[str] = []
    for key, levers in good.items():
        head, donor = heads[key]
        out[key] = Variant(head.template_id, f"{head.label},пара",
                           _pull(head.content, donor, levers[:1]), "пара")
        if len(levers) > 1:
            union.append(Variant(head.template_id, f"{head.label},пара",
                                 _pull(head.content, donor, levers), "пара"))
            union_keys.append(key)
    for key, var, got in zip(union_keys, union, _keys_of(union, theme)):
        if got == key:
            out[key] = var
    return out


def unique_variants(theme: str) -> list[tuple[str, Variant, list[Variant]]]:
    """Отобрать варианты с РАЗНЫМИ деревьями. -> [(ключ, широкий, остальные)].

    Дешёвый проход: Jinja + lxml, браузера нет. В браузер на каждое дерево
    уйдёт широкий вариант и те профили, которые дали ТОТ ЖЕ ключ. Пара нужна не
    для страховки, а как замер: элементы шириной по содержимому (flex-элемент с
    align-items:flex-start — .g22-text, .statement-title и ещё десяток) в этих
    снимках имеют разную рамку, и только по разнице их и можно отличить от
    растянутых. Что с этим делает калибровка — см. calibrate.content_sized().

    Партнёров обычно один или два: tiny перескакивает ветвление по длине и
    уходит в другой ключ, narrow не сжимается там, где текста больше полосы.
    Чем больше партнёров, тем точнее ответ.

    А вот НОЛЬ партнёров — не «нормально», как тут было написано раньше. Это
    дерево, у которого рамки по тексту не отличить от растянутых вообще: ни
    `aw`, ни `fh` в таблицу не уйдёт, и промах не увидит даже --check-reflow,
    потому что и он сравнивает пару. Таким деревьям партнёр подбирается
    отдельно — см. _pair_up.

    Широкий вариант идёт первым: его геометрия и попадает в таблицу как худший
    случай.
    """
    from lxml import html as LH

    from .domkey import variant_key

    cands = candidates()
    doc = LH.document_fromstring(build_deck(cands, theme))
    sections = doc.xpath('//section[contains(@class,"slide")]')
    if len(sections) != len(cands):
        raise RuntimeError(
            f"в эталонной деке {len(sections)} секций, вариантов {len(cands)}")

    by_key: dict[str, dict[str, Variant]] = {}
    order: list[str] = []
    for section, cand in zip(sections, cands):
        key = variant_key(section)
        if key not in by_key:
            by_key[key] = {}
            order.append(key)
        by_key[key].setdefault(cand.profile, cand)

    # Варианты по сочетанию осей: из них берётся «противоположная длина» для
    # подбора партнёра. Метка кончается профилем, он и отрезается.
    by_base: dict[tuple[str, str], dict[str, Variant]] = {}
    for v in cands:
        base = v.label[:len(v.label) - len(v.profile)].rstrip(",")
        by_base.setdefault((v.template_id, base), {})[v.profile] = v

    out = []
    lonely: list[tuple[str, Variant, dict]] = []
    for key in order:
        group = by_key[key]
        # Первым — самый «широкий» из имеющихся: в таблицу уходит худший случай.
        head = next(group[p] for p in PROFILES if p in group)
        rest = [v for p in PROFILES if (v := group.get(p)) is not None
                and v is not head]
        out.append((key, head, rest))
        if rest:
            continue
        # Голова tiny — тянем текст длиннее, иначе короче: нужен ДРУГОЙ текст,
        # а в какую сторону — зависит от того, чем голова оказалась.
        base = head.label[:len(head.label) - len(head.profile)].rstrip(",")
        want = "wide" if head.profile == "tiny" else "tiny"
        donor = by_base.get((head.template_id, base), {}).get(want)
        if donor is not None:
            lonely.append((key, head, donor.content))

    found = _pair_up(lonely, theme)
    for key, _head, rest in out:
        partner = found.get(key)
        if partner is not None:
            rest.append(partner)
    return out
