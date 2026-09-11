"""Сохранность фактуры: числа исходника, не доехавшие до слайда.

Замер на шести реальных деках (`data/e2e/`) показал медиану 0.38 по доле
дошедших чисел, при двух деках с нулём. Дека при этом безупречна на вид:
слайд заполнен, вёрстка чистая, линтер и vision-QA молчат — потерялась только
фактура. Промпт филлера её беречь ТРЕБУЕТ, а проверял это до сих пор никто.

Каждое правило шумоподавления здесь снимало ложные срабатывания на реальных
данных, поэтому у каждого — свой тест: без них проверка ругалась на 44%
слайдов вместо 16% и была бы выключена в первый же день.
"""
from htmlslides.models import DeckPlan, SlidePlan
from htmlslides.pipeline.facts import check_facts, extract_facts, slide_texts


# ── что считается фактом ─────────────────────────────────────────────────────
def test_numbers_units_and_standards_are_facts():
    facts = extract_facts(
        "SLA 99,95%, 1 200 ВМ, ISO/IEC 27001:2022, PCI DSS 3.2.1, 152-ФЗ, ГОСТ 57580")
    assert facts == {"99,95%", "1200", "27001", "2022", "3.2.1", "152", "57580"}


def test_thousands_separator_is_normalized():
    """«1 200» и «1200» — одно число: иначе слайд с аккуратным пробелом
    считался бы потерявшим факт."""
    assert extract_facts("выручка 1 200") == extract_facts("выручка 1200") == {"1200"}


def test_slide_number_header_is_not_a_fact():
    """Бриф из pptx начинается служебным «Слайд N». На замере этот номер давал
    44% ложных потерь — он и был главным «фактом» почти каждого слайда."""
    assert extract_facts("Слайд 12  Работа с возражениями") == set()
    assert extract_facts("Slide 7  Roadmap") == set()


def test_section_numbering_is_not_a_fact():
    """01/02/03 — нумерация разделов, а не фактура."""
    assert extract_facts("Портрет клиента  01  02  03") == set()


def test_single_digit_is_not_a_fact_but_a_percent_is():
    assert extract_facts("8 необсуждаемых требований") == set()
    assert extract_facts("рост 8%") == {"8%"}


def test_numbers_inside_urls_and_sources_are_ignored():
    """Ссылки в блоке [Sources] несут числа («/10-steps/»), фактурой не являясь."""
    assert extract_facts(
        "[Sources] - https://www.ncsc.gov.uk/collection/10-steps/data") == set()


# ── сверка со слайдом ────────────────────────────────────────────────────────
def _plan(brief):
    return DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="content", template_id="kpi",
                  content={}, brief=brief)])


def _deck(body):
    return f'<section class="slide" data-template="kpi">{body}</section>'


def test_lost_facts_are_reported_with_their_values():
    """Замечание обязано нести САМИ значения: autofix_slide работает без
    исходника (бриф из content стёрт заполнением), и «ты потерял числа» ему
    нечем исполнить."""
    issues = check_facts(_plan("Соответствие 152-ФЗ и ГОСТ 57580"),
                         _deck("<h2>Безопасность</h2><p>Сертификаты есть</p>"))
    assert len(issues) == 1
    assert issues[0].slide_index == 1 and issues[0].code == "facts_lost"
    assert "152" in issues[0].detail and "57580" in issues[0].detail


def test_fact_counts_as_reached_in_any_notation():
    """«152» засчитывается и в «152-ФЗ»: требовать точного совпадения строки
    значило бы ругаться на слайд, оформивший число лучше исходника."""
    assert check_facts(_plan("Соответствие 152"),
                       _deck("<p>Соответствие 152-ФЗ</p>")) == []


