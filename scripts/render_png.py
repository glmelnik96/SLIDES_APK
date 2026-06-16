"""Render a .pptx to per-slide PNGs for visual validation.

Usage: python -m scripts.render_png <deck.pptx> [out_dir] [--zoom 1.5]
Writes <out_dir>/<stem>_s{N}.png (1-based). Prints the paths so the agent
can Read each PNG and visually compare against the brand reference.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import fitz  # PyMuPDF


def _find_soffice() -> str:
    """Resolve the LibreOffice binary (it is often not on PATH on Windows)."""
    env = os.environ.get("SOFFICE_BIN")
    if env and Path(env).is_file():
        return env
    on_path = shutil.which("soffice") or shutil.which("soffice.exe")
    if on_path:
        return on_path
    candidates = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/opt/libreoffice/program/soffice",
    ]
    for c in candidates:
        if Path(c).is_file():
            return c
    raise RuntimeError("LibreOffice (soffice) not found; set SOFFICE_BIN")


# Per-thread LibreOffice user profile: concurrent soffice instances would
# fight over the shared profile lock and fail silently/hang (required for the
# parallel compose vision-QA renders). Reused within a thread so the ~5s
# first-run profile bootstrap is paid once per pool thread, not per render.
_lo_profile = threading.local()


def _lo_profile_uri() -> str:
    prof = getattr(_lo_profile, "dir", None)
    if prof is None:
        prof = tempfile.TemporaryDirectory(prefix="lo_profile_")
        _lo_profile.dir = prof  # finalizer cleans up at thread/process teardown
    return Path(prof.name).as_uri()


def pptx_to_pdf(pptx: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [_find_soffice(), "--headless",
         f"-env:UserInstallation={_lo_profile_uri()}",
         "--convert-to", "pdf", "--outdir", str(out_dir), str(pptx)],
        check=True, timeout=180,
    )
    pdf = out_dir / (pptx.stem + ".pdf")
    if not pdf.is_file():
        raise RuntimeError(f"LibreOffice did not produce {pdf}")
    return pdf


def pdf_to_pngs(pdf: Path, out_dir: Path, stem: str, zoom: float) -> list[Path]:
    doc = fitz.open(pdf)
    mat = fitz.Matrix(zoom, zoom)
    paths: list[Path] = []
    for i, page in enumerate(doc, start=1):
        pix = page.get_pixmap(matrix=mat)
        p = out_dir / f"{stem}_s{i}.png"
        p.write_bytes(pix.tobytes("png"))
        paths.append(p)
    doc.close()
    return paths


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: python -m scripts.render_png <deck.pptx> [out_dir] [--zoom Z]",
              file=sys.stderr)
        return 2
    pptx = Path(sys.argv[1])
    if not pptx.is_file():
        print(f"ERROR: not found: {pptx}", file=sys.stderr)
        return 2
    zoom = 1.5
    args = list(sys.argv[2:])
    if "--zoom" in args:
        zi = args.index("--zoom")
        zoom = float(args[zi + 1])
        del args[zi:zi + 2]
    out_dir = Path(args[0]) if args else pptx.parent / "png"
    pdf = pptx_to_pdf(pptx, out_dir)
    pngs = pdf_to_pngs(pdf, out_dir, pptx.stem, zoom)
    for p in pngs:
        print(p, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
