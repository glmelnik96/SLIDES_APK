from htmlslides.parsers.base import Section, TextBlock
from htmlslides.parsers.exact_text import split_exact_text


def test_split_two_slides_verbatim():
    raw = (
        "Слайд 1: Введение\n"
        "Первый абзац.\n"
        "\n"
        "Второй абзац.\n"
        "\n"
        "Слайд 2:\n"
        "Заголовок два\n"
        "Тело два.\n"
    )
    sections = split_exact_text(raw)
    assert len(sections) == 2
    assert isinstance(sections[0], Section)
    assert sections[0].heading == "Введение"
    assert [b.text for b in sections[0].blocks] == ["Первый абзац.", "Второй абзац."]
    assert sections[1].heading == "Заголовок два"
    assert sections[1].blocks[0].text == "Тело два."


def test_marker_lowercase_and_dot():
    raw = "слайд 1. Один\nтело\nслайд 2. Два\nтело2"
    sections = split_exact_text(raw)
    assert [s.heading for s in sections] == ["Один", "Два"]


def test_multiline_paragraph_kept_verbatim():
    raw = "Слайд 1: Т\nстрока A\nстрока B\n"
    sections = split_exact_text(raw)
    assert sections[0].blocks[0].text == "строка A\nстрока B"


def test_preamble_before_first_marker_ignored():
    raw = "Заголовок документа\nвступление\n\nСлайд 1: A\nтело a\n"
    sections = split_exact_text(raw)
    assert len(sections) == 1
    assert sections[0].heading == "A"
    assert all("Заголовок документа" not in b.text for b in sections[0].blocks)
    assert sections[0].blocks[0].text == "тело a"


def test_no_boundaries_single_slide_with_hint():
    """Нет разделителей → 1 слайд (не ошибка) + подсказка в progress."""
    msgs = []
    sections = split_exact_text("Просто текст без меток.\nЕщё строка.",
                                progress=msgs.append)
    assert len(sections) == 1
    assert sections[0].heading == "Просто текст без меток."
    assert sections[0].blocks[0].text == "Ещё строка."
    assert any("один слайд" in m.lower() or "границ" in m.lower() for m in msgs)


def test_split_by_horizontal_rule():
    raw = "A\nтело a\n---\nB\nтело b\n"
    sections = split_exact_text(raw)
    assert [s.heading for s in sections] == ["A", "B"]
    assert sections[0].blocks[0].text == "тело a"
    assert sections[1].blocks[0].text == "тело b"


def test_split_by_markdown_top_level_only():
    """Режем по верхнему уровню (#); подзаголовки (##) остаются внутри слайда."""
    raw = "# Один\nтело\n## под\nещё\n# Два\nтело2\n"
    sections = split_exact_text(raw)
    assert [s.heading for s in sections] == ["Один", "Два"]
    assert any("под" in b.text for b in sections[0].blocks)


def test_marker_wins_over_dashes():
    """Приоритет: если есть «Слайд N:», разделители --- внутри не режут слайд."""
    raw = "Слайд 1: A\nтекст\n---\nещё\nСлайд 2: B\nтекст2\n"
    sections = split_exact_text(raw)
    assert [s.heading for s in sections] == ["A", "B"]