def test_decimal_and_dotted_numbers_count_as_reached():
    """Регрессия 2026-09-11: ядро «9995» искалось в «99,95%» подстрокой и не
    находилось из-за запятой. Все числа с десятичным разделителем и версии вида
    3.2.1 считались потерянными, даже стоя на слайде дословно, — и ложная
    тревога отправляла слайд в автофикс."""
    for brief, slide in [
        ("SLA 99,95%", "Доступность 99,95% по SLA"),
        ("PCI DSS 3.2.1", "Сертификат PCI DSS 3.2.1"),
        ("доступность 99.982%", "SLA 99.982%"),
        ("срок 1,5 года", "Внедрение за 1,5 года"),
    ]:
        assert check_facts(_plan(brief), _deck(f"<p>{slide}</p>")) == [], brief


def test_decimal_counts_in_another_notation():
    """99,95% и 99.95 % — одно число: сравниваем ядра, а не написание."""
    assert check_facts(_plan("SLA 99,95%"), _deck("<p>SLA 99.95 %</p>")) == []


def test_short_number_is_not_found_inside_a_year():
    """«15» не засчитывается внутри «2015»: иначе потеря пряталась бы за годом.
    Первая версия искала подстроку и на этом слайде не видела ни одной потери."""
    issues = check_facts(_plan("RPO 15 минут, RTO 30 минут, хранение 45 дней"),
                         _deck("<p>Работаем с 2015 года, план до 2030</p>"))
    assert len(issues) == 1


def test_long_core_counts_inside_a_longer_number():
    """«57580» засчитывается в «57580.1» — тот же ГОСТ с номером части."""
    assert check_facts(_plan("ГОСТ 57580 и ISO 27001"),
                       _deck("<p>ГОСТ Р 57580.1 и ISO 27001</p>")) == []


def test_quarter_label_is_not_glued_to_the_amount():
    """«Q3 100 млн» читалось как «3 100»: цифра квартала склеивалась с суммой по
    правилу разделителя тысяч. Хуже ложной тревоги — автофиксу уходило «верни 3100»,
    то есть просьба вставить на слайд число, которого в документе нет."""
    assert extract_facts("Сделка Q3 100 млн, прогноз Q4 250 млн") == {"100", "250"}
    assert check_facts(_plan("Сделка Q3 100 млн, прогноз Q4 250 млн, итог Q2 300 млн"),
                       _deck("<p>Q3: 100 млн. Q4: 250 млн. Q2: 300 млн.</p>")) == []


def test_year_next_to_value_is_not_glued():
    """На слайде «2024 1 628 2025 1 886» группа тысяч забирала начало года:
    «1 628 202» — и год 2025 пропадал из найденных чисел."""
    from htmlslides.pipeline.facts import _digit_cores
    assert _digit_cores("2024 1 628 2025 1 886") >= {"2024", "1628", "2025", "1886"}


def test_version_after_letter_is_still_a_fact():
    """Запрет «после буквы» касается только группы тысяч. Первая версия правки
    запрещала число после буквы вообще — и «v1.12.18» переставало быть фактом:
    версии в release notes молча выпадали из проверки (замер 2026-09-11)."""
    assert extract_facts("Terraform: v1.12.16 → v1.12.18") == {"1.12.16", "1.12.18"}
    assert check_facts(_plan("Terraform v1.12.18"), _deck("<p>Terraform 1.12.17</p>")) != []


def test_real_thousands_still_group():
    """Обратная сторона: настоящие разделители тысяч по-прежнему склеиваются."""
    assert extract_facts("выручка 1 628 202 руб.") == {"1628202"}
    assert extract_facts("бюджет 1 200,5 млн") == {"1200,5"}


def test_html_entities_are_not_read_as_numbers():
    """«Data Scientist&#39;ы» давало число 39: раскрывался только &nbsp;, а сущность
    апострофа оставалась цифрами. Лишнее ядро на слайде маскирует потерю числа."""
    from htmlslides.pipeline.facts import _digit_cores
    text = slide_texts('<section class="slide"><p>Data Scientist&#39;ы уровня Kaggle</p></section>')[0]
    assert "39" not in _digit_cores(text)
    # неразрывный пробел по-прежнему разделитель тысяч
    nbsp = slide_texts('<section class="slide"><p>выручка 1&nbsp;200</p></section>')[0]
    assert "1200" in _digit_cores(nbsp)


