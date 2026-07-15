"""Детерминированный рендер exact-режима: Section → html-фрагмент, InputDoc → DeckPlan.

Без LLM: текст переносим дословно, картинки встраиваем base64. Каждый слайд —
freeform=True с готовым html и меткой content["exact"]=True (ассемблер по ней
кладёт контент в .exact-zone и включает JS-подгонку масштаба).
"""
from __future__ import annotations

import base64
import re
from html import escape

from ..library import TemplateLibrary
from ..models import DeckPlan, SlidePlan
from ..parsers.base import (CodeBlock, ImageBlock, InputDoc, ListBlock, Section,
                            TableBlock, TextBlock)

_RASTER = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}


def _img_html(block: ImageBlock) -> tuple[str, list[str]]:
    mime = (block.mime or "").lower()
    alt = escape(block.alt or "")
    if block.data and mime in _RASTER:
        b64 = base64.b64encode(block.data).decode("ascii")
        return f'<img class="exact-img" alt="{alt}" src="data:{mime};base64,{b64}">', []
    if block.src:                              # md: путь/URL
        return f'<img class="exact-img" alt="{alt}" src="{escape(block.src)}">', []
    fmt = block.mime or "неизвестен"
    return "", [f"картинка пропущена (формат {fmt} не поддержан)"]


def _block_html(block) -> tuple[str, list[str]]:
    if isinstance(block, TextBlock):
        return f'<p class="t-body-30">{escape(block.text).replace(chr(10), "<br>")}</p>', []
    if isinstance(block, ListBlock):
        tag = "ol" if block.ordered else "ul"
        items = "".join(f'<li class="t-body-30">{escape(it)}</li>' for it in block.items)
        return f'<{tag} class="exact-list">{items}</{tag}>', []
    if isinstance(block, TableBlock):
        rows = "".join(
            "<tr>" + "".join(f'<td class="t-body-30">{escape(c)}</td>' for c in row) + "</tr>"
            for row in block.rows)
        return f'<table class="exact-table">{rows}</table>', []
    if isinstance(block, ImageBlock):
        return _img_html(block)
    if isinstance(block, CodeBlock):
        return f'<pre class="exact-code">{escape(block.text)}</pre>', []
    return "", []


# Число с единицей/суффиксом — сигнал «здесь данные, а не проза» (совпадает с
# planner._NUMERIC_TOKEN; дублируем, чтобы exact_builder оставался автономным).
_NUMERIC_TOKEN = re.compile(
    r"\d[\d.,]*\s*(?:%|млрд|млн|тыс|₽|руб|x|×|раз|ГБ|ТБ|TB|GB|Tier|ФЗ|сек|мс|ч|сут)",
    re.IGNORECASE)
_HERO_MAX = 120       # «короткий» тезис/число — для крупной подачи
_CARD_ITEM_MAX = 120  # пункт списка ещё «карточный», а не абзац
_COL_ITEM_MAX = 80    # длинный пункт (предложение): нужна широкая колонка → максимум 2
_COL_ITEM_HALF = 40   # средний пункт: до 3 колонок; короче — до 4
_COL_MIN_ITEMS = 7    # с этого числа пунктов список раскладываем в колонки


