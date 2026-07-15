from htmlslides.parsers.base import (ImageBlock, InputDoc, ListBlock, Section,
                                     TableBlock, TextBlock)
from htmlslides.pipeline.exact_builder import (build_exact_plan, build_exact_slide,
                                               _choose_layout)

# 1x1 прозрачный PNG
_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_plan_one_to_one_verbatim():
    doc = InputDoc(title="Дека", sections=[
        Section(heading="A", blocks=[TextBlock(text="текст слово в слово")]),
        Section(heading="B", blocks=[ListBlock(items=["раз", "два"])]),
        Section(heading="C", blocks=[TextBlock(text="ещё")]),
    ])
    plan, warnings = build_exact_plan(doc)
    assert plan.title == "Дека"
    assert len(plan.slides) == 3
    assert all(s.freeform for s in plan.slides)
    assert all(s.content.get("exact") is True for s in plan.slides)
    assert [s.index for s in plan.slides] == [1, 2, 3]
    assert "текст слово в слово" in plan.slides[0].content["html"]
    assert "раз" in plan.slides[1].content["html"]
    assert warnings == []


def test_heading_rendered_as_content_head():
    html, _ = build_exact_slide(Section(heading="Заголовок", blocks=[]))
    assert '<div class="content-head">' in html
    assert "t-head-42" in html
    assert "Заголовок" in html


def test_raster_image_embedded_base64():
    section = Section(heading="Пик", blocks=[
        ImageBlock(data=_PNG, mime="image/png", alt="pic")])
    html, warnings = build_exact_slide(section)
    assert "data:image/png;base64," in html
    assert warnings == []


def test_vector_image_skipped_with_warning():
    section = Section(heading="V", blocks=[
        ImageBlock(data=b"\x01\x02", mime="image/x-emf")])
    html, warnings = build_exact_slide(section)
    assert "<img" not in html
    assert warnings and "пропущена" in warnings[0]


def test_html_escaped():
    html, _ = build_exact_slide(
        Section(heading="<b>", blocks=[TextBlock(text="a < b & c")]))
    assert "<b>" not in html.replace('class="content-head"', "")
    assert "a &lt; b &amp; c" in html


def test_choose_layout_table_is_default():
    from htmlslides.parsers.base import TableBlock
    s = Section(heading="T", blocks=[TableBlock(rows=[["a", "b"], ["c", "d"]])])
    assert _choose_layout(s) == "default"


def test_choose_layout_image_is_default():
    s = Section(heading="I", blocks=[ImageBlock(data=_PNG, mime="image/png")])
    assert _choose_layout(s) == "default"


def test_choose_layout_three_items_is_cards():
    s = Section(heading="C", blocks=[ListBlock(items=["раз", "два", "три"])])
    assert _choose_layout(s) == "cards"


def test_choose_layout_eight_items_is_list_cols():
    s = Section(heading="L", blocks=[ListBlock(items=[str(i) for i in range(8)])])
    assert _choose_layout(s) == "list-cols"


def test_choose_layout_long_list_items_is_default():
    long_item = "слово " * 40           # >120 символов — это абзац, не карточка
    s = Section(heading="P", blocks=[ListBlock(items=[long_item, long_item])])
    assert _choose_layout(s) == "default"


def test_choose_layout_numeric_is_hero_number():
    s = Section(heading="N", blocks=[TextBlock(text="99.9% аптайм")])
    assert _choose_layout(s) == "hero-number"


def test_choose_layout_short_text_is_statement():
    s = Section(heading="S", blocks=[TextBlock(text="Мы строим облако")])
    assert _choose_layout(s) == "statement"


def test_choose_layout_long_prose_is_default():
    prose = "Длинный абзац прозы. " * 20     # >120 символов
    s = Section(heading="D", blocks=[TextBlock(text=prose)])
    assert _choose_layout(s) == "default"


def test_choose_layout_empty_is_default():
    assert _choose_layout(Section(heading="E", blocks=[])) == "default"


def test_cards_five_items_yield_five_cards():
    s = Section(heading="Услуги", blocks=[
        ListBlock(items=["A", "B", "C", "D", "E"])])
    html, warns = build_exact_slide(s)
    assert html.count('class="card"') == 5
    assert warns == []


def test_cards_name_dash_description_split():
    s = Section(heading="X", blocks=[
        ListBlock(items=["IaaS — вычисления по запросу", "PaaS — платформа"])])
    html, _ = build_exact_slide(s)
    assert '<p class="t-head-36">IaaS</p>' in html
    assert '<p class="t-body-30">вычисления по запросу</p>' in html


def test_cards_plain_item_is_body():
    s = Section(heading="X", blocks=[ListBlock(items=["просто пункт", "второй"])])
    html, _ = build_exact_slide(s)
    assert '<div class="card"><p class="t-body-30">просто пункт</p></div>' in html


