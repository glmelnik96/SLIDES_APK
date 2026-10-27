"""Измерение точности конвертации на деке с РЕАЛИСТИЧНЫМ текстом.

Чем это отличается от проверок в calibrate. Там эталонная дека и таблица
сделаны из одного и того же: калибровка сверяет саму себя и отвечает на вопрос
«непротиворечива ли таблица». Здесь вопрос другой и единственно важный:
насколько совпадёт с браузером слайд, которого в калибровке не было. Поэтому
текст берётся не на пределе слотов (как в refdeck) и не обрезанный до 16
символов, а промежуточных длин — ровно те случаи, где число строк у реального
слайда другое, чем у рыбы.

Что с чем сверяется. Не картинка с картинкой: растровое сравнение показало бы
«похоже/непохоже», но не сказало бы, какой элемент уехал и почему. Сверяются
числа: конвертер запоминает геометрию, которой собрал каждую фигуру
(_Builder.placed), а браузер измеряет ту же деку — и сравнение идёт по
элементам, путь к пути.

Границы проверки. Для прямоугольников и текста это равносильно сравнению самих
фигур: координата получается из этих чисел умножением на EMU_PX, без других
слагаемых. А вот у <img> и у содержимого <svg> между рамкой элемента и рамкой
фигуры стоит ещё одна формула, и сверять её здесь НЕЛЬЗЯ по существу, а не по
недоделке: браузер отдаёт рамку элемента одну и ту же независимо от object-fit,
так что растянутая картинка и вписанная для него неразличимы (а внутрь <svg> он
и вовсе отдаёт один прямоугольник на весь блок). Поэтому разделение такое:
здесь проверяется, что офлайновая таблица совпадает с браузером, а производные
от неё формулы — юнит-тестами (tests/test_topptx_svgpath.py). Попытка «починить»
расхождение по картинке подгонкой её рамки под рамку элемента вернёт растяжение.

Отдельно считаются промахи по таблице: ключ, которого в geometry.json нет. Это
не ошибка точности, а другая ситуация — конвертер про такой слайд ЗНАЕТ, что
не знает его, и именно такие слайды должен добирать браузерный запас. Поэтому
в отчёте они своей строкой, а не в общем распределении.
"""
from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=None)
def _artwork(name: str) -> str:
    """Ассет каталога как data: URI — в том виде, в каком его вставляет сборщик.

    Проверочную деку смотрят и глазами — это приёмка экспорта. Калибровочная
    заглушка (1x1 серый PNG из refdeck) здесь выглядела бы как серый
    прямоугольник на месте картинки, то есть как потерянное изображение, —
    ровно так её однажды и прочитали. Замеру всё равно (рамки картинок жёстко
    заданы CSS), а приёмке — нет.
    """
    from importlib import resources

    raw = (resources.files("htmlslides") / "assets" / name).read_bytes()
    mime = "image/jpeg" if name.endswith(".jpg") else "image/webp"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _realistic(spec, counts: dict[str, int], frac: float,
               mix: bool = False, image: bool = True) -> dict:
    """Контент шаблона с текстом промежуточной длины.

    frac — доля от max_chars. Берём не одну долю на слайд, а сдвигаем её по
    слотам: если все слоты заполнить одинаково, у половины шаблонов строки
    совпадут с рыбой случайно, и проверка окажется мягче, чем жизнь.

    mix — выбросить необязательные под-слоты у ЧЁТНЫХ элементов списка. Это
    единственный способ проверить донорскую починку здесь: такого дерева в
    таблице нет и быть не может (у grid-2x2 это 2^15 вариантов на одну длину
    ряда), а в жизни оно получается само собой — у одной карточки подзаголовок
    есть, у другой нет. Выбор по чётности, а не случайный: дека должна быть
    одной и той же от запуска к запуску, иначе расхождение не повторить.
    """
    content: dict = {}
    for pos, (name, slot) in enumerate(spec.slots.items()):
        f = frac + 0.17 * ((pos % 3) - 1)             # разнос по слотам
        if slot.kind == "list":
            n = counts.get(name, 0)
            if not n:
                continue
            content[name] = []
            for i in range(n):
                item = {}
                for ipos, (iname, ispec) in enumerate(slot.item_slots.items()):
                    if mix and not ispec.required and i % 2 == 0:
                        continue
                    g = max(0.2, min(1.0, f + 0.11 * ((i + ipos) % 3 - 1)))
                    item[iname] = _slot_text(iname, ispec, g, i * 3 + ipos)
                content[name].append(item)
        elif name == "image":
            # ВЕТВЛЕНИЕ: timeline и service-table по наличию картинки рисуют
            # разные поддеревья (панели с фото — или брендовый лайнарт/точки).
            # Проверке нужны ОБА. С пустым слотом когда-то молча пропадали
            # слайды, где картинку подставил пользователь; с вечно заполненным
            # из деки исчезла фирменная иллюстрация с облаками и стрелками, и
            # приёмка прочитала это как «не та картинка». Поэтому длины
            # чередуют ветки — см. вызов в build_deck. Обложка — всегда с
            # картинкой: её дерево от слота не зависит, а пустой src выглядел
            # бы как потерянное фото. Картинка настоящая — см. _artwork.
            # Обложке — jpg (путь <image> в svg, с clip-path и
            # slice-кадрированием), остальным — webp: он в белый список
            # python-pptx не входит, и его перекодировку в PNG (_img_bytes)
            # иначе ни одна проверочная дека не трогала бы.
            if not image and spec.id != "cover-image" and not slot.required:
                continue
            content[name] = _artwork(
                "cover-image-sample.jpg" if spec.id == "cover-image"
                else "service-console-flat.webp")
        elif name == "number":
            content[name] = "04"
        else:
            g = max(0.2, min(1.0, f))
            content[name] = _slot_text(name, slot, g, pos * 5)
    return content