def _list_columns(items: list[str]) -> int:
    """Число колонок для длинного списка по объёму. Больше пунктов → больше
    колонок (ориентир ≤6 пунктов на колонку), но длина пункта ограничивает
    ширину: длинные предложения требуют широкой колонки, поэтому потолок колонок
    падает. <7 пунктов не колонкуем (вернём 1 — до колонок дело не доходит)."""
    n = len(items)
    if n < _COL_MIN_ITEMS:
        return 1
    longest = max(len(it) for it in items)
    length_cap = 2 if longest > _COL_ITEM_MAX else 3 if longest > _COL_ITEM_HALF else 4
    want = min(4, (n + 5) // 6)          # ceil(n/6), потолок 4 колонки
    return max(2, min(want, length_cap))

# Голая строка-число («1389», «40%», «130+», «99,982%», «18 мес.») — сигнал «это
# KPI-цифра, а не проза». Отличие от _NUMERIC_TOKEN: тот требует единицу ПОСЛЕ
# числа, а голое «1389» единицы не имеет. Ограничение длиной 16 не даёт дате или
# длинному номеру внутри предложения притвориться метрикой.
_METRIC_STRIP = re.compile(
    r"%|₽|\+|×|"
    r"\b(?:млрд|млн|тыс|руб|ГБ|ТБ|TB|GB|шт|чел|мес|сек|мс|раз)\.?",
    re.IGNORECASE)
_METRIC_CORE = re.compile(r"[+\-–—]?\d[\d\s.,  ]*")


def _is_metric_line(text: str) -> bool:
    """Вся строка — одно число (с необязательной единицей/знаком)? «1389», «40%»,
    «130+», «99,982%», «18 мес.» → True; «EvoClaw запущен», «2025 год стал…» → False."""
    t = text.strip()
    if not t or len(t) > 16 or not any(ch.isdigit() for ch in t):
        return False
    core = _METRIC_STRIP.sub("", t).strip().rstrip("+×xхХ ").strip()
    return bool(core) and _METRIC_CORE.fullmatch(core) is not None

# Разбор ячейки таблицы в число для геометрии графика (или None, если числа нет).
# Терпимо к разделителям: пробелы/неразрывные — тысячи, запятая — десятичная точка
# (та же логика, что assembler._num, но здесь важно вернуть None на нечисле, чтобы
# отличить «это данные для графика» от «это текст, оставить таблицей»).
_NUM_RE = re.compile(r"-?\d[\d   .,]*")


def _cell_number(value: str) -> float | None:
    m = _NUM_RE.search(str(value))
    if not m:
        return None
    s = m.group(0).replace(" ", "").replace("\u00a0", "").replace("\u202f", "")
    had_comma = "," in s
    s = s.replace(",", ".")
    if s.count(".") > 1:                 # несколько точек = тысячные разделители
        if had_comma:
            head, _, tail = s.rpartition(".")
            s = head.replace(".", "") + "." + tail
        else:
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _all_list_items(section: Section) -> list[str]:
    return [it for b in section.blocks if isinstance(b, ListBlock) for it in b.items]


def _plain_text(section: Section) -> str:
    parts: list[str] = []
    for block in section.blocks:
        if isinstance(block, TextBlock):
            parts.append(block.text)
        elif isinstance(block, ListBlock):
            parts.extend(block.items)
    return " ".join(parts)


def _choose_layout(section: Section) -> str:
    """Секция → имя flow-раскладки: cards | list-cols | hero-number | statement
    | default.

    Порядок проб важен: таблицы и картинки уходят в безопасный default (текущий
    рендер), затем короткие списки 2-6 → cards, длинные списки 7+ пунктов →
    list-cols (2/3/4 колонки по объёму, число колонок считает _list_columns),
    короткий текст → числа/тезис.
    """
    blocks = section.blocks
    if any(isinstance(b, TableBlock) for b in blocks):
        return "default"
    if any(isinstance(b, ImageBlock) for b in blocks):
        return "default"
    items = _all_list_items(section)
    if 2 <= len(items) <= 6 and all(len(it) <= _CARD_ITEM_MAX for it in items):
        return "cards"
    list_blocks = [b for b in blocks if isinstance(b, ListBlock)]
    if len(list_blocks) == 1 and len(items) >= _COL_MIN_ITEMS:
        return "list-cols"
    text_blocks = [b for b in blocks if isinstance(b, TextBlock)]
    joined = _plain_text(section)
    if not items and 1 <= len(text_blocks) <= 2 and 0 < len(joined) <= _HERO_MAX:
        return "hero-number" if _NUMERIC_TOKEN.search(joined) else "statement"
    return "default"


def _head_html(section: Section) -> str:
    if not section.heading:
        return ""
    return ('<div class="content-head"><h3 class="content-head-title t-head-42">'
            f'{escape(section.heading)}</h3></div>')


def _is_caption_block(block) -> bool:
    """Подпись к числу-герою: текст-НЕ-метрика или список. Таблица/картинка/код
    обрывают сбор подписи — это уже отдельный блок контента, а не подпись."""
    if isinstance(block, TextBlock):
        return not _is_metric_line(block.text)
    return isinstance(block, ListBlock)


def _segment_blocks(blocks: list) -> list[tuple[str, list]]:
    """Блоки секции → сегменты с сохранением порядка. ("stats", [(число, подписи)])
    — подряд идущие пары «число-герой + подпись»; ("blocks", [...]) — всё прочее.
    Число без следующей подписи остаётся обычным блоком (в "blocks")."""
    segments: list[tuple[str, list]] = []
    plain: list = []
    stats: list = []

    def flush_plain():
        if plain:
            segments.append(("blocks", plain[:]))
            plain.clear()

    def flush_stats():
        if stats:
            segments.append(("stats", stats[:]))
            stats.clear()

    i, n = 0, len(blocks)
    while i < n:
        b = blocks[i]
        if isinstance(b, TextBlock) and _is_metric_line(b.text):
            j = i + 1
            caps: list = []
            while j < n and _is_caption_block(blocks[j]):   # metric block стоп-кадр
                caps.append(blocks[j])
                j += 1
            if caps:                                        # число + подпись = KPI
                flush_plain()
                stats.append((b, caps))
                i = j
                continue
        flush_stats()
        plain.append(b)
        i += 1
    flush_stats()
    flush_plain()
    return segments


def _stats_row_html(stats: list) -> str:
    """Пары (число, подписи) → бренд-карточки KPI по 3 в ряд: крупная цифра
    (.t-hero-156) + дословные подписи (_block_html). Масштаб под зону даёт
    autofitExact у .exact-fit — ряд гарантированно вписывается."""
    cells: list[str] = []
    for num_block, caps in stats:
        num = escape(num_block.text).replace(chr(10), "<br>")
        caps_html = "".join(_block_html(c)[0] for c in caps)
        cells.append('<div class="col"><div class="card">'
                     f'<p class="t-hero-156">{num}</p>{caps_html}</div></div>')
    rows = [cells[k:k + 3] for k in range(0, len(cells), 3)]
    return "".join(f'<div class="row">{"".join(r)}</div>' for r in rows)


def _blocks_group_html(blocks: list) -> tuple[str, list[str]]:
    """Группа обычных блоков → .exact-text (проза/списки/таблицы), картинки —
    сбоку в .exact-cols. Прежняя тело-логика build_default (дословно)."""
    warnings: list[str] = []
    text_parts: list[str] = []
    img_parts: list[str] = []
    for block in blocks:
        html, warns = _block_html(block)
        warnings.extend(warns)
        if not html:
            continue
        (img_parts if isinstance(block, ImageBlock) else text_parts).append(html)
    text_html = "".join(text_parts)
    if img_parts:
        body = ('<div class="exact-cols">'
                f'<div class="exact-text">{text_html}</div>'
                f'<div class="exact-media">{"".join(img_parts)}</div></div>')
    else:
        body = f'<div class="exact-text">{text_html}</div>'
    return body, warnings


def _build_default(section: Section) -> tuple[str, list[str]]:
    """Безопасный дефолт для смешанного контента. Пары «число-герой + подпись»
    поднимаем в KPI-карточки (крупная цифра + дословная подпись), всё прочее
    (проза/списки/таблицы/картинки) — как прежде. Порядок блоков сохраняется,
    текст дословный."""
    warnings: list[str] = []
    head = _head_html(section)
    parts: list[str] = []
    for kind, items in _segment_blocks(section.blocks):
        if kind == "stats":
            parts.append(_stats_row_html(items))
            continue
        html, warns = _blocks_group_html(items)
        warnings.extend(warns)
        parts.append(html)
    return head + "".join(parts), warnings


def _card_html(item: str) -> str:
    if " — " in item:
        name, desc = item.split(" — ", 1)
        inner = (f'<p class="t-head-36">{escape(name)}</p>'
                 f'<p class="t-body-30">{escape(desc)}</p>')
    else:
        inner = f'<p class="t-body-30">{escape(item)}</p>'
    return f'<div class="col"><div class="card">{inner}</div></div>'


def _build_cards(section: Section) -> tuple[str, list[str]]:
    """Список 2-6 пунктов → бренд-карточки в .row/.col по 3 в ряд. Не-списочный
    текст (вводный абзац) сохраняем дословно перед сеткой."""
    warnings: list[str] = []
    head = _head_html(section)
    intro_parts: list[str] = []
    for block in section.blocks:
        if isinstance(block, ListBlock):
            continue
        html, warns = _block_html(block)
        warnings.extend(warns)
        if html:
            intro_parts.append(html)
    intro = (f'<div class="exact-text">{"".join(intro_parts)}</div>'
             if intro_parts else "")
    cards = [_card_html(it) for it in _all_list_items(section)]
    rows = [cards[i:i + 3] for i in range(0, len(cards), 3)]
    grid = "".join(f'<div class="row">{"".join(row)}</div>' for row in rows)
    return head + intro + grid, warnings


_COL_CLASS = {2: "exact-list--2col", 3: "exact-list--3col", 4: "exact-list--4col"}


def _build_list_multicol(section: Section) -> tuple[str, list[str]]:
    """Длинный список (7+ пунктов) → колонки через CSS column-count. Число колонок
    (2/3/4) считает _list_columns по объёму и длине пунктов — нагруженный список
    заполняет холст, а не жмётся в одну колонку слева. Список остаётся ОДНИМ
    <ul>/<ol>: нумерация сквозная, порядок и текст пунктов сохранены (дословно).
    Вводный абзац (если есть) — перед списком."""
    warnings: list[str] = []
    head = _head_html(section)
    intro_parts: list[str] = []
    list_block: ListBlock | None = None
    for block in section.blocks:
        if isinstance(block, ListBlock):
            list_block = block
            continue
        html, warns = _block_html(block)
        warnings.extend(warns)
        if html:
            intro_parts.append(html)
    intro = (f'<div class="exact-text">{"".join(intro_parts)}</div>'
             if intro_parts else "")
    items = _all_list_items(section)
    tag = "ol" if (list_block and list_block.ordered) else "ul"
    lis = "".join(f'<li class="t-body-30">{escape(it)}</li>' for it in items)
    cls = _COL_CLASS[_list_columns(items)]
    grid = f'<{tag} class="exact-list {cls}">{lis}</{tag}>'
    return head + intro + grid, warnings


def _build_hero_number(section: Section) -> tuple[str, list[str]]:
    """1-2 коротких текст-блока с числом-героем: число крупно (.t-number-320 для
    единственного числа, иначе .t-hero-156), подпись — .t-body-30. В один .row."""
    head = _head_html(section)
    blocks = [b for b in section.blocks if isinstance(b, TextBlock)]
    numeric_count = sum(1 for b in blocks if _NUMERIC_TOKEN.search(b.text))
    cols: list[str] = []
    for b in blocks:
        text = escape(b.text).replace(chr(10), "<br>")
        if _NUMERIC_TOKEN.search(b.text):
            cls = "t-number-320" if numeric_count == 1 else "t-hero-156"
        else:
            cls = "t-body-30"
        cols.append(f'<div class="col"><p class="{cls}">{text}</p></div>')
    body = f'<div class="row">{"".join(cols)}</div>'
    return head + body, []


def _build_statement(section: Section) -> tuple[str, list[str]]:
    """Короткий тезис (1-2 текст-блока без чисел) крупно в .t-hero-156. Каждый
    блок — своим абзацем (дословность per-block, переносы строк → <br>)."""
    head = _head_html(section)
    parts = [f'<p class="t-hero-156">{escape(b.text).replace(chr(10), "<br>")}</p>'
             for b in section.blocks if isinstance(b, TextBlock)]
    body = f'<div class="exact-text">{"".join(parts)}</div>'
    return head + body, []


def build_exact_slide(section: Section) -> tuple[str, list[str]]:
    """Section → (html-фрагмент, предупреждения). Диспетчер flow-раскладок:
    по _choose_layout выбираем бренд-строитель, иначе — безопасный default."""
    layout = _choose_layout(section)
    if layout == "cards":
        return _build_cards(section)
    if layout == "list-cols":
        return _build_list_multicol(section)
    if layout == "hero-number":
        return _build_hero_number(section)
    if layout == "statement":
        return _build_statement(section)
    return _build_default(section)


_CHART_TEMPLATE = "bar-chart"


def _table_bars(block: TableBlock) -> list[dict] | None:
    """Числовая 2-колоночная таблица → bars для bar-chart, иначе None.

    Первый столбец — подпись (label), второй — величина (value, обязана
    парситься в число). Строку-шапку (второй столбец не число) отбрасываем.
    Требуем ≥2 строки данных и строго 2 столбца: иначе это не «данные для
    графика», а обычная таблица — оставляем как есть (дословно)."""
    rows = [r for r in block.rows if any((c or "").strip() for c in r)]
    if len(rows) < 2 or any(len(r) != 2 for r in rows):
        return None
    if _cell_number(rows[0][1]) is None:        # шапка: второй столбец не число
        rows = rows[1:]
    if len(rows) < 2:
        return None
    bars: list[dict] = []
    for label, value in rows:
        if _cell_number(value) is None:         # хоть одна нечисловая величина —
            return None                         # это не график, оставляем таблицей
        bars.append({"label": label.strip(), "value": value.strip()})
    return bars


def _list_bars(items: list[str]) -> list[dict] | None:
    """Список пунктов «Название — 85%» → bars (label=Название, value=85%), иначе
    None. Требуем разделитель « — » и числовую правую часть в КАЖДОМ пункте, ≥2
    пункта. Смешанный список («IaaS — вычисления по запросу») отдаём как None —
    это карточки, а не график (тот же разделитель, что и в _card_html)."""
    items = [it for it in items if it.strip()]
    if len(items) < 2:
        return None
    bars: list[dict] = []
    for item in items:
        if " — " not in item:
            return None
        name, value = item.split(" — ", 1)
        if _cell_number(value) is None:         # правая часть не число — не график
            return None
        bars.append({"label": name.strip(), "value": value.strip()})
    return bars


def _section_bars(section: Section) -> list[dict] | None:
    """Данные секции → bars: одна числовая таблица ИЛИ один числовой список.
    Смесь таблицы и списка не трогаем (вернём None — уйдёт в freeform)."""
    tables = [b for b in section.blocks if isinstance(b, TableBlock)]
    lists = [b for b in section.blocks if isinstance(b, ListBlock)]
    if len(tables) == 1 and not lists:
        return _table_bars(tables[0])
    if len(lists) == 1 and not tables:
        return _list_bars(lists[0].items)
    return None


def _chart_slide_plan(section: Section, index: int,
                      library: TemplateLibrary) -> SlidePlan | None:
    """Секция = единственный числовой блок данных (таблица ИЛИ список «имя — N»)
    плюс максимум один вводный абзац → настоящий слайд bar-chart. Переиспользуем
    и разметку шаблона, и его лимиты: если контент не проходит слот-контракт
    (7+ баров, длинная подпись/значение) — возвращаем None, и секция уходит в
    дословный freeform (таблица/карточки). Так дословность не страдает — график
    рисуем, только когда данные реально в него влезают."""
    if not section.heading:
        return None
    data = [b for b in section.blocks if isinstance(b, (TableBlock, ListBlock))]
    others = [b for b in section.blocks if not isinstance(b, (TableBlock, ListBlock))]
    if len(data) != 1:
        return None
    intro = [b for b in others if isinstance(b, TextBlock)]
    if len(intro) != len(others) or len(intro) > 1:   # только данные + ≤1 абзац
        return None
    bars = _section_bars(section)
    if not bars:
        return None
    content: dict = {"title": section.heading, "bars": bars}
    if intro:
        content["subtitle"] = intro[0].text
    if library.validate_content(_CHART_TEMPLATE, content):
        return None                             # не влезло в контракт — freeform
    spec = library.get(_CHART_TEMPLATE)
    return SlidePlan(index=index, type=spec.type, template_id=_CHART_TEMPLATE,
                     content=content)


def build_exact_plan(doc: InputDoc) -> tuple[DeckPlan, list[str]]:
    """InputDoc → (DeckPlan 1-в-1, предупреждения). N секций → N SlidePlan.

    Секцию с чистой числовой таблицей отдаём в настоящий шаблон bar-chart
    (график вместо таблицы), всё остальное — дословный freeform-рендер."""
    library = TemplateLibrary.load()
    slides: list[SlidePlan] = []
    all_warnings: list[str] = []
    for i, section in enumerate(doc.sections, start=1):
        chart = _chart_slide_plan(section, i, library)
        if chart is not None:
            slides.append(chart)
            continue
        html, warns = build_exact_slide(section)
        all_warnings.extend(f"слайд {i}: {w}" for w in warns)
        slides.append(SlidePlan(index=i, type="exact", freeform=True,
                                content={"html": html, "exact": True}))
    return DeckPlan(title=doc.title, slides=slides), all_warnings
