import pytest
import webapp.chat_edit as chat_edit

_DECK = (
    '<div class="deck-stage">'
    '<section class="slide">ONE</section>'
    '<section class="slide slide--chrome-sm">TWO</section>'
    '<section class="slide">THREE</section>'
    '</div>'
)


def test_count_slides():
    assert chat_edit.count_slides(_DECK) == 3


def test_replace_nth_section():
    out = chat_edit._replace_nth_section(_DECK, 2, '<section class="slide">NEW</section>')
    assert "NEW" in out
    assert "ONE" in out and "THREE" in out
    assert "TWO" not in out


def test_replace_out_of_range():
    with pytest.raises(ValueError):
        chat_edit._replace_nth_section(_DECK, 9, "<section></section>")


class _FakeClient:
    def __init__(self, reply):
        self._reply = reply
        self.calls = []

    def chat(self, messages, max_tokens=4096, temperature=0.3):
        self.calls.append(messages)
        return self._reply


def test_rewrite_slide_swaps_section():
    fake = _FakeClient('ok: <section class="slide">EDITED</section> done')
    out = chat_edit.rewrite_slide(_DECK, 1, "сделай заголовок короче", client=fake)
    assert "EDITED" in out
    assert "ONE" not in out
    assert "TWO" in out and "THREE" in out
    assert len(fake.calls) == 1


def test_rewrite_slide_retries_on_forbidden():
    replies = iter([
        '<section class="slide" style="color:red">BAD</section>',
        '<section class="slide">CLEAN</section>',
    ])

    class _Seq:
        def __init__(self):
            self.n = 0

        def chat(self, messages, max_tokens=4096, temperature=0.3):
            self.n += 1
            return next(replies)

    seq = _Seq()
    out = chat_edit.rewrite_slide(_DECK, 1, "x", client=seq)
    assert "CLEAN" in out
    assert "BAD" not in out
    assert seq.n == 2


def test_rewrite_out_of_range():
    fake = _FakeClient("<section></section>")
    with pytest.raises(ValueError):
        chat_edit.rewrite_slide(_DECK, 5, "x", client=fake)
