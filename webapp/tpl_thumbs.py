"""PNG-миниатюры превью макетов/типов схем для пикера конструктора.

22 живых full-deck iframe — главная тяжесть пикера: простыня ~3000px тормозит
слабые машины. Клиент просит статичный PNG; генерим лениво при первом запросе
той же Playwright-скриншотилкой, что vision-QA, и кэшируем на диске.
Chromium недоступен → ThumbUnavailable → эндпоинт отдаёт 404, клиент падает
обратно на live-iframe (мягкая деградация, поведение как до этой фичи).
"""
from __future__ import annotations

import tempfile
from importlib import resources
from pathlib import Path

from webapp import paths


class ThumbUnavailable(RuntimeError):
    """Playwright/Chromium недоступен — вызывающему вернуть 404."""


def cache_dir() -> Path:
    # Рядом с сессиями: <workdir>/../tpl-thumbs. На VM SLIDESBOT_WORKDIR=
    # /opt/app2/data/sessions → кэш в /opt/app2/data/tpl-thumbs (внутри
    # ReadWritePaths юнита); локально — tempdir/slidesapp/tpl-thumbs.
    d = paths.workdir_root().parent / "tpl-thumbs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _library_mtime() -> float:
    with resources.as_file(resources.files("htmlslides") / "templates"
                           / "library.json") as p:
        return p.stat().st_mtime


def get_thumb(key: str, theme: str, render_html) -> Path:
    """PNG по ключу кэша; render_html() зовётся только на промахе.

    Инвалидация — по mtime library.json: правка каталога делает все
    закэшированные PNG старее файла, и они перегенерятся сами."""
    png = cache_dir() / f"{key}-{theme}.png"
    if png.exists() and png.stat().st_mtime >= _library_mtime():
        return png
    _render_png(render_html(), png)
    return png


def _render_png(html: str, out: Path) -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:                     # extra [qa] не установлен
        raise ThumbUnavailable("playwright не установлен") from exc
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "preview.html"
        src.write_text(html, encoding="utf-8")
        # Суффикс обязан остаться .png: Playwright определяет формат скриншота
        # по расширению пути и на «.png.tmp» падает с Unsupported mime type.
        tmp = out.with_name(out.stem + ".tmp.png")
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch()
                # 480×270: дека сама вписывает слайд 1920×1080 в вьюпорт
                # (transform-scale в deck.js) — готовая миниатюра без ресайза.
                # reduced_motion — финальные кадры входов/графиков (паттерн
                # htmlslides/pipeline/screenshot.py).
                page = browser.new_page(
                    viewport={"width": 480, "height": 270},
                    reduced_motion="reduce")
                page.goto(src.resolve().as_uri())
                page.add_style_tag(content=".deck-progress{display:none}")
                page.wait_for_timeout(200)
                page.screenshot(path=str(tmp))
                browser.close()
        except Exception as exc:              # chromium недоступен / краш
            tmp.unlink(missing_ok=True)
            raise ThumbUnavailable(str(exc)) from exc
        tmp.replace(out)   # атомарно: полузаписанный PNG не попадёт в кэш
