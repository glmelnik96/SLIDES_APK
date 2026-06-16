"""FastAPI app: home, job creation, progress WS, result/deck/png/history endpoints."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

if not os.environ.get("SLIDES_APP_SKIP_SHIM"):
    from webapp.config_shim import apply as _apply_shim
    _apply_shim()

from fastapi import (
    FastAPI, File, Form, HTTPException, Request, UploadFile, WebSocket,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from webapp import deck_edit, history, render_png
from webapp.paths import session_dir
from webapp.runner import JobRunner

_STATIC = Path(__file__).parent / "static"

# Allowed upload extensions per mode.
_ALLOWED = {
    "verstai": {".pptx"},
    "design": {".pptx"},
    "htmlnew": {".md", ".txt", ".docx", ".pptx"},
}
_PPTX_MODES = {"verstai", "design"}

app = FastAPI(title="Slides App")
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
runner = JobRunner()


@app.on_event("startup")
async def _startup() -> None:
    runner.bind_loop(asyncio.get_running_loop())


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((_STATIC / "index.html").read_text("utf-8"))


@app.get("/editor", response_class=HTMLResponse)
def editor() -> HTMLResponse:
    return HTMLResponse((_STATIC / "editor.html").read_text("utf-8"))


@app.post("/api/jobs")
async def create_job(mode: str = Form(...), file: UploadFile = File(...)) -> JSONResponse:
    from schemas.session import Mode, SessionInput
    if mode not in _ALLOWED:
        raise HTTPException(400, f"unsupported mode: {mode}")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED[mode]:
        raise HTTPException(400, f"bad file type {suffix} for mode {mode}")

    inp = SessionInput(user_id=0, chat_id=0, progress_message_id=0, mode=Mode(mode),
                       input_s3_key=None, source_filename=file.filename)
    dest = session_dir(inp.session_id) / f"input{suffix}"
    dest.write_bytes(await file.read())
    inp = inp.model_copy(update={"input_s3_key": str(dest)})

    runner.start(inp)
    kind = "pptx" if mode in _PPTX_MODES else "html"
    history.add(id=inp.session_id, mode=mode, source_filename=file.filename,
                result_path=None, kind=kind)
    return JSONResponse({"session_id": inp.session_id, "kind": kind})


@app.websocket("/ws/{session_id}")
async def ws_progress(ws: WebSocket, session_id: str) -> None:
    await ws.accept()
    queue = runner.queue(session_id)
    if queue is None:
        await ws.send_json({"stage": "failed", "terminal": True,
                            "error": "unknown session"})
        await ws.close()
        return
    while True:
        event = await queue.get()
        await ws.send_json(event)
        if event.get("terminal"):
            break
    await ws.close()


@app.get("/api/jobs/{session_id}/result")
def download_result(session_id: str) -> FileResponse:
    path = runner.result_path(session_id)
    if not path or not Path(path).is_file():
        raise HTTPException(404, "result not ready")
    return FileResponse(path, filename=Path(path).name)


@app.get("/api/jobs/{session_id}/deck", response_class=HTMLResponse)
def get_deck(session_id: str, download: int = 0):
    path = deck_edit.ensure_deck(session_id, runner.result_path(session_id))
    if path is None:
        raise HTTPException(404, "deck not found")
    if download:
        return FileResponse(path, filename="deck.html", media_type="text/html")
    return HTMLResponse(path.read_text("utf-8"))


@app.post("/api/jobs/{session_id}/deck")
async def post_deck(session_id: str, request: Request) -> JSONResponse:
    body = await request.body()
    deck_edit.save_deck(session_id, body.decode("utf-8"))
    return JSONResponse({"ok": True})


@app.post("/api/jobs/{session_id}/chat")
async def post_chat(session_id: str, request: Request) -> JSONResponse:
    """Rewrite one slide of the deck per a chat instruction (htmlnew/HTML decks)."""
    from webapp import chat_edit
    data = await request.json()
    try:
        slide_index = int(data["slide_index"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "slide_index required (int)")
    instruction = (data.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(400, "instruction required")
    deck = deck_edit.ensure_deck(session_id, runner.result_path(session_id))
    if deck is None:
        raise HTTPException(404, "deck not found")
    html = deck.read_text("utf-8")
    try:
        new_html = await run_in_threadpool(
            chat_edit.rewrite_slide, html, slide_index, instruction)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 — surface a clear message
        raise HTTPException(500, f"chat edit failed: {exc}") from exc
    deck_edit.save_deck(session_id, new_html)
    return JSONResponse({"ok": True})


@app.get("/api/jobs/{session_id}/png.zip")
def get_png_zip(session_id: str) -> FileResponse:
    deck = deck_edit.ensure_deck(session_id, runner.result_path(session_id))
    if deck is None:
        raise HTTPException(404, "deck not found")
    out = session_dir(session_id) / "deck.zip"
    try:
        render_png.export_zip(deck, out)
    except Exception as exc:  # noqa: BLE001 — surface a clear hint
        raise HTTPException(500, f"PNG export failed: {exc}. "
                                 f"Try: playwright install chromium") from exc
    return FileResponse(out, filename="slides.zip", media_type="application/zip")


@app.get("/api/history")
def get_history() -> JSONResponse:
    return JSONResponse(history.list_recent())


@app.post("/api/history/clear")
def clear_history() -> JSONResponse:
    history.clear()
    return JSONResponse({"ok": True})