def test_cards_preserve_intro_text_verbatim():
    s = Section(heading="X", blocks=[
        TextBlock(text="Вводный абзац."),
        ListBlock(items=["раз", "два", "три"])])
    html, _ = build_exact_slide(s)
    assert "Вводный абзац." in html          # дословный intro не теряется
    assert html.count('class="card"') == 3


def test_cards_escape_html():
    s = Section(heading="X", blocks=[ListBlock(items=["a < b — c & d", "y"])])
    html, _ = build_exact_slide(s)
    assert "a &lt; b" in html
    assert "c &amp; d" in html


def test_hero_single_number_is_number_320():
    s = Section(heading="Аптайм", blocks=[TextBlock(text="99.9%")])
    html, _ = build_exact_slide(s)
    assert '<p class="t-number-320">99.9%</p>' in html


def test_hero_number_with_caption_uses_body_for_caption():
    s = Section(heading="X", blocks=[
        TextBlock(text="40%"), TextBlock(text="рост выручки")])
    html, _ = build_exact_slide(s)
    assert '<p class="t-number-320">40%</p>' in html       # единственное число
    assert '<p class="t-body-30">рост выручки</p>' in html # подпись — мелким


def test_hero_number_escapes_html():
    s = Section(heading="X", blocks=[TextBlock(text="<b> 5 ₽")])   # ₽ → numeric
    html, _ = build_exact_slide(s)
    assert "&lt;b&gt;" in html
    assert "<b>" not in html.replace('class="content-head"', "")


def test_statement_short_text_is_hero_156():
    s = Section(heading="Миссия", blocks=[TextBlock(text="Мы строим облако")])
    html, _ = build_exact_slide(s)
    assert '<p class="t-hero-156">Мы строим облако</p>' in html


def test_statement_two_blocks_each_rendered():
    s = Section(heading="X", blocks=[
        TextBlock(text="Первый тезис"), TextBlock(text="Второй тезис")])
    html, _ = build_exact_slide(s)
    assert '<p class="t-hero-156">Первый тезис</p>' in html
    assert '<p class="t-hero-156">Второй тезис</p>' in html


def test_statement_escapes_html():
    s = Section(heading="X", blocks=[TextBlock(text="a < b & c")])
    html, _ = build_exact_slide(s)
    assert "a &lt; b &amp; c" in html


# --- A: числовая таблица → настоящий слайд bar-chart (переиспользуем шаблон) ---

def test_numeric_table_becomes_bar_chart():
    doc = InputDoc(title="D", sections=[
        Section(heading="Рост выручки", blocks=[
            TableBlock(rows=[["Год", "Выручка"],
                             ["2023", "40%"],
                             ["2024", "65%"],
                             ["2025", "90%"]])])])
    plan, warns = build_exact_plan(doc)
    s = plan.slides[0]
    assert s.template_id == "bar-chart"
    assert s.freeform is False
    assert s.content["title"] == "Рост выручки"
    assert [b["label"] for b in s.content["bars"]] == ["2023", "2024", "2025"]
    assert [b["value"] for b in s.content["bars"]] == ["40%", "65%", "90%"]
    assert warns == []


def test_text_table_stays_verbatim_table():
    doc = InputDoc(title="D", sections=[
        Section(heading="Сравнение", blocks=[
            TableBlock(rows=[["Функция", "Описание"],
                             ["API", "полный доступ"],
                             ["SLA", "поддержка 24 на 7"]])])])
    s = build_exact_plan(doc)[0].slides[0]
    assert s.freeform is True
    assert s.template_id is None
    assert 'class="exact-table"' in s.content["html"]


def test_numeric_table_with_extra_prose_stays_table():
    doc = InputDoc(title="D", sections=[
        Section(heading="Метрики", blocks=[
            TextBlock(text="Вступление один."),
            TextBlock(text="Вступление два."),
            TableBlock(rows=[["A", "10"], ["B", "20"]])])])
    s = build_exact_plan(doc)[0].slides[0]
    assert s.freeform is True                       # два абзаца нельзя выбросить
    assert "Вступление один." in s.content["html"]
    assert "Вступление два." in s.content["html"]


def test_numeric_table_over_six_rows_stays_table():
    rows = [[f"R{i}", str(i * 10)] for i in range(1, 8)]   # 7 строк данных
    doc = InputDoc(title="D", sections=[
        Section(heading="Много", blocks=[TableBlock(rows=rows)])])
    assert build_exact_plan(doc)[0].slides[0].freeform is True


def test_numeric_table_intro_becomes_subtitle():
    doc = InputDoc(title="D", sections=[
        Section(heading="Рост", blocks=[
            TextBlock(text="за три года"),
            TableBlock(rows=[["2023", "40%"], ["2024", "65%"]])])])
    s = build_exact_plan(doc)[0].slides[0]
    assert s.template_id == "bar-chart"
    assert s.content["subtitle"] == "за три года"


