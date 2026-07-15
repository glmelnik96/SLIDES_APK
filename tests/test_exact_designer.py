from htmlslides.parsers.base import (CodeBlock, ImageBlock, ListBlock, Section,
                                     TableBlock, TextBlock)
from htmlslides.pipeline.exact_designer import Atom, atomize


def test_atomize_heading_is_a1():
    atoms = atomize(Section(heading="Заголовок", blocks=[TextBlock(text="абзац")]))
    assert atoms[0] == Atom(id="a1", kind="heading", text="Заголовок")
    assert atoms[1].id == "a2"
    assert atoms[1].kind == "paragraph"
    assert atoms[1].text == "абзац"


def test_atomize_no_heading_starts_at_a1():
    atoms = atomize(Section(heading="", blocks=[TextBlock(text="первый")]))
    assert [a.id for a in atoms] == ["a1"]
    assert atoms[0].kind == "paragraph"
    assert atoms[0].text == "первый"


def test_atomize_list_items_are_separate_atoms():
    atoms = atomize(Section(heading="H", blocks=[
        ListBlock(items=["раз", "два", "три"])]))
    assert [a.kind for a in atoms] == ["heading", "list_item", "list_item", "list_item"]
    assert [a.text for a in atoms[1:]] == ["раз", "два", "три"]
    assert [a.id for a in atoms] == ["a1", "a2", "a3", "a4"]


def test_atomize_structural_blocks_keep_block_reference():
    table = TableBlock(rows=[["a", "b"]])
    image = ImageBlock(src="p.png", alt="pic")
    code = CodeBlock(text="x=1")
    atoms = atomize(Section(heading="", blocks=[table, image, code]))
    assert [a.kind for a in atoms] == ["table", "image", "code"]
    assert atoms[0].block is table
    assert atoms[1].block is image
    assert atoms[2].block is code


def test_atomize_verbatim_text_char_for_char():
    atoms = atomize(Section(heading="", blocks=[
        TextBlock(text="строка A\nстрока B")]))
    assert atoms[0].text == "строка A\nстрока B"


from htmlslides.pipeline.exact_designer import _render, _MARK_RE  # noqa: E402


def _atoms_from(section):
    from htmlslides.pipeline.exact_designer import atomize
    return atomize(section)


def test_render_substitutes_verbatim_escaped():
    atoms = _atoms_from(Section(heading="Заголовок", blocks=[
        TextBlock(text="a < b & c")]))
    html = _render('<h3>{{a1}}</h3><p>{{a2}}</p>', atoms)
    assert "<h3>Заголовок</h3>" in html
    assert "a &lt; b &amp; c" in html
    assert "{{a" not in html                      # все метки заменены


def test_render_paragraph_newlines_to_br():
    atoms = _atoms_from(Section(heading="", blocks=[
        TextBlock(text="строка A\nстрока B")]))
    html = _render("<p>{{a1}}</p>", atoms)
    assert "строка A<br>строка B" in html


def test_render_word_range_bolds_exact_words():
    atoms = _atoms_from(Section(heading="", blocks=[
        TextBlock(text="один два три четыре")]))
    html = _render("<p>{{a1:2-3}}</p>", atoms)
    assert html == "<p>один <b>два три</b> четыре</p>"


def test_render_word_range_single_word():
    atoms = _atoms_from(Section(heading="", blocks=[
        TextBlock(text="альфа бета")]))
    html = _render("<p>{{a1:1-1}}</p>", atoms)
    assert html == "<p><b>альфа</b> бета</p>"


def test_render_invalid_range_falls_back_to_plain_text():
    atoms = _atoms_from(Section(heading="", blocks=[
        TextBlock(text="одно слово-мало")]))
    html = _render("<p>{{a1:5-9}}</p>", atoms)          # слов меньше, чем 5
    assert "<b>" not in html
    assert "одно слово-мало" in html


def test_render_table_atom_expands_via_block_html():
    atoms = _atoms_from(Section(heading="", blocks=[
        TableBlock(rows=[["к1", "к2"]])]))
    html = _render("<div>{{a1}}</div>", atoms)
    assert "<table" in html and "к1" in html and "к2" in html


def test_mark_re_matches_plain_and_range():
    assert [m.group(1) for m in _MARK_RE.finditer("{{a1}} x {{a12:3-4}}")] == ["a1", "a12"]


