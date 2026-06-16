"""Рендер слайдов PPTX в PNG: soffice headless -> PDF -> pypdfium2 -> PNG.

Нужен для vision-планировщика в режиме rebrand. Опционален: без LibreOffice
или extra [render] бросает RenderUnavailable — текстовый парсинг работает всегда.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    import pypdfium2
except ImportError:                  # extra [render] не установлен
    pypdfium2 = None


class RenderUnavailable(RuntimeError):
    """LibreOffice (soffice) или pypdfium2 недоступны."""


def find_soffice() -> str | None:
    found = shutil.which("soffice")
    if found:
        return found
    for candidate in (
        Path("C:/Program Files/LibreOffice/program/soffice.exe"),
        Path("C:/Program Files (x86)/LibreOffice/program/soffice.exe"),
        Path("/usr/bin/soffice"),
        Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"),
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def render_pptx_pngs(path: str | Path, out_dir: str | Path,
                     scale: float = 2.0) -> list[Path]:
    """PPTX -> по PNG на слайд в out_dir. Бросает RenderUnavailable/CalledProcessError."""
    soffice = find_soffice()
    if soffice is None:
        raise RenderUnavailable("LibreOffice (soffice) not found")
    if pypdfium2 is None:
        raise RenderUnavailable("pypdfium2 not installed: pip install 'htmlslides[render]'")
    source = Path(path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf",
             "--outdir", tmp, str(source)],
            check=True, capture_output=True, timeout=120)
        pdf_path = Path(tmp) / (source.stem + ".pdf")
        if not pdf_path.is_file():
            raise RenderUnavailable("soffice did not produce a pdf")
        pdf = pypdfium2.PdfDocument(str(pdf_path))
        try:
            files: list[Path] = []
            for index, page in enumerate(pdf, start=1):
                bitmap = page.render(scale=scale)
                png = out / f"slide-{index:02d}.png"
                bitmap.to_pil().save(str(png))
                files.append(png)
        finally:
            pdf.close()
    return files