def test_numbers_in_adjacent_elements_are_not_glued():
    """Граница тега — не пробел. «медиаиндекс 170» в подписи одной ячейки и «200
    тыс.» в значении соседней склеивались по правилу тысяч в «170 200»: на слайде не
    находилось ни 170, ни 200, и проверка поднимала ложную тревогу (замер
    2026-09-11, макет stats-row)."""
    from htmlslides.pipeline.facts import _digit_cores
    deck = _deck('<div class="sr-caption">Публикаций 18, медиаиндекс 170</div>'
                 '<div class="sr-value">200 тыс.</div>')
    assert {"170", "200"} <= _digit_cores(slide_texts(deck)[0])
    assert check_facts(_plan("медиаиндекс 170, охват 200 тыс."), deck) == []
    # перевод строки внутри текста — просто пробел вёрстки: «1 200» остаётся числом
    formatted = slide_texts('<section class="slide"><p>выручка 1\n    200</p></section>')[0]
    assert "1200" in _digit_cores(formatted)


def test_partial_loss_is_not_reported():
    """Пересказ обязан сокращать: слайд, донёсший часть чисел, работу сделал."""
    assert check_facts(_plan("Выручка 1200, рост 15%, срок 24 месяца"),
                       _deck("<p>Выручка 1200</p>")) == []


def test_many_lost_is_reported_even_when_one_survived():
    """Но потеря трёх и более — уже не сокращение."""
    issues = check_facts(
        _plan("152-ФЗ, ГОСТ 57580, ISO 27001, приказ 719, выручка 1200"),
        _deck("<p>Выручка 1200</p>"))
    assert len(issues) == 1
    assert "1200" not in issues[0].detail          # дошедшее не поминаем


def test_slide_without_brief_is_skipped():
    """Деки из черновика брифа не несут — сверять не с чем, и это не дефект."""
    assert check_facts(_plan(""), _deck("<p>что угодно</p>")) == []


def test_svg_text_counts_as_slide_content():
    """Слайды-диаграммы рисуются целиком в <svg>. Выбрасывать его нельзя: на
    замере такие слайды давали ложные потери — числа были на слайде, внутри svg."""
    deck = _deck('<svg viewBox="0 0 10 10"><text>RPO 15 мин</text>'
                 '<text>RTO 30 мин</text></svg>')
    assert check_facts(_plan("RPO от 15 минут, RTO от 30 минут"), deck) == []


def test_slide_texts_splits_by_section():
    texts = slide_texts('<section class="slide"><p>первый 10</p></section>'
                        '<section class="slide"><p>второй 20</p></section>')
    assert len(texts) == 2
    assert "первый" in texts[0] and "второй" in texts[1]


# ── проводка ─────────────────────────────────────────────────────────────────
def test_qa_notes_carries_fact_losses_into_the_autofix_round(monkeypatch):
    """Проверка обязана быть ВЫЗВАНА из фазы QA, иначе она — стенд, который
    никто не гоняет: файл на месте, тесты зелёные, в сборке не участвует."""
    from htmlslides.pipeline import build

    monkeypatch.setattr(build, "lint_html", lambda html: [])
    plan = DeckPlan(title="T", slides=[
        SlidePlan(index=1, type="content", template_id="kpi", content={},
                  brief="Соответствие 152-ФЗ и ГОСТ 57580")])
    html = _deck("<h2>Безопасность</h2><p>Сертификаты есть</p>")

    notes = build._qa_notes(plan, html, vision=False, measure=False,
                            client=object(), artifacts=None,
                            progress=lambda m: None)

    assert list(notes) == [1]
    assert "facts_lost" in notes[1][0]
    assert "152" in notes[1][0] and "57580" in notes[1][0]