from htmlslides.pipeline.exact_designer import _verify  # noqa: E402


def _two_atoms():
    return _atoms_from(Section(heading="Шапка", blocks=[TextBlock(text="тело")]))


def test_verify_ok_when_all_markers_present_and_clean():
    atoms = _two_atoms()
    raw = ('<section class="slide" data-template="freeform">'
           '<div class="content-head"><h3 class="content-head-title t-head-42">'
           '{{a1}}</h3></div><div class="row"><div class="col">{{a2}}</div></div></section>')
    assert _verify(raw, atoms) is None


def test_verify_fails_when_prose_typed_outside_markers():
    atoms = _two_atoms()
    raw = '<h3>{{a1}}</h3><p>лишний текст {{a2}}</p>'   # «лишний текст» — проза ИИ
    assert _verify(raw, atoms) is not None


def test_verify_fails_on_missing_atom():
    atoms = _two_atoms()
    raw = '<h3>{{a1}}</h3>'                              # a2 потерян
    assert _verify(raw, atoms) is not None


def test_verify_fails_on_duplicate_atom():
    atoms = _two_atoms()
    raw = '<h3>{{a1}}</h3><p>{{a2}}</p><p>{{a2}}</p>'    # a2 дважды
    assert _verify(raw, atoms) is not None


def test_verify_allows_word_range_marker_as_one_use():
    atoms = _two_atoms()
    raw = '<h3>{{a1}}</h3><p>{{a2:1-1}}</p>'             # a2 через диапазон — одно использование
    assert _verify(raw, atoms) is None


def test_verify_fails_on_forbidden_technique():
    atoms = _two_atoms()
    raw = '<h3>{{a1}}</h3><p style="x">{{a2}}</p>'       # style= запрещён
    assert _verify(raw, atoms) is not None


def test_verify_fails_on_unknown_class():
    atoms = _two_atoms()
    raw = '<h3>{{a1}}</h3><div class="totally-unknown">{{a2}}</div>'
    assert _verify(raw, atoms) is not None


def test_verify_ignores_html_entities_in_purity():
    """Сущности (&nbsp;) между метками не считаем прозой — иначе ложный провал."""
    atoms = _two_atoms()
    raw = '<h3>{{a1}}</h3><p>{{a2}}&nbsp;</p>'
    assert _verify(raw, atoms) is None


from htmlslides.models import DeckPlan, SlidePlan               # noqa: E402
from htmlslides.parsers.base import InputDoc                    # noqa: E402
from htmlslides.pipeline.exact_designer import (                # noqa: E402
    EXACT_DESIGN_SYSTEM, design_exact_deck)


class _FakeClient:
    """Мок клиента: возвращает заранее заданные ответы chat() по очереди."""
    def __init__(self, replies):
        self._replies = list(replies)
        self.calls = 0
        self.usage_total = {"prompt_tokens": 0, "completion_tokens": 0,
                            "cached_tokens": 0, "calls": 0}

    def chat(self, messages, *, max_tokens=4096, extra_body=None):
        self.calls += 1
        return self._replies.pop(0) if self._replies else self._replies_default()

    def _replies_default(self):
        raise AssertionError("chat вызван больше раз, чем задано ответов")


def _one_slide_doc_and_plan():
    doc = InputDoc(title="Дека", sections=[
        Section(heading="Шапка", blocks=[TextBlock(text="дословное тело")])])
    plan = DeckPlan(title="Дека", slides=[SlidePlan(
        index=1, type="exact", freeform=True,
        content={"html": '<div class="content-head">ФОЛБЭК</div>'
                          '<div class="exact-text">дословное тело</div>',
                 "exact": True})])
    return doc, plan


def test_design_system_prompt_is_plain_not_format():
    """Промпт держит литералы {{a1}} и плейсхолдер __CLASSES__ (иначе .format их съест)."""
    assert "{{a1}}" in EXACT_DESIGN_SYSTEM
    assert "__CLASSES__" in EXACT_DESIGN_SYSTEM


