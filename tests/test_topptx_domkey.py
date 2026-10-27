"""Контракт нумерации DOM между калибровкой и конвертером (htmlslides.topptx).

Геометрию снимает браузер один раз, а приклеивает её к элементам lxml в
рантайме — то есть обе половины обязаны нумеровать дерево ОДИНАКОВО. Ошибка тут
не падает и не видна: координаты просто достаются соседнему элементу. Поэтому
правило проверяется тестом, а не доверием.

Браузера в тестах нет, и он не нужен: расходиться эти два обхода могут ровно в
трёх местах, и каждое проверяется напрямую — порядок детей, пропускаемые теги и
граница <svg>.
"""
import os
import re

os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from lxml import html as LH

from htmlslides.topptx import domkey


def _sec(inner: str, cls: str = "slide", template: str = "t"):
    doc = LH.fragment_fromstring(
        f'<section class="{cls}" data-template="{template}">{inner}</section>')
    return doc


# --------------------------------------------------------------- сам обход
def test_walk_numbers_children_depth_first_in_document_order():
    sec = _sec('<div><span></span><b></b></div><p></p>')
    assert [p for p, _ in domkey.walk_paths(sec)] == [
        "0", "0.0", "0.1", "1"]


def test_walk_ignores_comments_like_browser_children_does():
    """Комментарий не элемент: в el.children его нет, в lxml-итерации есть."""
    sec = _sec('<!-- рамка --><div></div><!-- --><p></p>')
    assert [p for p, _ in domkey.walk_paths(sec)] == ["0", "1"]
    assert [el.tag for _, el in domkey.walk_paths(sec)] == ["div", "p"]


def test_skipped_tag_shifts_both_walks_alike():
    """Пропускаемый тег НЕ сдвигает нумерацию — ни в lxml, ни в браузере.

    Питонная половина фильтрует детей и только потом нумерует
    (element_children), и JS-половина обязана делать то же. Если она будет
    нумеровать по el.children и пропускать на ходу, <style> первым ребёнком
    отдаст <div> индекс 1, а lxml — 0. Сравнивать с живым браузером тут нечем,
    поэтому проверяем требование к обеим: фильтр стоит ДО нумерации.
    """
    sec = _sec('<style>.x{}</style><div></div><script></script><p></p>')
    assert [p for p, _ in domkey.walk_paths(sec)] == ["0", "1"]
    assert [el.tag for _, el in domkey.walk_paths(sec)] == ["div", "p"]

    js = domkey.JS_COLLECT
    # Фильтр и нумерация в одном выражении: .filter(...) ДО .forEach((c, i)).
    assert re.search(r"const kids = \(el\) => Array\.from\(el\.children\)\s*"
                     r"\.filter\(\(c\) => !SKIP\.has\(c\.tagName\)\);", js)
    # И ни одного обхода детей в сыром виде — только через kids().
    assert "Array.from(el.children).forEach" not in js
    assert "Array.from(sec.children).forEach" not in js


def test_js_and_python_skip_the_same_tags():
    """Список пропускаемых тегов не продублирован с расхождением."""
    js = re.search(r"const SKIP = new Set\(\[([^\]]+)\]\)",
                   domkey.JS_COLLECT).group(1)
    in_js = {t.strip().strip('"').lower() for t in js.split(",")}
    assert in_js == set(domkey._SKIP_TAGS)


# ------------------------------------------------------------ граница <svg>
def test_svg_itself_is_browser_laid_out_but_its_content_is_authored():
    """Рамку <svg> ставит CSS, координаты внутри — автор в системе viewBox."""
    sec = _sec('<svg viewBox="0 0 10 10"><g><rect/></g></svg>')
    by_tag = {el.tag: el for _, el in domkey.walk_paths(sec)}
    assert domkey.inside_svg(by_tag["svg"]) is False
    assert domkey.is_authored(by_tag["svg"]) is True
    for tag in ("g", "rect"):
        assert domkey.inside_svg(by_tag[tag]) is True


def test_inside_svg_stops_at_the_section_boundary():
    """Подъём не уходит за свою секцию: data-template — граница."""
    sec = _sec('<div><p></p></div>')
    p = dict(domkey.walk_paths(sec))["0.0"]
    assert domkey.inside_svg(p) is False


# ------------------------------------------------------------ ключ варианта
def test_fingerprint_sees_class_on_the_section_itself():
    """.statement--wide прячет блок стрелок — ключ обязан это различать."""
    inner = '<div class="st-arrows"></div>'
    assert (domkey.structure_key(_sec(inner, cls="slide"))
            != domkey.structure_key(_sec(inner, cls="slide statement--wide")))


def test_fingerprint_ignores_text_but_sees_structure():
    """Текст в ключ не входит (его влияние непрерывно), разметка — входит."""
    assert (domkey.structure_key(_sec("<p>коротко</p>"))
            == domkey.structure_key(_sec("<p>очень длинный текст слайда</p>")))
    assert (domkey.structure_key(_sec("<p></p>"))
            != domkey.structure_key(_sec("<p></p><p></p>")))


