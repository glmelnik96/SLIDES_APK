"""Регрессия на два model-independent дефекта рендера, найденные в Phase 2:

  Fix 1 — cards-6 с 5 плитками: раньше при repeat(3) 6-я ячейка оставалась пустой
          «дырой». Правка чисто CSS и включается атрибутом data-n="5" на .c6-grid.
          Тест страхует контракт: 5 карточек → в HTML есть data-n="5" (иначе CSS-
          правило "5 плиток без дыры" не сработает).

  Fix 2 — stats-row: длинные значения-диапазоны («−30–45%», «15→65%») при 104px
          вылезали из ячейки и наезжали на соседнюю. Правка — JS-функция
          autofitStats() в deck.js, которая ужимает ВЕСЬ ряд под самую тесную ячейку.
          assemble() встраивает deck.js с диска, поэтому тест проверяет, что функция
          доехала до собранной деки, вызвана в init() и что шаблон отдаёт ту самую
          структуру (.sr-row/.sr-cell/.sr-value), по которой функция и меряет.
"""
from htmlslides.assembler import assemble
from htmlslides.models import DeckPlan, SlidePlan


def _cards6(n: int) -> str:
    cards = [{"heading": f"H{i}", "text": f"t{i}"} for i in range(n)]
    plan = DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="content", template_id="cards-6",
                  content={"title": "Заголовок", "cards": cards})])
    return assemble(plan, theme="dark")


# --- Fix 1: cards-6 n=5 -------------------------------------------------------

def test_cards6_five_emits_data_n_5():
    """5 плиток → data-n=\"5\": триггер CSS-правила «без дыры в 6-й ячейке»."""
    html = _cards6(5)
    assert 'data-n="5"' in html
    assert html.count('class="c6-cell"') == 5


def test_cards6_data_n_matches_card_count():
    """data-n всегда равен числу карточек (2..6) — CSS раскладки на это опираются."""
    for n in (2, 3, 4, 5, 6):
        html = _cards6(n)
        assert f'data-n="{n}"' in html
        assert html.count('class="c6-cell"') == n


# --- Fix 2: stats-row autofit -------------------------------------------------

def _stats_row() -> str:
    plan = DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="content", template_id="stats-row",
                  content={"title": "TCO", "stats": [
                      {"value": "−30–45%", "label": "TCO", "caption": "c"},
                      {"value": "15→65%", "label": "Утилизация"},
                      {"value": "−40%", "label": "Штат"},
                      {"value": "3–6 мес.", "label": "Срок"},
                  ]})])
    return assemble(plan, theme="dark")


def test_stats_row_autofit_shipped_and_wired():
    """Функция ужима встроена в деку и вызвана в init() (иначе цифры столкнутся)."""
    html = _stats_row()
    assert "function autofitStats" in html          # код доехал в собранную деку
    assert "autofitStats();" in html                # и вызывается (init + load)


def test_stats_row_structure_matches_autofit_selectors():
    """Шаблон отдаёт структуру, по которой меряет autofitStats (.sr-row/-cell/-value)."""
    html = _stats_row()
    assert 'class="sr-row m-stagger"' in html
    assert html.count('class="sr-cell"') == 4
    assert html.count('class="sr-value js-count"') == 4
    # длинные значения сохранены дословно (autofit масштабирует, не режет текст)
    assert "−30–45%" in html and "15→65%" in html


# --- Слайд 7: два независимых ряда (stats + необязательный stats2) -------------

def _stats_row_two() -> str:
    """Ряд 1 (stats) = 3 метрики, ряд 2 (stats2) = 1 метрика с длинной подписью."""
    plan = DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="content", template_id="stats-row",
                  content={"title": "Итоги", "stats": [
                      {"value": "47%", "label": "Рост"},
                      {"value": "1200", "label": "Клиентов"},
                      {"value": "3,5×", "label": "ROI"},
                  ], "stats2": [
                      {"value": "98%", "label": "Удержание",
                       "caption": "после перехода на единую платформу"},
                  ]})])
    return assemble(plan, theme="dark")


def test_stats_row_two_rows_render_independently():
    """stats → ряд 1, stats2 → ряд 2: два .sr-row, свои --sr-cols, всего 4 ячейки."""
    html = _stats_row_two()
    assert html.count('class="sr-row m-stagger"') == 2   # два независимых ряда
    assert html.count('class="sr-cell"') == 4            # 3 + 1
    assert "--sr-cols:3" in html                         # ряд 1 = 3 колонки
    assert "--sr-cols:1" in html                         # ряд 2 = 1 колонка (широкая)


def test_stats_row_empty_second_row_not_rendered():
    """Без stats2 второй ряд не создаётся — только один .sr-row (пустой ряд не мозолит)."""
    html = _stats_row()
    assert html.count('class="sr-row m-stagger"') == 1
    assert "--sr-cols:4" in html                         # 4 метрики → 4 колонки


# --- Fix 3: число не отрывается от единицы (неразрывный пробел) ----------------

_NBSP = " "


def test_units_glued_in_rendered_slide():
    """«18 мес.»/«12 ВМ» в подписях склеиваются NBSP — судья ловил их разрыв."""
    plan = DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="content", template_id="stats-row",
                  content={"title": "Срок", "subtitle": "Миграция за 18 мес.",
                           "stats": [{"value": "12 ВМ", "label": "перенесено"}]})])
    html = assemble(plan, theme="dark")
    assert f"18{_NBSP}мес." in html
    assert f"12{_NBSP}ВМ" in html
    # обычный (разрывный) пробел между числом и единицей не остался
    assert "18 мес." not in html
    assert "12 ВМ" not in html


def test_units_not_glued_in_exact_verbatim():
    """Точный перенос дословен: NBSP не вставляем (freeform идёт мимо Jinja)."""
    html = ('<div class="content-head"><h2 class="content-head-title t-head-42">'
            'Шапка</h2></div><div class="exact-text">'
            '<p class="t-body-30">Срок 18 мес. и 12 ВМ</p></div>')
    plan = DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="exact", freeform=True,
                  content={"html": html, "exact": True})])
    out = assemble(plan, theme="dark")
    assert "18 мес." in out          # ровно как в источнике, обычный пробел
    assert f"18{_NBSP}мес." not in out
