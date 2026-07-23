"""Регресс: .md/.txt в не-UTF-8 кодировках не должны ронять парсер.

Реальные русские .txt/.md часто в Windows-1251 или UTF-16 (Блокнот «Юникод»).
Раньше parse_file читал их как UTF-8 и падал с UnicodeDecodeError — билд «периодически
вылетал». read_text_smart угадывает кодировку; проверяем цепочку и сквозной parse_file.
"""
from htmlslides.parsers import parse_file
from htmlslides.parsers.base import decode_smart, read_text_smart

CYR = "Заголовок\n\nПервый тезис с цифрой 42.\nВторой тезис.\n"


def test_read_text_smart_cp1251(tmp_path):
    p = tmp_path / "win.txt"
    p.write_bytes(CYR.encode("cp1251"))
    assert read_text_smart(p) == CYR


def test_read_text_smart_utf16(tmp_path):
    p = tmp_path / "notepad.txt"
    p.write_bytes(CYR.encode("utf-16"))  # BOM + UTF-16
    assert read_text_smart(p) == CYR


def test_read_text_smart_utf8_bom(tmp_path):
    p = tmp_path / "bom.md"
    p.write_bytes(CYR.encode("utf-8-sig"))
    assert read_text_smart(p) == CYR


def test_read_text_smart_plain_utf8(tmp_path):
    p = tmp_path / "plain.md"
    p.write_text(CYR, encoding="utf-8", newline="")
    assert read_text_smart(p) == CYR


def test_parse_file_cp1251_txt_does_not_crash(tmp_path):
    p = tmp_path / "win.txt"
    p.write_bytes("Первый тезис.\nВторой тезис.\n".encode("cp1251"))
    doc = parse_file(p)  # раньше — UnicodeDecodeError
    text = " ".join(b.text for s in doc.sections for b in s.blocks if hasattr(b, "text"))
    assert "Первый тезис" in text


def test_parse_file_cp1251_md_headings(tmp_path):
    p = tmp_path / "win.md"
    p.write_bytes("# Заголовок\n\nТекст раздела.\n".encode("cp1251"))
    doc = parse_file(p)
    assert doc.title == "Заголовок"


def test_decode_smart_cp1251_bytes():
    assert decode_smart(CYR.encode("cp1251")) == CYR


def test_decode_smart_pure_cyrillic_not_empty():
    # Пустой-файл-гейт на загрузке проверяет непустоту через decode_smart. Раньше
    # использовался decode("utf-8","ignore") — на чисто кириллическом cp1251 без
    # ASCII-цифр/пунктуации он выдавал "", и реальный файл падал как «пустой».
    raw = "Привет всем участникам встречи".encode("cp1251")
    assert decode_smart(raw).strip()
