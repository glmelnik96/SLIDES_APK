from htmlslides.parsers.base import (ImageBlock, InputDoc, ListBlock, Section,
                                     TextBlock)
from htmlslides.pipeline.exact_builder import build_exact_plan, build_exact_slide

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
