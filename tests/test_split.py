"""Раздел больше слайда режется на части размером в слайд — без модели.

Замер 2026-09-11 на 88 деках (docs/superpowers/specs/2026-09-11-doc-preparation-
research.md): документ без разметки одним разделом доносит до слайдов 10% мыслей
автора и 39% чисел, нарезкой по ~700 символов — 69% и 86%, вровень с разметкой
моделью. Тесты держат условия, от которых этот результат зависит: текст дословно и
по порядку, части размером в слайд, заголовок части — начало фразы, код неделим,
размеченные разделы размером в слайд не трогаются, pptx не режется.
"""
from htmlslides.parsers.base import (CodeBlock, InputDoc, ListBlock, Section,
                                     TextBlock, parse_file)
from htmlslides.parsers.split import CHUNK_CHARS, split_oversized
from htmlslides.pipeline.planner import _block_to_text, _section_to_text


def _body(sections):
    """Текст всех блоков подряд, без заголовков: так видно, что ничего не пропало."""
    return " ".join(" ".join(_block_to_text(b).split())
                    for s in sections for b in s.blocks)


def _lines(n):
    return [f"Пункт {i}: рост выручки на {i}% за квартал и {i * 3} новых клиентов в отрасли."
            for i in range(1, n + 1)]


def test_unmarked_txt_is_cut_into_slide_sized_parts(tmp_path):
    """Реальная txt-загрузка с прода: 254 строки без единой пустой — раньше это был
    один раздел на 22 тыс. символов, то есть титул и один слайд."""
    lines = _lines(60)
    src = tmp_path / "doc.txt"
    src.write_text("\n".join(lines), encoding="utf-8")

    doc = parse_file(src)

    assert len(doc.sections) >= 6
    for s in doc.sections:
        assert len(_section_to_text(s)) <= CHUNK_CHARS + len(s.heading) + 2
    assert _body(doc.sections) == " ".join(lines)      # дословно и по порядку


def test_part_heading_is_the_start_of_its_first_phrase():
    """Заголовок части — первая строка брифа, из неё филлер берёт название слайда:
    «Часть 3» стала бы названием слайда."""
    text = "\n".join(["Цели на 2026: рост выручки вдвое и выход в регионы."] + [
        f"SLA 99.95% по зоне {i}, резервирование и мониторинг круглосуточно."
        for i in range(40)])

    doc = split_oversized(InputDoc(sections=[Section(blocks=[TextBlock(text=text)])]))

    assert doc.sections[0].heading == "Цели на 2026"
    assert doc.sections[1].heading.startswith("SLA 99.95% по зоне")   # точка в числе
    assert not any(s.heading.startswith("Часть") for s in doc.sections)


def test_marked_sections_that_fit_stay_as_the_author_made_them(tmp_path):
    """Размеченный раздел размером в слайд — решение автора, его не трогаем."""
    src = tmp_path / "doc.md"
    src.write_text("# Отчёт\n\n" + "".join(
        f"## Раздел {i}\n\n{'Текст про метрики. ' * 25}\n\n" for i in range(3)),
        encoding="utf-8")

    doc = parse_file(src)

    assert [s.heading for s in doc.sections] == ["Раздел 0", "Раздел 1", "Раздел 2"]


def test_only_the_oversized_section_is_cut_and_keeps_its_heading(tmp_path):
    long = " ".join(f"Мысль {i} о выручке и клиентах." for i in range(120))
    src = tmp_path / "doc.md"
    src.write_text(f"# Отчёт\n\n## Короткий\n\nОдин абзац.\n\n## Длинный\n\n{long}\n\n"
                   f"## Хвост\n\nЕщё абзац.\n", encoding="utf-8")

    doc = parse_file(src)

    heads = [s.heading for s in doc.sections]
    assert heads[0] == "Короткий" and heads[-1] == "Хвост"
    assert heads[1] == "Длинный" and len(heads) >= 5
    assert _body(doc.sections[1:-1]) == " ".join(long.split())


def test_code_block_is_never_cut():
    """Разрезанный блок кода — это открытый ``` в одной части и закрытый в другой."""
    code = "\n".join(f"print({i})" for i in range(400))
    section = Section(heading="Скрипт", level=2, blocks=[
        TextBlock(text="Вот скрипт выгрузки. " * 30),
        CodeBlock(text=code, lang="py"),
        TextBlock(text="После запуска проверьте журнал. " * 30)])
    tail = Section(heading="Итог", level=2, blocks=[TextBlock(text="Готово.")])

    doc = split_oversized(InputDoc(sections=[section, tail]))

    codes = [b for s in doc.sections for b in s.blocks if isinstance(b, CodeBlock)]
    assert codes == [CodeBlock(text=code, lang="py")]
    assert len(doc.sections) > 3


def test_cut_numbered_list_keeps_counting():
    """«Шаг 5» на следующем слайде не должен стать «1.»."""
    steps = [f"Проверить настройку номер {i} и записать результат в журнал смены"
             for i in range(1, 31)]
    doc = split_oversized(InputDoc(sections=[
        Section(heading="Порядок", level=2, blocks=[ListBlock(items=steps, ordered=True)]),
        Section(heading="Итог", level=2, blocks=[TextBlock(text="Готово.")])]))

    parts = [s for s in doc.sections if s.heading != "Итог"]
    rendered = "\n".join(_block_to_text(b) for s in parts for b in s.blocks)

    assert len(parts) >= 3
    assert [int(line.split(".")[0]) for line in rendered.splitlines()] == list(range(1, 31))


def test_small_unmarked_document_still_gets_more_than_one_slide():
    """Документ в тысячу символов без разметки иначе остаётся одним слайдом."""
    text = "\n".join(f"Мысль {i}: коротко о продукте и его пользе для клиента."
                     for i in range(18))

    doc = split_oversized(InputDoc(sections=[Section(blocks=[TextBlock(text=text)])]))

    assert len(doc.sections) == 2
    tiny = InputDoc(sections=[Section(blocks=[TextBlock(text="Одна мысль про продукт.")])])
    assert split_oversized(tiny).sections == tiny.sections


def test_txt_keeps_its_lines_md_keeps_markdown_semantics(tmp_path):
    """В .txt перевод строки — новая строка: заголовок или пункт без маркера. В .md
    это перенос внутри абзаца, как велит markdown."""
    (tmp_path / "a.txt").write_text("Цели\nРост выручки", encoding="utf-8")
    (tmp_path / "a.md").write_text("Цели\nРост выручки", encoding="utf-8")

    assert parse_file(tmp_path / "a.txt").sections[0].blocks[0].text == "Цели\nРост выручки"
    assert parse_file(tmp_path / "a.md").sections[0].blocks[0].text == "Цели Рост выручки"


def test_pptx_is_never_cut(monkeypatch, tmp_path):
    """В pptx раздел — это слайд автора: резать его значило бы менять его деку."""
    big = InputDoc(sections=[Section(heading="Слайд", level=1,
                                     blocks=[TextBlock(text="Текст. " * 400)])])
    monkeypatch.setattr("htmlslides.parsers.pptx.parse_pptx", lambda path: big)

    assert parse_file(tmp_path / "deck.pptx") is big