def test_design_success_produces_branded_verbatim_html():
    doc, plan = _one_slide_doc_and_plan()
    good = ('```html\n<section class="slide" data-template="freeform">'
            '<div class="content-head"><h3 class="content-head-title t-head-42">'
            '{{a1}}</h3></div><div class="row"><div class="col">{{a2}}</div>'
            '</div></section>\n```')
    client = _FakeClient([good])
    out = design_exact_deck(client, doc, plan, workers=1)
    html = out.slides[0].content["html"]
    assert 'class="row"' in html and 'class="col"' in html   # брендовая вёрстка
    assert "Шапка" in html and "дословное тело" in html      # дословный текст
    assert out.slides[0].content["exact"] is True
    assert client.calls == 1


def test_design_falls_back_when_model_types_prose():
    """ИИ «врёт» (печатает свой текст) оба раза → остаётся фолбэк-html, текст цел."""
    doc, plan = _one_slide_doc_and_plan()
    liar = ('```html<section data-template="freeform"><p>отсебятина {{a1}} {{a2}}</p>'
            '</section>```')
    client = _FakeClient([liar, liar])                       # ответ + ретрай
    out = design_exact_deck(client, doc, plan, workers=1)
    html = out.slides[0].content["html"]
    assert "ФОЛБЭК" in html                                  # исходный html Этапа 1
    assert "дословное тело" in html                          # дословность сохранена
    assert client.calls == 2                                 # была попытка + ретрай


def test_design_falls_back_when_atom_missing():
    doc, plan = _one_slide_doc_and_plan()
    missing = '```html<section data-template="freeform"><h3>{{a1}}</h3></section>```'
    client = _FakeClient([missing, missing])
    out = design_exact_deck(client, doc, plan, workers=1)
    assert "ФОЛБЭК" in out.slides[0].content["html"]


def test_design_transient_api_error_keeps_fallback():
    import httpx
    from openai import APIConnectionError

    doc, plan = _one_slide_doc_and_plan()

    class _Boom:
        usage_total = {}
        def chat(self, messages, *, max_tokens=4096, extra_body=None):
            raise APIConnectionError(request=httpx.Request("POST", "http://x"))

    out = design_exact_deck(_Boom(), doc, plan, workers=1)
    assert "ФОЛБЭК" in out.slides[0].content["html"]         # дека не упала


def test_design_retry_succeeds_second_try():
    doc, plan = _one_slide_doc_and_plan()
    bad = '```html<section><p>проза {{a1}} {{a2}}</p></section>```'
    good = ('```html<section data-template="freeform">'
            '<div class="content-head"><h3 class="content-head-title t-head-42">'
            '{{a1}}</h3></div><p class="t-body-30">{{a2}}</p></section>```')
    client = _FakeClient([bad, good])
    out = design_exact_deck(client, doc, plan, workers=1)
    assert "дословное тело" in out.slides[0].content["html"]
    assert 't-body-30' in out.slides[0].content["html"]
    assert client.calls == 2


def test_verify_fails_on_ai_words_in_attribute():
    """ИИ не может протащить свои слова через значение атрибута (title/alt/data-*):
    старая проверка срезала теги целиком и такие слова не ловила."""
    atoms = _two_atoms()
    raw = ('<section class="slide" data-template="freeform">'
           '<h3 class="content-head-title t-head-42">{{a1}}</h3>'
           '<p class="t-body-30" title="это придумал ИИ">{{a2}}</p></section>')
    assert _verify(raw, atoms) is not None


def test_verify_fails_on_ai_words_in_comment():
    """Слова ИИ в HTML-комментарии тоже под запретом (иначе утекут в деку)."""
    atoms = _two_atoms()
    raw = ('<section class="slide" data-template="freeform"><h3>{{a1}}</h3>'
           '<!-- отсебятина ИИ --><p>{{a2}}</p></section>')
    assert _verify(raw, atoms) is not None


def test_design_non_transient_error_keeps_fallback():
    """Не-транзиентная ошибка (400/ValueError/формат) не роняет всю деку —
    проблемный слайд деградирует до простого вида (как Этап 1)."""
    doc, plan = _one_slide_doc_and_plan()

    class _Boom:
        usage_total = {}

        def chat(self, messages, *, max_tokens=4096, extra_body=None):
            raise ValueError("boom-400")

    out = design_exact_deck(_Boom(), doc, plan, workers=1)
    assert "ФОЛБЭК" in out.slides[0].content["html"]         # дека собралась
    assert "дословное тело" in out.slides[0].content["html"]  # текст цел