def test_numeric_table_long_label_stays_table():
    long_label = "Очень длинная подпись категории, которая точно не влезает в бар"
    doc = InputDoc(title="D", sections=[
        Section(heading="X", blocks=[
            TableBlock(rows=[[long_label, "10"], ["B", "20"]])])])
    assert build_exact_plan(doc)[0].slides[0].freeform is True


# --- B: числовой список «Название — 85%» → тот же слайд bar-chart ---

def test_numeric_list_becomes_bar_chart():
    doc = InputDoc(title="D", sections=[
        Section(heading="Метрики", blocks=[
            ListBlock(items=["Аптайм — 99.9%", "Латентность — 12",
                             "Экономия — 40%"])])])
    plan, warns = build_exact_plan(doc)
    s = plan.slides[0]
    assert s.template_id == "bar-chart"
    assert s.freeform is False
    assert [b["label"] for b in s.content["bars"]] == [
        "Аптайм", "Латентность", "Экономия"]
    assert [b["value"] for b in s.content["bars"]] == ["99.9%", "12", "40%"]
    assert warns == []


def test_text_list_stays_cards():
    doc = InputDoc(title="D", sections=[
        Section(heading="Услуги", blocks=[
            ListBlock(items=["IaaS — вычисления по запросу", "PaaS — платформа"])])])
    s = build_exact_plan(doc)[0].slides[0]
    assert s.freeform is True
    assert s.template_id is None
    assert 'class="card"' in s.content["html"]


def test_mixed_numeric_list_stays_cards():
    # хотя бы один пункт без числовой правой части → не график, карточки
    doc = InputDoc(title="D", sections=[
        Section(heading="X", blocks=[
            ListBlock(items=["Аптайм — 99.9%", "IaaS — вычисления по запросу"])])])
    s = build_exact_plan(doc)[0].slides[0]
    assert s.freeform is True
    assert 'class="card"' in s.content["html"]


def test_numeric_list_intro_becomes_subtitle():
    doc = InputDoc(title="D", sections=[
        Section(heading="Метрики", blocks=[
            TextBlock(text="ключевые показатели"),
            ListBlock(items=["Аптайм — 99.9%", "Экономия — 40%"])])])
    s = build_exact_plan(doc)[0].slides[0]
    assert s.template_id == "bar-chart"
    assert s.content["subtitle"] == "ключевые показатели"


def test_numeric_list_over_six_items_stays_verbatim():
    items = [f"M{i} — {i * 10}" for i in range(1, 8)]   # 7 пунктов данных
    doc = InputDoc(title="D", sections=[
        Section(heading="Много", blocks=[ListBlock(items=items)])])
    assert build_exact_plan(doc)[0].slides[0].freeform is True


def test_numeric_list_long_value_stays_cards():
    # правая часть длиннее лимита value (8) → не влезает в бар, карточки
    doc = InputDoc(title="D", sections=[
        Section(heading="X", blocks=[
            ListBlock(items=["Скорость — 99.9% в пике нагрузки", "Рост — 40%"])])])
    s = build_exact_plan(doc)[0].slides[0]
    assert s.freeform is True
    assert 'class="card"' in s.content["html"]


# --- C: длинный список (7+ пунктов) → колонки (2/3/4 по объёму) ---

def test_choose_layout_seven_items_is_list_cols():
    s = Section(heading="L", blocks=[
        ListBlock(items=[f"пункт {i}" for i in range(7)])])
    assert _choose_layout(s) == "list-cols"


def test_list_2col_renders_two_column_class():
    s = Section(heading="Возможности", blocks=[
        ListBlock(items=[f"функция {i}" for i in range(8)])])
    html, warns = build_exact_slide(s)
    assert "exact-list--2col" in html
    assert html.count("<li") == 8            # все пункты сохранены дословно
    assert warns == []


def test_list_2col_preserves_order_tag():
    s = Section(heading="Шаги", blocks=[
        ListBlock(items=[f"шаг {i}" for i in range(7)], ordered=True)])
    html, _ = build_exact_slide(s)
    assert "<ol" in html and "exact-list--2col" in html


def test_list_2col_preserves_intro_verbatim():
    s = Section(heading="X", blocks=[
        TextBlock(text="Вводный абзац перед списком."),
        ListBlock(items=[f"пункт {i}" for i in range(7)])])
    html, _ = build_exact_slide(s)
    assert "Вводный абзац перед списком." in html
    assert "exact-list--2col" in html


