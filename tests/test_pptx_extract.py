"""Render-free обогащение parse_pptx: текст из групп, числа из диаграмм, заметки.

python-pptx читает pptx прямо из XML — рендер (LibreOffice) не нужен. Раньше
parse_pptx терял три вещи: (1) текст внутри сгруппированных фигур, (2) данные
диаграмм, (3) заметки докладчика. Тесты строят НАСТОЯЩИЙ pptx (python-pptx умеет
и создавать группы/графики) и проверяют, что контент доезжает до InputDoc.

Ключевой инвариант: заметки идут в Section.notes, а НЕ в blocks — иначе они
утекут прямо на слайд в режиме «Точный перенос» (exact рендерит heading+blocks).
"""
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
from pptx.util import Inches

from htmlslides.parsers.pptx import parse_pptx


def _fixture(tmp_path: Path) -> Path:
    """3 слайда: [1] группа с текстом, [2] диаграмма, [3] видимый текст + заметка."""
    prs = Presentation()
    blank = prs.slide_layouts[6]

    s1 = prs.slides.add_slide(blank)
    grp = s1.shapes.add_group_shape()
    tb = grp.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    tb.text_frame.text = "Текст-внутри-группы"

    s2 = prs.slides.add_slide(blank)
    data = CategoryChartData()
    data.categories = ["2023", "2024", "2025"]
    data.add_series("Выручка", (10, 20, 35))
    s2.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED,
                        Inches(1), Inches(1), Inches(6), Inches(4), data)

    s3 = prs.slides.add_slide(blank)
    box = s3.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    box.text_frame.text = "Видимый текст слайда"
    s3.notes_slide.notes_text_frame.text = "СЕКРЕТНАЯ-ЗАМЕТКА"

    out = tmp_path / "fixture.pptx"
    prs.save(str(out))
    return out


def _all_text(section) -> str:
    """Весь текст из блоков секции (для проверки «контент дошёл»)."""
    parts: list[str] = []
    for b in section.blocks:
        parts.append(getattr(b, "text", "") or "")
        parts.extend(getattr(b, "items", []) or [])
        for row in getattr(b, "rows", []) or []:
            parts.extend(row)
    return " ".join(parts)


def test_group_text_is_captured(tmp_path):
    """Текст внутри сгруппированных фигур раньше терялся — теперь собирается."""
    doc = parse_pptx(_fixture(tmp_path))
    assert "Текст-внутри-группы" in _all_text(doc.sections[0])


def test_chart_numbers_captured_as_table(tmp_path):
    """Диаграмма отдаётся таблицей «категория × ряд»: цифры доходят до планировщика."""
    text = _all_text(parse_pptx(_fixture(tmp_path)).sections[1])
    assert "Выручка" in text                    # имя ряда
    assert "2025" in text and "35" in text       # категория и её значение


def test_notes_go_to_section_notes_not_blocks(tmp_path):
    """Заметка — в Section.notes и НЕ в контенте (иначе утечёт в exact)."""
    s3 = parse_pptx(_fixture(tmp_path)).sections[2]
    assert s3.notes == "СЕКРЕТНАЯ-ЗАМЕТКА"
    assert "СЕКРЕТНАЯ-ЗАМЕТКА" not in _all_text(s3)


def test_exact_mode_does_not_leak_notes(tmp_path):
    """Контракт exact: рендерятся heading+blocks, заметки докладчика — никогда."""
    from htmlslides.pipeline.exact_builder import build_exact_plan
    doc = parse_pptx(_fixture(tmp_path))
    plan, _ = build_exact_plan(doc)
    html = " ".join(s.content.get("html", "") for s in plan.slides)
    assert "Видимый текст слайда" in html        # контент на месте
    assert "СЕКРЕТНАЯ-ЗАМЕТКА" not in html        # заметка не утекла


def test_notes_reach_planner_section_text(tmp_path):
    """Заметки доезжают в текст для планировщика — как контекст для дизайна."""
    from htmlslides.pipeline.planner import _section_to_text
    txt = _section_to_text(parse_pptx(_fixture(tmp_path)).sections[2])
    assert "СЕКРЕТНАЯ-ЗАМЕТКА" in txt
