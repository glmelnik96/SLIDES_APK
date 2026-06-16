"""CLI: python -m htmlslides build input.md -o deck.html — отладка без Telegram."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="htmlslides",
        description="Self-contained HTML-дека в бренде Cloud.ru 2.0 из md/docx/pptx")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="собрать деку")
    build.add_argument("input", help="входной файл: .md / .docx / .pptx")
    build.add_argument("-o", "--out", default="deck.html", help="выходной .html")
    build.add_argument("--mode", choices=["auto", "rebrand"], default="auto",
                       help="rebrand: перенос структуры pptx по скриншотам")
    build.add_argument("--no-vision", action="store_true",
                       help="отключить vision-QA (быстрее/дешевле)")
    build.add_argument("--freeform-ok", action="store_true",
                       help="разрешить freeform-слайды вне шаблонов")
    build.add_argument("--max-autofix", type=int, default=1,
                       help="кругов autofix (0 — отключить)")
    build.add_argument("--keep-artifacts", metavar="DIR", default=None,
                       help="сохранить DeckPlan/SlideFill/скриншоты в DIR")
    build.add_argument("--theme", choices=["dark", "light"], default="dark",
                       help="тема деки (тёмная по умолчанию)")
    args = parser.parse_args(argv)

    _load_dotenv()
    from .pipeline.build import build_deck
    out = build_deck(
        args.input, args.out, mode=args.mode, vision=not args.no_vision,
        freeform_ok=args.freeform_ok, theme=args.theme, max_autofix=args.max_autofix,
        keep_artifacts=args.keep_artifacts,
        progress=lambda message: print(f"[htmlslides] {message}", flush=True))
    print(out)
    return 0


def _load_dotenv() -> None:
    """Подхватить .env из cwd (KEY=VALUE), не перетирая уже заданные переменные."""
    env = Path(".env")
    if not env.is_file():
        return
    for line in env.read_text("utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"'))
