"""Офлайн-калибровка: снять геометрию эталонной деки браузером ОДИН раз.

Это единственное место во всей конвертации, где поднимается Playwright. Запуск
ручной (CLI), результат — geometry.json, который коммитится в репозиторий. В
рантайме конвертер читает таблицу и браузер не трогает: ровно этого требует
ограничение по ресурсам (секунды и сотни мегабайт на экспорт мы позволить себе
не можем).

Три вещи, без которых замеры были бы молча неверными — все три решаются
штатными механизмами самой деки, а не самодельными обходами:

1. `?editor=1` → deck.js:181 навешивает body.no-motion, и motion.css показывает
   входы в финальном состоянии (`animation:none`, `opacity:1`). Без этого у
   активного слайда работает @keyframes m-enter с `transform:translateY(16px)`,
   и getBoundingClientRect() вернул бы позицию посреди анимации.
2. `prefers-reduced-motion: reduce` → deck.js:61 отдаёт счётчикам .js-count
   сразу финальный текст. Иначе цифра измерялась бы на полпути от нуля, а это
   другая ширина строки.
3. viewport ровно 1920x1080 → rescale() выставляет --deck-scale = 1, и рамки
   приходят в настоящих слайдовых пикселях. Проверяем явно: если масштаб уехал,
   падаем, а не пишем молча испорченную таблицу.

Остальные слайды измеряются вместе с активным, потому что .slide — это
`visibility:hidden`, а не `display:none`: раскладка у них настоящая. Поэтому
вариантов за один проход снимается столько, сколько влезет в память — отсюда
нарезка на страницы по CHUNK секций.

Ключ строки таблицы считает domkey.variant_key() по РАЗМЕТКЕ, а не по плану
контента. Это важно: ту же функцию по той же разметке зовёт конвертер в
рантайме, поэтому две половины контракта не могут разойтись — она одна.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .domkey import js_collect, sources_digest, variant_key

DATA_DIR = Path(__file__).parent / "data"
THEMES = ("dark", "light")
SLIDE_PX = (1920, 1080)

# Секций на одну страницу браузера. У каждой настоящая раскладка 1920x1080, и
# полторы тысячи сразу съедают память без всякой пользы.
CHUNK = 400



def effective(slide: dict, fit: dict[str, tuple[bool, bool]]) -> list[tuple]:
    """Срез слайда, который обязан определяться ТОЛЬКО деревом секции.

    Это не замер как есть, а то, чем конвертер в итоге пользуется, — иначе
    проверка ловила бы шум. У рамки, подогнанной под текст, от текста зависят и
    ширина, и положение (.quote-text центрирован: x = центр − w/2, и на коротком
    тексте он уезжает на 373 px). Сама по себе эта пара чисел конвертеру не
    нужна: он берёт доступную полосу и выключку, а они от текста не зависят —
    вот их и сверяем. То же по вертикали: высота по строкам исключается, а y
    остаётся, и если он всё же поедет, проверка это покажет.

    Элементы внутри <svg> отброшены: их позиции заданы автором в координатах
    viewBox, и внутри одного дерева они законно различаются (у bar-chart с 2 и с
    6 барами разное число <rect>). Конвертер читает их из HTML, не из таблицы.
    """
    items = {it["path"]: it for it in slide["items"]}
    out = []
    for it in slide["items"]:
        if it["authored"]:
            continue
        fit_w, fit_h = fit.get(it["path"], (False, False))
        if fit_w:
            ax, aw = available(items, it["path"], slide["root"], fit)
            x, w = round(ax, 2), round(aw, 2)
        else:
            x, w = it["x"], it["w"]
        h = None if fit_h else it["h"]
        out.append((it["path"], it["tag"], it["cls"], it["slot"],
                    x, it["y"], w, h))
    return out


def shifts(a: list[tuple], b: list[tuple]) -> list[tuple[float, str, str]] | None:
    """Все расхождения между двумя снимками одного дерева: [(px, элемент, поле)].

    Не только максимум: нужна картина целиком. Остаточные расхождения — это
    места, где длина текста двигает раскладку по-настоящему (перенос строки
    поднимает соседа ниже), и чинить их можно только измерением текста. Чтобы
    знать, что именно чинить, калибровка печатает список, а не одно число.

    None — если наборы элементов не совпали: это не «погрешность», а
    разъехавшийся ключ, и сообщать об этом надо отдельно.
    """
    if len(a) != len(b):
        return None
    out = []
    for ra, rb in zip(a, b):
        if ra[:4] != rb[:4]:                      # path/tag/cls/slot
            return None
        for name, u, v in zip(("x", "y", "w", "h"), ra[4:], rb[4:]):
            if u is None or v is None:
                continue                          # высота по строкам — не сверяем
            if abs(u - v) > 0.5:
                cls = (ra[2] or ra[1]).split()[0]
                out.append((abs(u - v), f"{ra[1]}.{cls}", name))
    return out


def content_sized(wide: dict,
                  others: list[dict]) -> dict[str, tuple[bool, bool]]:
    """{путь: (ширина по тексту, высота по тексту)} по снимкам одного дерева.

    Отличить «рамку, подогнанную под текст» от растянутой иначе нельзя: в CSS
    это одно и то же `display:block`, а разница — в том, что родитель flex с
    align-items:flex-start, и тогда ширина элемента равна fit-content. Поэтому
    не угадываем по стилям, а СМОТРИМ: меряем дерево несколько раз разным
    текстом и сравниваем. Разошлась ширина — она от текста; разошлась высота —
    в ней число перенесённых строк.

    Ответ — ОБЪЕДИНЕНИЕ по всем снимкам: достаточно, чтобы рамка разошлась хоть
    с одним. Это не перестраховка, а следствие того, что разные профили
    показывают разное (см. refdeck.PROFILES): у .quote-text узкий профиль даёт
    ровно ту же рамку, что широкий (оба переносятся по полосе), и только tiny
    сжимается; у .statement-title наоборот есть только narrow.

    Зачем это нужно. В таблицу уходит широкий вариант, у которого ширина
    .g22-text равна 1740 px, а при коротком тексте — 266 px. Для положения
    текста это безразлично (x и y совпадают, мы это проверяем отдельно), но
    рамку в PPTX надо делать не по замеру, а по ДОСТУПНОЙ ширине: иначе
    реальный текст деки переносился бы по случайной ширине синтетической рыбы.
    """
    maps = [{it["path"]: it for it in o["items"]} for o in others]
    out: dict[str, tuple[bool, bool]] = {}
    for it in wide["items"]:
        peers = [m[it["path"]] for m in maps if it["path"] in m]
        if not peers:
            continue
        out[it["path"]] = (
            any(abs(it["w"] - p["w"]) > 0.5 for p in peers),
            any(abs(it["h"] - p["h"]) > 0.5 for p in peers))
    return out


def available(items: dict[str, dict], path: str, root: dict,
              fit: dict[str, tuple[bool, bool]]) -> tuple[float, float]:
    """Доступная тексту полоса (левый край, ширина), px.

    Это content-box ближайшего предка, чья ширина задана РАСКЛАДКОЙ, а не
    содержимым. Просто «родитель» тут не годится: в contacts.html подпись лежит
    в .contacts-subtitle-wrap, а тот сам подогнан под текст, и полоса,
    посчитанная по нему, уехала бы на 909 px вместе с ним. Поэтому поднимаемся,
    пока предки подогнаны под текст, накапливая их padding — он сужает место
    независимо от того, чем задана рамка.

    Для прямого ребёнка секции предка в items нет: секция занимает весь слайд,
    поэтому её рамку берём как (0, 1920), а padding — из её же стиля.

    Рамку (border) вычитаем вместе с padding и по той же причине: в getBoundingClientRect
    приходит border-box, а текст живёт в content-box. У .fg-cell рамка 1 px, и без
    этого вычета полоса начиналась на пиксель левее настоящей — ровно столько,
    чтобы проверка выключки «слева» (|x - ax| < 1) не сработала и элемент молча
    записался бы как центрированный.

    Поля вычитаются тоже, но не как padding предка, а как СОБСТВЕННОЕ поле
    элемента: padding сужает место изнутри предка, поле — снаружи самого
    элемента. Разница видна на .kpi-cell с margin-left:150px — место под него
    меньше ровно на эти 150.
    """
    me = items.get(path)
    pad_l = _px(me["style"].get("marginLeft")) if me else 0.0
    pad_r = _px(me["style"].get("marginRight")) if me else 0.0
    cur = path
    while True:
        parent_path = cur.rpartition(".")[0]
        if not parent_path:
            x0, w0, style = 0.0, float(SLIDE_PX[0]), root
            break
        p = items.get(parent_path)
        if p is None:                             # предок внутри <svg>
            return 0.0, float(SLIDE_PX[0])
        if not fit.get(parent_path, (False, False))[0]:
            x0, w0, style = p["x"], p["w"], p["style"]
            break
        pad_l += _inset_l(p["style"]) + _px(p["style"].get("marginLeft"))
        pad_r += _inset_r(p["style"]) + _px(p["style"].get("marginRight"))
        cur = parent_path
    pad_l += _inset_l(style)
    pad_r += _inset_r(style)
    return x0 + pad_l, max(w0 - pad_l - pad_r, 0.0)


def _inset_l(style: dict) -> float:
    """Отступ от левого края рамки до content-box: padding + border."""
    return (_px(style.get("paddingLeft"))
            + _px(style.get("borderLeftWidth")))


def _inset_r(style: dict) -> float:
    return (_px(style.get("paddingRight"))
            + _px(style.get("borderRightWidth")))


def _px(css: str | None) -> float:
    """'30px' -> 30.0. Нечисловое -> 0."""
    if not css:
        return 0.0
    try:
        return float(css.strip().removesuffix("px"))
    except ValueError:
        return 0.0


def collect(html: str, *, timeout_ms: int = 120_000) -> list[dict]:
    """Снять геометрию всех секций деки. -> список слайдов в порядке документа."""
    import tempfile

    from playwright.sync_api import sync_playwright

    # Имя уникальное, каталог системный: с фиксированным путём в data/ два
    # одновременных запуска (например, проверка и сборка таблицы) удаляли файл
    # друг у друга на полпути, и один из них падал на ERR_FILE_NOT_FOUND.
    fd, name = tempfile.mkstemp(prefix="calib-", suffix=".html")
    page_file = Path(name)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(html)
    page_url = page_file.as_uri() + "?editor=1"

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                device_scale_factor=1,
                reduced_motion="reduce",      # счётчики .js-count → сразу финал
            )
            page = ctx.new_page()
            page.goto(page_url, wait_until="load", timeout=timeout_ms)
            # Шрифты инлайновые (base64), но декодирование всё равно
            # асинхронное: мерить до fonts.ready — мерить Verdana.
            page.evaluate("document.fonts.ready")
            # Пересчитать autofit уже на настоящих метриках. Внутрь IIFE не
            # дотянуться (window.deck отдаёт только навигацию), поэтому
            # переиспользуем штатный обработчик 'load' из deck.js:192.
            page.evaluate("window.dispatchEvent(new Event('load'))")
            page.wait_for_timeout(300)

            scale = page.evaluate(
                "getComputedStyle(document.querySelector('.deck-stage'))"
                ".getPropertyValue('--deck-scale').trim()")
            if scale not in ("1", ""):
                raise RuntimeError(
                    f"--deck-scale = {scale!r}, ожидался 1: рамки приедут "
                    "отмасштабированными, таблица была бы испорчена")
            slides = page.evaluate(js_collect())
            browser.close()
    finally:
        page_file.unlink(missing_ok=True)
    return slides


def compact(slide: dict, palette: dict[str, int],
            fit: dict[str, tuple[bool, bool]],
            peers: list[dict] | None = None) -> list[dict]:
    """Сжать элементы слайда до того, что реально нужно в рантайме.

    Выкидываем балласт, каждый вид по своей причине:

    * `style` → индекс в общей палитре. На 14.5k элементов приходится меньше
      сотни различных наборов, то есть 8 из 11 МБ таблицы были одной и той же
      сотней значений, переписанной заново на каждом элементе.
    * `text` → не нужен вовсе: в таблице лежит синтетическая рыба, а текст для
      PPTX конвертер берёт из настоящей деки.
    * `sw`/`sh` → ширина строки зависит от РАНТАЙМНОГО текста, а в таблице
      измерена рыба. Для autofitStats() такое число не просто бесполезно, а
      обманчиво: нужную ширину конвертер считает по метрикам шрифта сам.
    * элементы строго внутри <svg> → читаются из HTML напрямую, точнее.

    Добавляем для рамок, подогнанных под текст (fit), два числа: предельную
    ширину `aw` и выключку `al` — какой край рамки при смене текста остаётся на
    месте. Замеренную ширину при этом оставляем: по ней конвертер считает
    плашки, у которых заливка обводит сам текст (.ba-delta).

    Левый край полосы (раньше был `ax`) не нужен и не хранится. Рантайм считает
    положение не от полосы, а ОТНОСИТЕЛЬНО замеренной рамки: при `al="l"` x не
    меняется вовсе, при "c" остаётся на месте центр, при "r" — правый край. Это
    не экономия, а точность: полосу приходилось вычислять подъёмом по дереву, и
    там, где подъём ошибался (у .contacts-title-wrap полоса выходила 0..1920
    вместо настоящей), ошибка переносилась прямо в x. Замеренная рамка ошибаться
    не может.

    Предельная ширина берётся двумя способами, и выбор между ними — замер, а не
    допущение. Если рыба широкого варианта ПЕРЕНЕСЛАСЬ (её max-content шире
    замеренной рамки), значит рамку обрезала полоса, и замеренная ширина РАВНА
    пределу — берём её. Если не перенеслась, предел выше замера и остаётся
    считать его подъёмом по дереву. Первый случай точен и покрывает ровно те
    элементы, где предел вообще важен (.ba-text: подъём давал 772 при настоящих
    640).

    Отдельный флаг `wp` — для рамок, которые в снимках разошлись по ширине, но
    НЕ от своего текста: это растянутые блоки внутри подогнанного под текст
    родителя. У .tcc-heading ширина меняется вслед за .tcc-tab (width:fit-content,
    min-width:299px), и считать её по max-content текста — промах на 130 px
    (158 вместо 247 = 299 − padding). Признак тоже замер: ширина равна
    content-box родителя во ВСЕХ снимках (см. _stretched).
    """
    from .textfit import Style, width

    items = {it["path"]: it for it in slide["items"]}
    maps = [{it["path"]: it for it in p["items"]} for p in (peers or [])]
    out = []
    for it in slide["items"]:
        if it["authored"]:
            continue
        sig = json.dumps(it["style"], sort_keys=True, ensure_ascii=False)
        idx = palette.setdefault(sig, len(palette))
        row = {"p": it["path"], "t": it["tag"], "x": it["x"], "y": it["y"],
               "w": it["w"], "h": it["h"], "s": idx}
        if it["cls"]:
            row["c"] = it["cls"]
        if it["slot"]:
            row["sl"] = it["slot"]
        fit_w, fit_h = fit.get(it["path"], (False, False))
        if fit_w:
            st = Style(it["style"])
            inset = _inset_l(it["style"]) + _inset_r(it["style"])
            own = it["text"] or ""
            clamped = (own.strip() and st.known
                       and width(own, st) + inset > it["w"] + 0.5)
            if clamped:
                row["aw"] = it["w"]
            else:
                row["aw"] = round(
                    available(items, it["path"], slide["root"], fit)[1], 2)
            if _stretched(it, items, maps):
                # Выключку такому не пишем вовсе, и это не пропуск. `al` значит
                # «какой край рамки, подогнанной под текст, остаётся на месте»,
                # а у растянутого блока левый край просто приклеен к content-box
                # родителя — рантайм увезёт его вместе с родителем, и вторая
                # поправка на выключку сдвинула бы его дважды.
                row["wp"] = 1          # ширина = content-box родителя
            else:
                # Из партнёров берём самого непохожего: чем сильнее разошлись
                # рамки, тем надёжнее видно, какой край остался на месте.
                seen = [m[it["path"]] for m in maps if it["path"] in m]
                tell = max(seen, key=lambda p: abs(p["w"] - it["w"]),
                           default=None)
                row["al"] = _align_in(it, tell, items, slide["root"], fit)
        if fit_h:
            row["fh"] = 1              # высота = число строк, не рамка макета
        if it.get("fs0"):
            # Кегль до вмешательства deck.js. В style палитры лежит кегль,
            # подогнанный под РЫБУ, — от него считать коэффициент сжатия нельзя.
            row["fs0"] = round(_px(it["fs0"]), 2)
        out.append(row)
    return out


def _stretched(wide: dict, items: dict[str, dict],
               maps: list[dict[str, dict]]) -> bool:
    """Ширина рамки равна content-box родителя во ВСЕХ снимках.

    Так отличается растянутый блок от подогнанного под текст, когда по одному
    снимку они неразличимы: у .tcc-heading ширина тоже гуляет от профиля к
    профилю, но не от своего текста, а потому что гуляет родительская .tcc-tab.
    Проверяем совпадение в каждом снимке: у настоящей fit-content рамки оно
    случайно совпасть во всех сразу не может — профили специально дают разную
    длину текста (см. refdeck.PROFILES).

    Плюс второе условие, без которого вывод был бы ложным. Рамка, которую
    полоса ОБРЕЗАЛА, тоже равна content-box родителя — но она fit-content, и на
    коротком реальном тексте должна сжаться. Поэтому у элемента с собственным
    текстом требуем снимок, где рыба в рамку ВЛЕЗЛА: значит рамка держит
    ширину родителя сама, а не потому что места не хватило. (Партнёров бывает и
    ноль, и tiny до нас может не дойти — см. refdeck.unique_variants, — так что
    на «профили разные» одно надеяться нельзя.)

    Требуем, чтобы родитель был в items: у прямого ребёнка секции рамки
    родителя нет, но он и не попадёт сюда — секция во всех профилях одной
    ширины, так что растянутый по ней блок вообще не считается content_sized.
    """
    from .textfit import Style, width

    par = wide["path"].rpartition(".")[0]
    if not par or par not in items:
        return False
    own = _px(wide["style"].get("marginLeft")) + _px(
        wide["style"].get("marginRight"))
    roomy = False
    for snap in [items, *maps]:
        me, pa = snap.get(wide["path"]), snap.get(par)
        if me is None or pa is None:
            return False
        inner = (pa["w"] - _inset_l(pa["style"]) - _inset_r(pa["style"])
                 - own)
        if abs(me["w"] - inner) > 0.5:
            return False
        st = Style(me["style"])
        text = me["text"] or ""
        if not text.strip():
            roomy = True               # не лист: ширина не от своего текста
        elif st.known and (width(text, st) + _inset_l(me["style"])
                           + _inset_r(me["style"])) <= me["w"] + 0.5:
            roomy = True
    return roomy


def _align_in(wide: dict, thin: dict | None, items: dict[str, dict],
              root: dict, fit: dict[str, tuple[bool, bool]]) -> str:
    """Какой край рамки не двигается при смене текста: l / c / r.

    Выключку берём из замера, а не из CSS text-align: у подогнанной под текст
    рамки text-align бессмыслен (рамка равна тексту), а горизонтальное место
    задаёт align-items/justify-content родителя.

    И берём её из ПАРЫ, сравнивая две рамки одного элемента напрямую. Это и
    есть определение: выключка — то, что при смене текста осталось на месте.
    Сравнение с вычисленной полосой отвечало на тот же вопрос через посредника
    и ошибалось дважды — когда рыба заполняла полосу целиком (у .statement-title
    1734 из 1736, и x молчал о выключке) и когда подъём по дереву находил не ту
    полосу.
    """
    if thin is not None:
        if abs(wide["x"] - thin["x"]) < 0.5:
            return "l"
        if abs((wide["x"] + wide["w"]) - (thin["x"] + thin["w"])) < 0.5:
            return "r"
        if abs((wide["x"] + wide["w"] / 2)
               - (thin["x"] + thin["w"] / 2)) < 0.75:
            return "c"
        return "l"
    # Пары нет (одиночное дерево) — остаётся полоса.
    ax, aw = available(items, wide["path"], root, fit)
    x, w = wide["x"], wide["w"]
    if abs(x - ax) < 1.0:
        return "l"
    if abs((x + w) - (ax + aw)) < 1.0:
        return "r"
    if abs((x + w / 2) - (ax + aw / 2)) < 1.5:
        return "c"
    return "l"


def _pages(picked: list, chunk: int = CHUNK) -> list[list]:
    """Разложить группы профилей по страницам браузера. Группа не делится.

    Делить нельзя: профили одного дерева сравниваются между собой, и если
    партнёр уедет на другую страницу, сравнивать будет не с чем. А резать
    приходится потому, что групп в теме 1629, а секций в них 4873 — страница
    такого размера кладёт браузер по памяти (проверка падала с SIGKILL).
    """
    pages, page, n = [], [], 0
    for item in picked:
        size = 1 + len(item[2])
        if page and n + size > chunk:
            pages.append(page)
            page, n = [], 0
        page.append(item)
        n += size
    if page:
        pages.append(page)
    return pages


def _measure(picked: list, theme: str, chunk: int = CHUNK):
    """Снять геометрию групп постранично.

    Отдаёт по странице (slides, peers_at), где peers_at = {индекс широкого
    варианта: [индексы его партнёров]}. Этой парой пользуются обе проверки:
    партнёры нужны, чтобы отличить рамку по тексту от растянутой тем же
    способом, которым это делает build_table.
    """
    from .refdeck import build_deck

    for page in _pages(picked, chunk):
        part: list = []
        peers_at: dict[int, list[int]] = {}
        for _, head, rest in page:
            peers_at[len(part)] = [len(part) + 1 + k for k in range(len(rest))]
            part.append(head)
            part += rest
        yield collect(build_deck(part, theme)), peers_at


def build_table(theme: str, palette: dict[str, int],
                ) -> tuple[dict, dict, list[str], dict[str, tuple[float, int]]]:
    """Собрать эталонную деку, снять геометрию, свернуть по ключу дерева.

    Возвращает (таблица, переменные, предупреждения, худший сдвиг). Таблица:
    {"шаблон|отпечаток": слайд}. Переменные — {шаблон: {--имя: значение}}: они
    зависят от шаблона и темы, но не от контента, поэтому хранятся отдельно.

    Каждое дерево мерится в нескольких профилях текста (refdeck.PROFILES).
    Широкий идёт в таблицу (худший случай), остальные нужны, чтобы отличить
    рамки, подогнанные под текст, от растянутых (content_sized).

    Остаток — расхождение рамок между профилями ПОСЛЕ вычета подогнанных под
    текст. Это честная погрешность таблицы: насколько ещё может сдвинуться то,
    что мы считаем независимым от контента. Возвращается не одно число, а
    разбивка {"шаблон тег.класс поле": (худший сдвиг, в скольких деревьях)}:
    чинить такое можно только измерением текста в питоне, и чтобы знать, ЧТО
    чинить, нужен список элементов, а не максимум по деке.
    """
    from lxml import html as LH

    from .refdeck import build_deck, unique_variants

    picked = unique_variants(theme)
    group_of = {id(head): (key, rest) for key, head, rest in picked}

    table: dict[str, dict] = {}
    cssvars: dict[str, dict] = {}
    warnings: list[str] = []
    residual: dict[str, tuple[float, int]] = {}

    # Режем по ГРУППАМ, а не по секциям (см. _pages).
    for page in _pages(picked):
        part = [v for _, head, rest in page for v in [head, *rest]]
        html = build_deck(part, theme)
        # Ключи считаем по той самой разметке, что уедет в браузер, и той самой
        # функцией, которой конвертер считает их по реальной деке.
        doc = LH.document_fromstring(html)
        sections = doc.xpath('//section[contains(@class,"slide")]')
        slides = collect(html)
        if not (len(slides) == len(sections) == len(part)):
            raise RuntimeError(
                f"снято {len(slides)} секций, в разметке {len(sections)}, "
                f"вариантов {len(part)}")

        by_index = {id(v): i for i, v in enumerate(part)}
        for slide, section, v in zip(slides, sections, part):
            if slide["template"] != v.template_id:
                raise RuntimeError(
                    f"порядок разъехался: DOM={slide['template']} "
                    f"план={v.template_id}")
            prev = cssvars.setdefault(v.template_id, slide["vars"])
            if prev != slide["vars"]:
                warnings.append(
                    f"{v.template_id}: CSS-переменные зависят от контента")
            if id(v) not in group_of:
                continue                          # это партнёр, не голова
            key, rest = group_of[id(v)]
            if key in table:
                continue
            peers = []
            for other in rest:
                j = by_index.get(id(other))
                if j is None:
                    raise RuntimeError(f"{key}: группа разъехалась по страницам")
                peers.append(slides[j])
            fit = content_sized(slide, peers)
            mine = effective(slide, fit)
            for peer in peers:
                found = shifts(mine, effective(peer, fit))
                if found is None:
                    warnings.append(
                        f"{key} ({v.label}): у одного дерева разный набор "
                        f"элементов — отпечаток не описывает раскладку")
                    continue
                for px, elem, field in found:
                    tag = f"{v.template_id} {elem} {field}"
                    was, n = residual.get(tag, (0.0, 0))
                    residual[tag] = (max(was, px), n + 1)
            root_sig = json.dumps(slide["root"], sort_keys=True,
                                  ensure_ascii=False)
            table[key] = {"s": palette.setdefault(root_sig, len(palette)),
                          "items": compact(slide, palette, fit, peers)}
    return table, cssvars, warnings, residual


def check_text(theme: str, limit: int = 0) -> int:
    """Сверить измеритель текста (textfit) с браузером. -> число промахов.

    Это не тест «на всякий случай», а проверка того единственного, что осталось
    зависеть от контента: числа строк. Эталон здесь не рукописный, а снятый —
    по каждому текстовому листу эталонной деки браузер уже сказал и ширину, и
    высоту, то есть у нас есть десятки тысяч размеченных примеров.

    Сверяем две разные вещи, и обе нужны:

    * ширину — только у листьев, подогнанных под текст и уложившихся в одну
      строку. Там замеренная рамка И ЕСТЬ ширина текста, то есть сравнение
      прямое, без посредников: оно проверяет шейпинг, кернинг и letter-spacing.
      Подогнанность берём не из CSS, а из той же пары снимков, что и основная
      калибровка (content_sized): у растянутого блока рамка равна полосе, и
      сверять с ней ширину текста было бы сравнением с бессмыслицей.
    * число строк — у листьев, чья высота кратна line-height. Это и есть то,
      что должно попасть в рантайм, и ошибка здесь дискретная: промах на одну
      строку двигает соседей ровно на line-height.
    """
    from .refdeck import unique_variants
    from .textfit import Style, line_count, width

    picked = unique_variants(theme)[:limit or None]
    w_err: list[tuple[float, str, str]] = []
    l_err: dict[str, tuple[int, int]] = {}
    n_w = n_l = 0
    w_max = 0.0
    for slides, peers_at in _measure(picked, theme):
        for i, js in peers_at.items():
            slide = slides[i]
            fit = content_sized(slide, [slides[j] for j in js])
            kids = {p["path"].rpartition(".")[0] for p in slide["items"]}
            for it in slide["items"]:
                if it["authored"] or it["path"] in kids or not it["text"]:
                    continue              # лист с собственным текстом
                st = Style(it["style"])
                if not st.known or st.line_height <= 0:
                    continue
                pad_l = _px(it["style"].get("paddingLeft"))
                pad_r = _px(it["style"].get("paddingRight"))
                pad_v = (_px(it["style"].get("paddingTop"))
                         + _px(it["style"].get("paddingBottom")))
                lines = (it["h"] - pad_v) / st.line_height
                tag = (f'{slide["template"]} {it["tag"]}'
                       f'.{(it["cls"] or "-").split()[0]}')
                # Одна строка И рамка по тексту -> прямая сверка ширины.
                if abs(lines - 1) < 0.02 and fit.get(it["path"], (False,))[0]:
                    n_w += 1
                    d = width(it["text"], st) - (it["w"] - pad_l - pad_r)
                    w_max = max(w_max, abs(d))
                    if abs(d) > 1.0:
                        w_err.append((d, tag, it["text"][:40]))
                # Высота кратна строке -> сверяем ПРЕДСКАЗАННОЕ число строк.
                if abs(lines - round(lines)) < 0.02 and round(lines) >= 1:
                    n_l += 1
                    got = line_count(it["text"], it["w"] - pad_l - pad_r, st)
                    if got != round(lines):
                        bad, cnt = l_err.get(tag, (0, 0))
                        l_err[tag] = (bad + 1, cnt)
    print(f"{theme}: ширина сверена на {n_w} листах, худшее расхождение "
          f"{w_max:.3f} px, промахов >1px: {len(w_err)}")
    for d, tag, txt in sorted(w_err, key=lambda r: -abs(r[0]))[:10]:
        print(f"  {d:+8.2f} px  {tag}  {txt!r}")
    print(f"{theme}: число строк сверено на {n_l} листах, промахов: "
          f"{sum(b for b, _ in l_err.values())}")
    for tag, (bad, _) in sorted(l_err.items(), key=lambda kv: -kv[1][0])[:10]:
        print(f"  x{bad:<5d} {tag}")
    return len(w_err) + sum(b for b, _ in l_err.values())


def check_reflow(theme: str, limit: int = 0) -> int:
    """Сверить поправку на перенос строк с браузером. -> число промахов.

    Эталон здесь не придуман, а снят: каждое дерево измерено в нескольких
    профилях текста. Берём таблицу ШИРОКОГО варианта (именно она уезжает в
    репозиторий), подставляем текст партнёра, прогоняем reflow.apply() и сверяем
    с измеренным партнёром. Если поправка верна, предсказание совпадёт с
    замером; если нет — увидим, на каком элементе и на сколько.

    Направление выбрано не случайно. Широкий -> узкий значит, что строк
    становится МЕНЬШЕ, то есть дельты отрицательные и элементы едут вверх. Это
    тот же код, что и на рост, но ошибку в знаке или в порядке обхода он
    покажет сразу.

    Сверяются все четыре числа, а не только y. Поправка правит и ширину
    подогнанных под текст рамок, и положение внутри полосы, — проверка, которая
    смотрит на один y, пропустила бы ровно те ошибки, из-за которых fit-content
    переписывался трижды.
    """
    from .refdeck import unique_variants
    from .reflow import apply

    picked = [g for g in unique_variants(theme) if g[2]][:limit or None]
    palette: dict[str, int] = {}
    err: dict[str, tuple[float, int]] = {}
    before: dict[str, tuple[float, int]] = {}
    fixed = checked = 0
    for slides, peers_at in _measure(picked, theme):
        for i, js in peers_at.items():
            wide_s = slides[i]
            peers = [slides[j] for j in js]
            fit = content_sized(wide_s, peers)
            base = compact(wide_s, palette, fit, peers)
            styles = [json.loads(s) for s in palette]
            for peer in peers:
                rows = [dict(r) for r in base]
                truth = {it["path"]: it for it in peer["items"]
                         if not it["authored"]}
                text_at = {p: it["text"] for p, it in truth.items()}
                # Расхождение ДО поправки — чтобы видеть, что она работает, а
                # не просто «промахов мало, потому что их и не было».
                _diff(rows, truth, wide_s, before)
                fixed += len(apply(rows, styles, text_at))
                checked += _diff(rows, truth, wide_s, err)
    print(f"{theme}: поправок применено {fixed}, сверено {checked} рамок")
    print(f"  до поправки расходилось: "
          f"{sum(n for _, n in before.values())} ({len(before)} видов)")
    print(f"  после поправки расходится: "
          f"{sum(n for _, n in err.values())} ({len(err)} видов)")
    for tag, (px, n) in sorted(err.items(), key=lambda kv: -kv[1][0])[:12]:
        print(f"    {px:8.2f} px  x{n:<5d} {tag}")
    return sum(n for _, n in err.values())


def _tag(slide: dict, row: dict) -> str:
    cls = (row.get("c") or row["t"]).split()[0]
    return f'{slide["template"]} {row["t"]}.{cls}'


def _diff(rows: list[dict], truth: dict[str, dict], slide: dict,
          acc: dict[str, tuple[float, int]]) -> int:
    """Сверить строки таблицы с замером партнёра. -> сколько рамок сверено.

    Копим по виду элемента ХУДШЕЕ расхождение и число промахов: один и тот же
    промах повторяется на каждом однотипном элементе каждого слайда, и без
    группировки список превращается в простыню из одинаковых строк, по которой
    не видно, что именно починить следующим.

    Порог 1 px, а не 0.5: половина пикселя — это округление браузера при
    раскладке дробных размеров, и ловить её как ошибку значит утонуть в шуме.
    """
    n = 0
    for row in rows:
        t = truth.get(row["p"])
        if t is None:
            continue
        n += 1
        for f in ("x", "y", "w", "h"):
            d = abs(row[f] - t[f])
            if d <= 1.0:
                continue
            tag = f"{_tag(slide, row)} {f}"
            was, cnt = acc.get(tag, (0.0, 0))
            acc[tag] = (max(was, d), cnt + 1)
    return n


def main(argv: list[str] | None = None) -> int:
    import argparse
    import time

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--theme", choices=[*THEMES, "all"], default="all")
    ap.add_argument("--out", type=Path, default=DATA_DIR / "geometry.json")
    ap.add_argument("--check-text", action="store_true",
                    help="сверить textfit с браузером и выйти")
    ap.add_argument("--check-reflow", action="store_true",
                    help="сверить поправку на перенос строк и выйти")
    ap.add_argument("--limit", type=int, default=0,
                    help="взять только первые N деревьев (для проверок)")
    args = ap.parse_args(argv)

    if args.check_text or args.check_reflow:
        themes = THEMES if args.theme == "all" else (args.theme,)
        check = check_reflow if args.check_reflow else check_text
        return 1 if sum(check(t, args.limit) for t in themes) else 0

    themes = THEMES if args.theme == "all" else (args.theme,)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    palette: dict[str, int] = {}          # общая на все темы
    # sources — отпечаток шаблонов, CSS и правила ключа на момент замера (см.
    # domkey.sources_digest). Версия поднята с 3 на 4 именно из-за него: в
    # таблице версии 3 поля нет, и конвертер обязан отличать «отпечаток не
    # сошёлся» от «его там никогда и не было».
    payload: dict = {"version": 4, "slide": {"w": SLIDE_PX[0], "h": SLIDE_PX[1]},
                     "sources": sources_digest(),
                     "themes": {}, "vars": {}}
    problems = 0
    residual: dict[str, tuple[float, int]] = {}
    trees = 0
    for theme in themes:
        t0 = time.monotonic()
        table, cssvars, warnings, found = build_table(theme, palette)
        payload["themes"][theme] = table
        payload["vars"][theme] = cssvars
        trees += len(table)
        for tag, (px, n) in found.items():
            was, cnt = residual.get(tag, (0.0, 0))
            residual[tag] = (max(was, px), cnt + n)
        print(f"{theme}: {len(table)} деревьев, "
              f"{sum(len(v['items']) for v in table.values())} элементов, "
              f"{len(cssvars)} наборов переменных, "
              f"{time.monotonic() - t0:.0f} c")
        for w in warnings:
            problems += 1
            print(f"  ! {w}")

    payload["styles"] = [json.loads(s) for s in palette]
    print(f"палитра стилей: {len(palette)} наборов")
    args.out.write_text(json.dumps(payload, ensure_ascii=False,
                                   separators=(",", ":")), encoding="utf-8")
    size = args.out.stat().st_size / 1024 / 1024
    print(f"-> {args.out} ({size:.2f} MB)")
    # Остаток печатаем списком: это план работ по autofitStats/метрикам шрифта,
    # а не оценка «хорошо/плохо». Доля деревьев важнее пикселей: 200 px на одном
    # дереве из 1600 и 20 px на каждом — разные задачи.
    if residual:
        hit = sum(n for _, n in residual.values())
        print(f"остаточная зависимость от длины текста: {len(residual)} видов "
              f"элементов, {hit} случаев на {trees} деревьев")
        for tag, (px, n) in sorted(residual.items(), key=lambda kv: -kv[1][0]):
            print(f"  {px:8.2f} px  x{n:<5d} {tag}")
    else:
        print("остаточная зависимость от длины текста: нет")
    if problems:
        print(f"ВНИМАНИЕ: {problems} расхождений внутри отпечатка")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
