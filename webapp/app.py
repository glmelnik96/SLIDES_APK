"""FastAPI app: home, job creation, progress WS, result/deck/png/history endpoints."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

if not os.environ.get("SLIDES_APP_SKIP_SHIM"):
    from webapp.config_shim import apply as _apply_shim
    _apply_shim()

from fastapi import (
    Depends, FastAPI, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from webapp import deck_edit, jobs_repo, render_png
from webapp.auth import get_current_user
from webapp.paths import session_dir
from webapp.runner import CapacityError, JobRunner

_STATIC = Path(__file__).parent / "static"


def _serve_shell(name: str, *, email: str = "") -> "HTMLResponse":
    """Serve an app-shell HTML page, gateway-prefixed and cache-busted.

    Two transforms:
    1. Cache-bust: append ?v=<latest static mtime> to local .js/.css so a shipped
       fix actually reaches browsers instead of being served from stale cache.
    2. Gateway prefix: the gateway mounts this app under APP_PREFIX (e.g. /slides)
       and strips it when proxying. The browser, however, lives at /<prefix>/, so
       absolute asset/API URLs must carry the prefix. We prepend it to /static
       refs here and expose it as window.__APP_PREFIX__ so the JS prefixes every
       fetch / EventSource / navigation URL too. Empty prefix = standalone dev.
    """
    import json
    import re

    from webapp.config import settings
    prefix = settings.normalized_prefix()
    html = (_STATIC / name).read_text("utf-8")
    mtimes = [p.stat().st_mtime for p in _STATIC.glob("*.js")]
    mtimes += [p.stat().st_mtime for p in _STATIC.glob("*.css")]
    token = str(int(max(mtimes))) if mtimes else "0"
    html = re.sub(r'(/static/[\w./-]+\.(?:js|css))"', rf'{prefix}\1?v={token}"', html)
    inject = f"<script>window.__APP_PREFIX__={json.dumps(prefix)};</script>"
    html = html.replace("<head>", "<head>\n" + inject, 1)
    # Canon topbar shows the gateway-supplied email; no email = standalone dev.
    from html import escape as _esc
    html = html.replace("{{ email }}", _esc(email))
    return HTMLResponse(html)

# App2 is HTML-only: the sole mode is htmlnew (document → editable HTML deck).
# PPTX rebrand/design modes are out of scope for this deployment.
_ALLOWED = {
    "htmlnew": {".md", ".txt", ".docx", ".pptx"},
}

from webapp.config import settings as _settings

app = FastAPI(title="Slides App")
app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
runner = JobRunner(max_active=_settings.max_active,
                   max_per_user=_settings.user_queue_limit,
                   build_workers=_settings.build_workers,
                   build_timeout_sec=_settings.build_timeout_sec)


@app.on_event("startup")
async def _startup() -> None:
    runner.bind_loop(asyncio.get_running_loop())
    from webapp.config import settings
    from webapp.db.database import init_db, make_engine, make_sessionmaker
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.db_url)
    await init_db(engine)
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)

    async def _persist_terminal(session_id: str, data: dict) -> None:
        status = data.get("stage", "failed")
        async with app.state.sessionmaker() as s:
            await jobs_repo.mark_terminal(
                s, session_id, status=status,
                result_path=data.get("result_path"), error=data.get("error"))
            await s.commit()
        # Usage log — best-effort and isolated in its OWN transaction so a logging
        # failure can never roll back / block the terminal status above.
        try:
            from webapp import usage
            async with app.state.sessionmaker() as s:
                await usage.log_render(
                    s, owner_user_id=runner.owner(session_id), status=status,
                    workflow=runner.workflow(session_id),
                    started_at=runner.started_at(session_id),
                    result_path=data.get("result_path"))
                await s.commit()
        except Exception:  # noqa: BLE001 — analytics must never affect the build
            pass

    runner.set_terminal_hook(_persist_terminal)

    # In-memory queue is gone after a restart → fail orphaned non-terminal rows.
    from webapp import retention
    await retention.reconcile_interrupted(app.state.sessionmaker)
    app.state._retention_task = asyncio.create_task(
        retention.retention_loop(app.state.sessionmaker,
                                 ttl_hours=settings.retention_hours),
        name="retention-loop")


async def _owned_or_404(request: Request, session_id: str, user):
    """Refuse access to a session the current user does not own (404, not 403,
    so existence isn't leaked). Returns the Job row."""
    async with request.app.state.sessionmaker() as s:
        job = await jobs_repo.get_owned(s, session_id, user.id)
    if job is None:
        raise HTTPException(404, "not found")
    return job


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return _serve_shell("index.html",
                        email=request.headers.get("X-User-Email", ""))


@app.get("/editor", response_class=HTMLResponse)
def editor(request: Request) -> HTMLResponse:
    return _serve_shell("editor.html",
                        email=request.headers.get("X-User-Email", ""))


@app.post("/api/jobs")
async def create_job(request: Request, mode: str = Form(...),
                     file: UploadFile = File(...),
                     user=Depends(get_current_user)) -> JSONResponse:
    from schemas.session import Mode, SessionInput
    if mode not in _ALLOWED:
        raise HTTPException(400, f"unsupported mode: {mode}")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED[mode]:
        raise HTTPException(400, f"bad file type {suffix} for mode {mode}")

    raw = await file.read()
    # Reject an empty upload up front: an all-whitespace doc would otherwise pass
    # validation, occupy a queue slot and "succeed" with a contentless cover+
    # contacts deck. Binary inputs (docx/pptx) carry structure even when "small",
    # so only the plain-text formats are whitespace-checked.
    if not raw.strip() or (suffix in (".md", ".txt")
                           and not raw.decode("utf-8", "ignore").strip()):
        raise HTTPException(400, "файл пустой — загрузите документ с содержимым")

    inp = SessionInput(user_id=user.id, chat_id=0, progress_message_id=0,
                       mode=Mode(mode), input_s3_key=None,
                       source_filename=file.filename)
    dest = session_dir(inp.session_id) / f"input{suffix}"
    dest.write_bytes(raw)
    inp = inp.model_copy(update={"input_s3_key": str(dest)})

    kind = "html"
    # Persist ownership BEFORE starting so even a fast terminal can update it.
    async with request.app.state.sessionmaker() as s:
        await jobs_repo.create(s, session_id=inp.session_id, user_id=user.id,
                               mode=mode, kind=kind, source_filename=file.filename)
        await s.commit()
    try:
        runner.start(inp, user_id=user.id)
    except CapacityError as exc:
        raise HTTPException(429, str(exc))
    return JSONResponse({"session_id": inp.session_id, "kind": kind})


@app.get("/api/jobs/active")
def active_jobs(user=Depends(get_current_user)) -> JSONResponse:
    """The current user's jobs still building, with live stage/pct."""
    return JSONResponse(runner.active_jobs(user_id=user.id))


@app.post("/api/jobs/{session_id}/cancel")
async def cancel_job(session_id: str, request: Request,
                     user=Depends(get_current_user)) -> JSONResponse:
    """Stop a running or queued build (owner only). Cooperative — a running job
    aborts at its next progress checkpoint."""
    await _owned_or_404(request, session_id, user)
    if not runner.cancel(session_id):
        raise HTTPException(404, "job not active")
    return JSONResponse({"ok": True})


@app.get("/api/jobs/{session_id}/status")
async def job_status(session_id: str, request: Request,
                     user=Depends(get_current_user)) -> JSONResponse:
    await _owned_or_404(request, session_id, user)
    st = runner.status(session_id)
    if st is None:
        raise HTTPException(404, "unknown session")
    return JSONResponse(st)


@app.get("/api/jobs/{session_id}/events")
async def job_events(session_id: str, request: Request,
                     user=Depends(get_current_user)):
    """Progress stream over SSE (owner only). The gateway proxies HTTP streaming
    (not WebSocket), so progress rides Server-Sent Events. Emits the current
    snapshot, then each queued event until a terminal one closes the stream."""
    import asyncio
    import json as _json

    from sse_starlette.sse import EventSourceResponse

    await _owned_or_404(request, session_id, user)
    status = runner.status(session_id)
    queue = runner.queue(session_id)

    async def gen():
        if status is None and queue is None:
            yield {"data": _json.dumps({"stage": "failed", "terminal": True,
                                        "error": "unknown session"})}
            return
        if status is not None:
            yield {"data": _json.dumps(status)}
            if status.get("terminal"):
                return
        if queue is None:
            return
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue  # keep the connection alive, re-check disconnect
            yield {"data": _json.dumps(event)}
            if event.get("terminal"):
                break

    return EventSourceResponse(gen())


@app.get("/api/jobs/{session_id}/deck", response_class=HTMLResponse)
async def get_deck(session_id: str, request: Request, download: int = 0,
                   user=Depends(get_current_user)):
    await _owned_or_404(request, session_id, user)
    path = deck_edit.ensure_deck(session_id, runner.result_path(session_id))
    if path is None:
        raise HTTPException(404, "deck not found")
    if download:
        return FileResponse(path, filename="deck.html", media_type="text/html")
    return HTMLResponse(path.read_text("utf-8"))


@app.post("/api/jobs/{session_id}/deck")
async def post_deck(session_id: str, request: Request,
                    user=Depends(get_current_user)) -> JSONResponse:
    await _owned_or_404(request, session_id, user)
    body = await request.body()
    deck_edit.save_deck(session_id, body.decode("utf-8"))
    return JSONResponse({"ok": True})


@app.post("/api/jobs/{session_id}/chat")
async def post_chat(session_id: str, request: Request,
                    user=Depends(get_current_user)) -> JSONResponse:
    """Rewrite one slide of the deck per a chat instruction (owner only)."""
    from webapp import chat_edit
    await _owned_or_404(request, session_id, user)
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
async def get_png_zip(session_id: str, request: Request,
                      user=Depends(get_current_user)) -> FileResponse:
    await _owned_or_404(request, session_id, user)
    deck = deck_edit.ensure_deck(session_id, runner.result_path(session_id))
    if deck is None:
        raise HTTPException(404, "deck not found")
    out = session_dir(session_id) / "deck.zip"
    try:
        await run_in_threadpool(render_png.export_zip, deck, out)
    except Exception as exc:  # noqa: BLE001 — surface a clear hint
        raise HTTPException(500, f"PNG export failed: {exc}. "
                                 f"Try: playwright install chromium") from exc
    return FileResponse(out, filename="slides.zip", media_type="application/zip")


@app.get("/api/history")
async def get_history(request: Request,
                      user=Depends(get_current_user)) -> JSONResponse:
    async with request.app.state.sessionmaker() as s:
        jobs = await jobs_repo.list_for_user(s, user.id)
    return JSONResponse([
        {"id": j.session_id, "mode": j.mode, "kind": j.kind,
         "source_filename": j.source_filename, "status": j.status,
         "error": j.error,
         "created_at": j.created_at.isoformat() if j.created_at else None}
        for j in jobs
    ])


@app.post("/api/history/clear")
async def clear_history(request: Request,
                        user=Depends(get_current_user)) -> JSONResponse:
    import shutil
    async with request.app.state.sessionmaker() as s:
        session_ids = await jobs_repo.delete_for_user(s, user.id)
        await s.commit()
    for sid in session_ids:
        d = session_dir(sid)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    return JSONResponse({"ok": True})