def test_fingerprint_ignores_svg_content():
    """У bar-chart с 2 и с 6 барами браузерная раскладка одна — и ключ один."""
    one = '<svg viewBox="0 0 10 10"><rect/></svg>'
    many = '<svg viewBox="0 0 10 10"><rect/><rect/><rect/></svg>'
    assert domkey.structure_key(_sec(one)) == domkey.structure_key(_sec(many))


def test_fingerprint_sees_slot_names():
    assert (domkey.structure_key(_sec('<p data-slot="heading"></p>'))
            != domkey.structure_key(_sec('<p data-slot="body"></p>')))


def test_variant_key_carries_the_template_id_for_human_eyes():
    key = domkey.variant_key(_sec("<p></p>", template="statement"))
    tpl, _, digest = key.partition("|")
    assert tpl == "statement"
    assert re.fullmatch(r"[0-9a-f]{12}", digest)


# ---------------------------------------------------- отпечаток источников
def _digest_without(path: str, mutate) -> str:
    """Отпечаток при изменённом файле. Файл восстанавливается всегда."""
    import pathlib
    import shutil
    import tempfile

    p = pathlib.Path(path)
    with tempfile.TemporaryDirectory() as tmp:
        keep = pathlib.Path(tmp) / "keep"
        shutil.copy(p, keep)
        try:
            before = p.read_text(encoding="utf-8")
            after = mutate(before)
            assert after != before, f"мутация {path} не применилась"
            p.write_text(after, encoding="utf-8")
            domkey.sources_digest.cache_clear()
            return domkey.sources_digest()
        finally:
            shutil.copy(keep, p)
            domkey.sources_digest.cache_clear()


def test_the_sources_digest_is_stable_and_short():
    """Отпечаток не зависит от прогона: иначе таблица «отставала» бы всегда."""
    first = domkey.sources_digest()
    domkey.sources_digest.cache_clear()
    assert domkey.sources_digest() == first
    assert re.fullmatch(r"[0-9a-f]{12}", first)


def test_editing_a_template_changes_the_sources_digest():
    """Правка разметки меняет ключи деревьев — отпечаток обязан это заметить.

    Это и есть тот случай, который однажды стоил деке 40% слайдов: разметка
    ушла вперёд, ключи сменились, а таблица об этом не узнала.
    """
    base = domkey.sources_digest()
    got = _digest_without(
        "htmlslides/templates/slides/statement.html",
        lambda s: s.replace('class="slide', 'class="slide probe', 1))
    assert got != base


def test_editing_the_css_changes_the_sources_digest():
    """CSS меняет не ключи, а сами снятые рамки — таблица тоже устаревает."""
    base = domkey.sources_digest()
    for css in ("htmlslides/engine/deck.css", "htmlslides/engine/motion.css"):
        assert _digest_without(css, lambda s: s + "\n/* probe */\n") != base


def test_editing_the_key_rule_changes_the_sources_digest():
    """Правка САМОГО обхода тоже меняет ключи, хотя файлы шаблонов те же."""
    base = domkey.sources_digest()
    got = _digest_without(
        "htmlslides/topptx/domkey.py",
        lambda s: s.replace("def rec(el, prefix: list[int]):",
                            "def rec(el, prefix: list[int]):  # probe", 1))
    assert got != base


def test_the_digest_ignores_what_cannot_stale_the_table():
    """Ложная тревога стоит пересборки 9.5 MB — значит шуметь нельзя.

    deck.js в калибровке уже отработал (viewport 1920x1080 -> масштаб 1) и на
    снятые рамки не влияет. Комментарий в шапке domkey.py не влияет ни на что:
    в отпечаток идёт исходник функций правила, а не весь модуль.
    """
    base = domkey.sources_digest()
    assert _digest_without("htmlslides/engine/deck.js",
                           lambda s: s + "\n/* probe */\n") == base
    assert _digest_without(
        "htmlslides/topptx/domkey.py",
        lambda s: s.replace('"""Контракт между', '"""PROBE. Контракт между',
                            1)) == base


def test_the_shipped_table_matches_the_shipped_templates():
    """Таблица в репозитории соответствует шаблонам в репозитории.

    Единственная проверка, которая ловит самую дорогую рассинхронизацию
    проекта: geometry.json лежит рядом с кодом, но собирается отдельной
    командой, и разойтись они могут молча. Упадёт этот тест — не геометрия.
    """
    from htmlslides.topptx.convert import table_state

    fresh, verdict = table_state()
    assert fresh, verdict


# ------------------------------------- допущение «браузер ничего не доинсертит»
def test_catalogue_has_no_tables_so_tbody_cannot_appear():
    """Браузер сам добавляет <tbody> в <table> — тогда обходы разойдутся.

    Пока таблиц в каталоге нет, допущение верно. Появятся — правило нумерации
    надо пересматривать, и упасть должно здесь, а не в геометрии.
    """
    from htmlslides.assembler import _read_pkg
    from htmlslides.library import TemplateLibrary

    bad = {}
    for spec in TemplateLibrary.load().templates:
        src = _read_pkg(f"templates/slides/{spec.id}.html")
        found = re.findall(r"<\s*(table|thead|tbody|tfoot|tr|td|th)\b", src)
        if found:
            bad[spec.id] = sorted(set(found))
    assert not bad, f"в шаблонах появились таблицы: {bad}"
