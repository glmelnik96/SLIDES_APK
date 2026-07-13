"""Регрессия: pptx-загрузка не должна падать, если на машине нет LibreOffice.

Раньше режим rebrand (для pptx его включает pick_mode) жёстко пробрасывал
RenderUnavailable наружу — сборка падала с «LibreOffice (soffice) not found».
Правка: RenderUnavailable (нет soffice/pypdfium — «зрение» невозможно в принципе)
теперь деградирует на текстовый путь во ВСЕХ режимах, включая rebrand. При этом
сбой на конкретном файле (soffice есть, но упал/завис: CalledProcessError/
TimeoutExpired) в явном rebrand по-прежнему падает громко — это проблема файла,
а не машины, и её нельзя прятать.
"""
import subprocess

import htmlslides.parsers.render as render_mod
from htmlslides.pipeline import build


def _pptx(tmp_path):
    src = tmp_path / "deck.pptx"
    src.write_bytes(b"PK\x03\x04")   # содержимое неважно: render замокан
    return src


def test_rebrand_degrades_to_text_when_libreoffice_missing(tmp_path, monkeypatch):
    """rebrand + нет LibreOffice → пустой список и запись «работаю по тексту»."""
    def boom(*_a, **_k):
        raise render_mod.RenderUnavailable("LibreOffice (soffice) not found")

    monkeypatch.setattr(render_mod, "render_pptx_pngs", boom)
    msgs: list[str] = []
    images = build._render_source_slides(_pptx(tmp_path), "rebrand",
                                         tmp_path, msgs.append)
    assert images == []
    assert any("работаю по тексту" in m for m in msgs)


def test_auto_still_degrades_when_libreoffice_missing(tmp_path, monkeypatch):
    """Граница: в auto RenderUnavailable как и раньше молча деградирует."""
    def boom(*_a, **_k):
        raise render_mod.RenderUnavailable("LibreOffice (soffice) not found")

    monkeypatch.setattr(render_mod, "render_pptx_pngs", boom)
    images = build._render_source_slides(_pptx(tmp_path), "auto",
                                         tmp_path, lambda _m: None)
    assert images == []


def test_rebrand_still_raises_on_broken_file(tmp_path, monkeypatch):
    """Граница: soffice ЕСТЬ, но упал на файле — в явном rebrand падаем громко."""
    def choke(*_a, **_k):
        raise subprocess.CalledProcessError(1, "soffice")

    monkeypatch.setattr(render_mod, "render_pptx_pngs", choke)
    try:
        build._render_source_slides(_pptx(tmp_path), "rebrand",
                                    tmp_path, lambda _m: None)
    except subprocess.CalledProcessError:
        return
    raise AssertionError("ожидали проброс CalledProcessError в rebrand")
