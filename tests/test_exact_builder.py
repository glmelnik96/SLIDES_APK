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


def test_choose_layout_eight_items_is_default():
    s = Section(heading="L", blocks=[ListBlock(items=[str(i) for i in range(8)])])
    assert _choose_layout(s) == "default"


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