def test_list_cols_long_items_still_columnize():
    # Раньше длинные пункты падали в плоский default и жались в одну колонку слева;
    # теперь длинный список тоже раскладываем в колонки, чтобы нагруженный слайд
    # заполнял холст. Длина пункта ограничивает шириной → максимум 2 колонки.
    long_item = "очень длинный пункт списка " * 4     # >80 символов на пункт
    s = Section(heading="X", blocks=[
        ListBlock(items=[long_item for _ in range(7)])])
    assert _choose_layout(s) == "list-cols"
    html, _ = build_exact_slide(s)
    assert "exact-list--2col" in html


def test_list_cols_fifteen_short_items_three_columns():
    # Больше пунктов → больше колонок: 15 коротких пунктов заполняют холст в 3 колонки.
    s = Section(heading="Много", blocks=[
        ListBlock(items=[f"пункт {i}" for i in range(15)])])
    html, warns = build_exact_slide(s)
    assert "exact-list--3col" in html
    assert html.count("<li") == 15
    assert warns == []


def test_list_cols_twentyfour_short_items_four_columns():
    # Совсем нагруженный список коротких пунктов → 4 колонки.
    s = Section(heading="Очень много", blocks=[
        ListBlock(items=[f"п{i}" for i in range(24)])])
    html, _ = build_exact_slide(s)
    assert "exact-list--4col" in html
    assert html.count("<li") == 24


def test_list_cols_many_long_items_capped_two_columns():
    # 15 длинных пунктов: по объёму просилось бы 3 колонки, но длина ограничивает
    # шириной — держим 2 колонки, чтобы предложения читались.
    long_item = "очень длинный пункт списка про сервис "     # >80 при повторе
    s = Section(heading="Длинные", blocks=[
        ListBlock(items=[long_item * 3 for _ in range(15)])])
    html, _ = build_exact_slide(s)
    assert "exact-list--2col" in html
    assert "exact-list--3col" not in html


def test_list_cols_preserves_all_items_verbatim():
    items = [f"уникальный пункт номер {i}" for i in range(20)]
    s = Section(heading="Список", blocks=[ListBlock(items=items)])
    html, warns = build_exact_slide(s)
    assert html.count("<li") == 20
    for it in items:
        assert it in html
    assert warns == []


# --- D: числа-герои с подписями внутри смешанного слайда → KPI-карточки ---
# Реальный кейс s2: таблица + голые числа (1389/351/151) с подписями. Раньше
# _build_default вываливал числа плоским текстом; теперь пара «число + подпись»
# становится бренд-карточкой с крупной цифрой, а таблица/проза остаются дословно.

def test_default_number_caption_pairs_become_kpi_cards():
    s = Section(heading="Итоги", blocks=[
        TableBlock(rows=[["Категория", "A"], ["Апрель", "3"]]),
        TextBlock(text="1389"),
        ListBlock(items=["Агентов создано на платформе", "AI Agents"]),
        TextBlock(text="351"),
        TextBlock(text="EvoClaw агент запущен"),
        TextBlock(text="151"),
        TextBlock(text="Ouroboros агент запущен"),
    ])
    html, warns = build_exact_slide(s)
    assert '<p class="t-hero-156">1389</p>' in html      # число — крупно
    assert '<p class="t-hero-156">351</p>' in html
    assert '<p class="t-hero-156">151</p>' in html
    assert html.count('class="card"') == 3               # три KPI-карточки
    assert "Агентов создано на платформе" in html        # подписи дословно
    assert "EvoClaw агент запущен" in html
    assert 'class="exact-table"' in html                 # таблица осталась таблицей
    assert warns == []


def test_default_bare_number_without_caption_stays_text():
    # одинокое число без подписи (перед таблицей) НЕ превращается в KPI-героя
    s = Section(heading="X", blocks=[
        TextBlock(text="2"),
        TableBlock(rows=[["a", "b"], ["c", "d"]]),
    ])
    html, _ = build_exact_slide(s)
    assert '<p class="t-hero-156">2</p>' not in html
    assert '<p class="t-body-30">2</p>' in html           # осталось обычным текстом


def test_default_kpi_preserves_order_table_before_numbers():
    s = Section(heading="X", blocks=[
        TableBlock(rows=[["a", "b"], ["c", "d"]]),
        TextBlock(text="40%"),
        TextBlock(text="рост выручки"),
    ])
    html, _ = build_exact_slide(s)
    assert html.index('exact-table') < html.index('t-hero-156')   # таблица раньше KPI


def test_default_long_number_line_not_kpi():
    # таблица уводит секцию в default; длинный абзац с числа в начале — НЕ метрика
    s = Section(heading="X", blocks=[
        TableBlock(rows=[["a", "b"], ["c", "d"]]),
        TextBlock(text="2025 год стал переломным для всей отрасли облаков"),  # >16
        TextBlock(text="и это только начало"),
    ])
    html, _ = build_exact_slide(s)
    assert 't-hero-156' not in html                      # KPI не сработал
    assert "2025 год стал переломным" in html            # проза дословно