def _slot_text(name: str, spec, frac: float, seed: int) -> str:
    from . import refdeck

    if name in refdeck._NUMERIC:
        return refdeck._pick(refdeck._VALUES, spec.max_chars, seed)
    if name == "display":
        return refdeck._pick(refdeck._DISPLAYS, spec.max_chars, seed)
    limit = int((spec.max_chars or 60) * frac)
    return refdeck._text(max(limit, 4), seed)


def build_deck(theme: str) -> str:
    """Собрать проверочную деку: каждый шаблон в четырёх длинах текста.

    Плюс пятый слайд — со смешанными необязательными под-слотами. Он проверяет
    не длину текста, а донорскую починку: ключа такого дерева в таблице нет, и
    без этого слайда весь путь repair.py остался бы вне браузерной сверки.
    Для шаблонов без необязательных под-слотов внутри элементов списка смесь
    совпала бы с обычным вариантом, и пятый слайд не добавляется.
    """
    from ..library import TemplateLibrary
    from .refdeck import Variant, build_deck as assemble_deck

    items: list = []
    for spec in TemplateLibrary.load().templates:
        lists = {n: s for n, s in spec.slots.items() if s.kind == "list"}
        # Списки берём в среднем размере: длины уже перебраны в калибровке, а
        # здесь проверяется текст, и раздувать деку вторым перебором незачем.
        counts = {n: max(1, (s.max_items + 1) // 2) for n, s in lists.items()}
        # Нечётные длины — без картинки: у timeline это фирменный лайнарт
        # с облаками и стрелками, у service-table — точечный узор. Чётные —
        # с фото (и с перекодировкой webp). Детерминированно, не случайно:
        # дека обязана быть одной и той же от запуска к запуску.
        for k, frac in enumerate((0.35, 0.55, 0.8, 1.0)):
            content = _realistic(spec, counts, frac, image=k % 2 == 1)
            items.append(Variant(spec.id, f"frac={frac}", content, True))
        # Смеси нужно минимум ДВА элемента: смысл в том, что у одного под-слот
        # есть, а у другого нет. При среднем размере 1 смесь вырождается в «ни
        # у кого нет» — а это обычное перебранное дерево, и слайд проверял бы
        # не починку, а тот же путь, что и четыре предыдущих. На этом и
        # попались kpi и two-col-cards: у них max_items=2, середина=1, и
        # промах по таблице у обоих измерен — то есть смесь молчала ровно там,
        # где была нужна.
        mixable = {n for n, s in lists.items() if s.max_items >= 2
                   and any(not i.required
                           for i in (s.item_slots or {}).values())}
        if mixable:
            mixed = dict(counts)
            for n in mixable:
                mixed[n] = max(2, counts[n])
            items.append(Variant(spec.id, "смесь",
                                 _realistic(spec, mixed, 0.7, mix=True),
                                 True))
    return assemble_deck(items, theme)


def compare(theme: str = "dark", out: Path | None = None) -> dict:
    """Собрать деку, конвертировать, сверить с браузером. -> сводка."""
    from lxml import html as LH

    from .calibrate import collect
    from .convert import _Builder
    from .domkey import variant_key

    html = build_deck(theme)
    doc = LH.document_fromstring(html)
    sections = doc.xpath('//section[contains(@class,"slide")]')

    builder = _Builder(theme)
    for i, section in enumerate(sections, start=1):
        builder.add_slide(section, i)
    if out:
        builder.save(out)

    truth = collect(html)
    if len(truth) != len(sections):
        raise RuntimeError(f"браузер снял {len(truth)} секций, "
                           f"в разметке {len(sections)}")

    err: dict[str, list[float]] = {}
    n = 0
    for i, (slide, section) in enumerate(zip(truth, sections), start=1):
        tmpl = slide["template"]
        for it in slide["items"]:
            if it["authored"] or it["w"] <= 0 or it["h"] <= 0:
                continue
            got = builder.placed.get((i, it["path"]))
            if got is None:
                continue                  # промах по таблице — считается отдельно
            n += 1
            for field, a, b in zip("xywh", got,
                                   (it["x"], it["y"], it["w"], it["h"])):
                d = abs(a - b)
                if d > 0.5:
                    cls = (it["cls"] or it["tag"]).split()[0]
                    err.setdefault(f"{tmpl} {it['tag']}.{cls} {field}",
                                   []).append(d)
    return {"slides": len(sections), "compared": n, "errors": err,
            "missing": builder.stats["missing"], "reflow": builder.stats["reflow"],
            "repaired": builder.stats["repaired"], "stats": builder.stats}


def compare_all(theme: str = "dark", only: str | None = None,
                chunk: int | None = None) -> dict:
    """То же сравнение, но по ВСЕМУ перебору, а не по 25 шаблонам с рыбой.

    Зачем нужна вторая, дорогая проверка. У трёх имеющихся разделение труда
    такое, что полный путь по всему перебору не проверяет никто (замерено):

        calibrate      все 1651 дерева, но сверяет калибровку с самой собой;
        --check-reflow все 1651 + партнёры, но только поправку на перенос,
                       на внутреннем представлении калибровки;
        compare        полный путь конвертации, но 27 деревьев из 1651 (1.6%)
                       — четыре слайда на шаблон отличаются длиной текста, а
                       не структурой, и дают один и тот же ключ.

    Разница между «алгоритм верен» и «верен тот путь, которым мы грузим» — это
    ровно щель устаревшей таблицы: geometry.json лежит в репозитории отдельно
    от кода, и расходятся они молча. Здесь проверяется путь целиком: ключ по
    дереву, строка из geometry.json, донорская починка, подгонка по тексту и
    числа, которыми собрана фигура, — против свежего замера браузером.

    Партнёров берём вместе с деревом не для компании: партнёр — та же
    раскладка с другой длиной текста, и подогнанная под текст рамка видна
    только на паре (см. refdeck.PROFILES). На two-col-cards, где раньше жили
    худшие расхождения в 192 px, 28 слайдов пары дают худшее отклонение
    0.020 px — а 5 слайдов из compare показывали ровно 0 и этого не ловили.

    Замерено на полном проходе (обе темы): 1651 дерево, 4943 слайда, сверено
    141468 рамок в dark и 141480 в light — 565 тысяч чисел на тему. Промахов
    по таблице 0, расхождений >0.5px нет, худшее отклонение 0.050 px.

    Чего здесь НЕ проверяется, и почему это не регрессия: «починено по донору:
    0». Донорская починка и не может сработать — перебор по определению весь
    лежит в таблице, промахнуться нечем. Её проверяют слайды-смеси из
    build_deck, где измерено 7 починок. Поэтому две проверки не заменяют друг
    друга: здесь широта (всё, что есть в таблице), там единственный путь для
    дерева, которого в таблице нет.

    only — ограничить одним шаблоном: полный проход поднимает браузер на
    4943 секции, а при разборе одного шаблона ждать это незачем.
    """
    from lxml import html as LH

    from .calibrate import CHUNK, _pages, collect
    from .convert import _Builder
    from .refdeck import build_deck as assemble, unique_variants

    picked = [g for g in unique_variants(theme)
              if only is None or g[1].template_id == only]
    if not picked:
        raise ValueError(f"нет деревьев шаблона {only!r}")

    err: dict[str, list[float]] = {}
    slides = compared = reflow = 0
    worst = 0.0
    missing: list = []
    repaired: list = []
    # Постранично по той же причине, что и в калибровке: дека на 4943 секции
    # кладёт браузер по памяти (_pages это и объясняет).
    for page in _pages(picked, chunk or CHUNK):
        part: list = []
        for _, head, rest in page:
            part.append(head)
            part += rest
        html = assemble(part, theme)
        sections = LH.document_fromstring(html).xpath(
            '//section[contains(@class,"slide")]')
        builder = _Builder(theme)
        for i, section in enumerate(sections, start=1):
            builder.add_slide(section, i)
        truth = collect(html)
        if len(truth) != len(sections):
            raise RuntimeError(f"браузер снял {len(truth)} секций, "
                               f"в разметке {len(sections)}")
        for i, slide in enumerate(truth, start=1):
            for it in slide["items"]:
                if it["authored"] or it["w"] <= 0 or it["h"] <= 0:
                    continue
                got = builder.placed.get((i, it["path"]))
                if got is None:
                    continue
                compared += 1
                for field, a, b in zip("xywh", got,
                                       (it["x"], it["y"], it["w"], it["h"])):
                    d = abs(a - b)
                    worst = max(worst, d)
                    if d > 0.5:
                        cls = (it["cls"] or it["tag"]).split()[0]
                        err.setdefault(
                            f"{slide['template']} {it['tag']}.{cls} {field}",
                            []).append(d)
        reflow += builder.stats["reflow"]
        # Сдвигаем номер слайда на уже пройденные страницы: builder на каждой
        # странице свой и нумерует с единицы, так что без сдвига «слайд 12» из
        # тринадцатой страницы указал бы на двенадцатый слайд всей деки — не на
        # тот, где промах. Числа это не меняет, но по такому отчёту искать
        # нечего.
        missing += [(slides + n, k) for n, k in builder.stats["missing"]]
        repaired += [(slides + n, k) for n, k in builder.stats["repaired"]]
        slides += len(sections)
    return {"slides": slides, "compared": compared, "errors": err,
            "missing": missing, "reflow": reflow, "repaired": repaired,
            "worst": worst, "trees": len(picked)}


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--theme", default="dark")
    ap.add_argument("--out", type=Path, help="куда сохранить PPTX")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--all", action="store_true",
                    help="по всему перебору, а не по деке с рыбой (долго: "
                         "браузер на 4943 секции)")
    ap.add_argument("--only", help="только один шаблон, например two-col-cards")
    args = ap.parse_args(argv)

    if args.all or args.only:
        r = compare_all(args.theme, args.only)
        print(f"деревьев {r['trees']}, слайдов {r['slides']} "
              f"(деревья + партнёры)")
    else:
        r = compare(args.theme, args.out)
    bad = sum(len(v) for v in r["errors"].values())
    print(f"слайдов {r['slides']}, сверено рамок {r['compared']}, "
          f"поправок на перенос {r['reflow']}")
    print(f"промахов по таблице: {len(r['missing'])}")
    for index, key in r["missing"][:10]:
        print(f"  слайд {index}: {key}")
    # Печатать это обязательно: без него «0 расхождений» не отличить от «починка
    # ни разу не вызвалась». Смеси легко выродиться в перебранное дерево —
    # см. build_deck, — и тогда проверка молча станет пустой.
    print(f"починено по донору: {len(r['repaired'])}")
    for index, key in r["repaired"][:10]:
        print(f"  слайд {index}: {key}")
    print(f"расхождений >0.5px: {bad} "
          f"({100 * bad / max(r['compared'] * 4, 1):.2f}% от всех чисел)")
    # Худшее отклонение печатаем и при нуле расхождений: «0 за порогом» и
    # «совпало до сотых» — разные новости, а порог 0.5px их не различает.
    if "worst" in r:
        print(f"худшее отклонение: {r['worst']:.3f} px")
    rank = sorted(r["errors"].items(), key=lambda kv: -max(kv[1]))
    for tag, ds in rank[:args.top]:
        print(f"  {max(ds):8.2f} px  x{len(ds):<5d} {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
