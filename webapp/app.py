"""FastAPI app: home, job creation, progress WS, result/deck/png/history endpoints."""
from __future__ import annotations

import asyncio
import json as _json
import logging
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

from webapp import (
    chat_edit, deck_edit, draft, draft_render, exports, jobs_repo, render_png,
    render_pptx,
)
from webapp.auth import get_current_user
from webapp.paths import session_dir
from webapp.runner import CapacityError, JobRunner

_STATIC = Path(__file__).parent / "static"
logger = logging.getLogger(__name__)


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
    # Г§9 — expose the data-retention window so the UI can announce it (silent
    # deletion of history/drafts is a least-astonishment violation otherwise).
    inject = (f"<script>window.__APP_PREFIX__={json.dumps(prefix)};"
              f"window.__RETENTION_HOURS__={int(settings.retention_hours)};</script>")
    html = html.replace("<head>", "<head>\n" + inject, 1)
    # Canon topbar shows the gateway-supplied email; no email = standalone dev.
    from html import escape as _esc
    html = html.replace("{{ email }}", _esc(email))
    # The shell HTML itself carries no version token (only its .js/.css refs do),
    # so a browser that cached an older document keeps showing stale markup after
    # a ship (e.g. a hidden entry card reappears). Force revalidation of the shell
    # so HTML-level fixes reach clients immediately; assets stay cache-busted by ?v=.
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

# App2 is HTML-only: the sole mode is htmlnew (document → editable HTML deck).
# PPTX rebrand/design modes are out of scope for this deployment.
_ALLOWED = {
    "htmlnew": {".md", ".txt", ".docx", ".pptx"},
}
# «Точный перенос» (mode=exact) переносит источник послайдово 1-в-1 без ИИ: в
# .pptx слайды есть, в .md/.txt границы задаёт метка «Слайд N:». В .docx понятия
# «слайд» нет (parse_docx режет по стилям заголовков), поэтому exact его не берёт.
# Раньше эта связка проходила приём, занимала слот очереди и падала глубоко в
# воркере голым ValueError — теперь режем на входе понятной 400.
_EXACT_ALLOWED = {".pptx", ".md", ".txt"}

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
    # Keep strong refs to fire-and-forget usage-push tasks so the loop can't GC
    # them mid-flight (create_task only holds a weak reference).
    app.state._usage_tasks = set()

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
        # Cross-app usage push to the gateway (variant B). Fire-and-forget: identity
        # args are captured NOW (runner meta may be cleared before the task runs);
        # the coroutine swallows its own errors and no-ops when the token is unset.
        try:
            from webapp import usage
            task = asyncio.create_task(usage.report_to_gateway(
                app.state.sessionmaker,
                owner_user_id=runner.owner(session_id), status=status,
                workflow=runner.workflow(session_id),
                started_at=runner.started_at(session_id),
                result_path=data.get("result_path")))
            app.state._usage_tasks.add(task)
            task.add_done_callback(app.state._usage_tasks.discard)
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


_DECK_LOCKS: dict[str, asyncio.Lock] = {}


def _deck_lock(session_id: str) -> asyncio.Lock:
    """Per-session lock serializing writes to deck.html, so concurrent editors
    (browser save vs chat rewrite) can't interleave a read-modify-write."""
    lock = _DECK_LOCKS.get(session_id)
    if lock is None:
        lock = _DECK_LOCKS.setdefault(session_id, asyncio.Lock())
    return lock


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
                     exact_transfer: str = Form(default="false"),
                     file: UploadFile = File(...),
                     user=Depends(get_current_user)) -> JSONResponse:
    from schemas.session import Mode, SessionInput
    if mode not in _ALLOWED:
        raise HTTPException(400, f"unsupported mode: {mode}")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED[mode]:
        raise HTTPException(400, f"bad file type {suffix} for mode {mode}")

    exact = exact_transfer.lower() in ("1", "true", "on", "yes")
    if exact and suffix not in _EXACT_ALLOWED:
        raise HTTPException(
            400,
            f"«Точный перенос» не поддерживает {suffix} — снимите галочку "
            "«Точный перенос» (обычная сборка отлично переносит Word) или "
            "загрузите .pptx, .md либо .txt.")

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
                       source_filename=file.filename,
                       exact_transfer=exact)
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


_DRAFT_MODES = {"manual", "chat"}


@app.post("/api/drafts")
async def create_draft(request: Request, user=Depends(get_current_user)
                       ) -> JSONResponse:
    """Start an empty draft deck (manual-fill or chat builder). Unlike /api/jobs
    this runs no engine build — it creates an owned session whose source of truth
    is plan.json (DeckPlan-as-truth); deck.html is rendered from it. The draft then
    flows through the same /editor and /api/jobs/{id}/deck|png endpoints."""
    from uuid import uuid4
    data = await _json_body(request)
    mode = (data.get("mode") or "manual")
    if mode not in _DRAFT_MODES:
        raise HTTPException(400, f"unsupported draft mode: {mode}")
    session_id = uuid4().hex[:16]
    plan = draft.DraftPlan(title=str(data.get("title") or ""))
    draft.save_plan(session_id, plan)
    draft_render.render_draft(session_id, plan)   # seed an (empty-state) deck.html
    # Ownership row; status "draft" keeps it out of the runner queue, retention
    # reconcile (queued/running only) and the build history (terminal only).
    async with request.app.state.sessionmaker() as s:
        await jobs_repo.create(s, session_id=session_id, user_id=user.id,
                               mode=mode, kind="draft", source_filename=None,
                               status="draft")
        await s.commit()
    return JSONResponse({"session_id": session_id, "kind": "draft", "mode": mode})


@app.get("/api/drafts")
async def list_drafts(request: Request,
                      user=Depends(get_current_user)) -> JSONResponse:
    """Черновики пользователя (status="draft") — незавершённая работа, снова
    достижимая с главной («Продолжить» ведёт обратно в редактор)."""
    async with request.app.state.sessionmaker() as s:
        jobs = await jobs_repo.list_drafts_for_user(s, user.id)
    return JSONResponse([
        {"id": j.session_id, "mode": j.mode,
         "created_at": j.created_at.isoformat() if j.created_at else None}
        for j in jobs
    ])


@app.get("/api/templates")
def list_templates(user=Depends(get_current_user)) -> JSONResponse:
    """Slide-template catalog with slot contracts (drives the manual builder)."""
    from webapp import templates_api
    return JSONResponse(templates_api.catalog())


# Правила покоя для статичного превью пикера: копия print-блока
# htmlslides/engine/motion.css:175–186 БЕЗ обёртки @media print — входы, лупы и
# графики в финальном видимом состоянии, чтобы ~20 превью не мельтешили.
# motion.css не редактируется; при правках блока печати синхронизировать вручную.
_PREVIEW_QUIET_STYLE = (
    "<style>"
    ".m-enter,.m-enter-left,.m-enter-right,.m-enter-scale,"
    ".m-fade,.m-stagger>*{opacity:1 !important;animation:none !important;}"
    ".m-loop-pulse,.m-loop-drift,.m-loop-draw,.m-loop-float{"
    "animation:none !important;opacity:1 !important;transform:none !important;}"
    ".bar-fill,.sb-seg{animation:none !important;transform:scaleX(1) !important;}"
    ".arc-draw{animation:none !important;stroke-dashoffset:var(--draw-to,0) !important;}"
    ".line-draw{animation:none !important;stroke-dashoffset:0 !important;}"
    ".line-fade{animation:none !important;opacity:1 !important;}"
    "</style>"
)


@app.get("/api/templates/{template_id}/preview", response_class=HTMLResponse)
def template_preview(template_id: str, static: bool = False,
                     user=Depends(get_current_user)):
    """A one-slide deck with representative sample content — the visual preview
    shown in the template picker. Reuses the real engine so the preview matches
    the actual output. ``static=1`` injects quiet motion rules so the picker
    shows still final frames instead of ~20 looping decks."""
    from htmlslides.assembler import assemble
    from htmlslides.library import TemplateLibrary
    from htmlslides.models import DeckPlan, SlidePlan
    from webapp import templates_api
    try:
        spec = TemplateLibrary.load().get(template_id)
    except Exception:  # noqa: BLE001
        raise HTTPException(404, "unknown template")
    plan = DeckPlan(title="", slides=[SlidePlan(
        index=1, type=spec.type, template_id=template_id,
        content=templates_api.sample_content(template_id))])
    html = assemble(plan)
    if static:
        html = html.replace("</head>", _PREVIEW_QUIET_STYLE + "</head>", 1)
    return HTMLResponse(html)


def _validation_errors(template_id: str, content: dict) -> list[dict]:
    from htmlslides.library import TemplateLibrary
    errs = TemplateLibrary.load().validate_content(template_id, content)
    return [{"code": e.code, "slot": e.slot, "detail": e.detail} for e in errs]


async def _json_body(request: Request) -> dict:
    """Parse a JSON request body, returning a clean 400 (not a 500) on malformed
    input. An empty body is treated as {} so optional-body endpoints still work."""
    raw = await request.body()
    if not raw:
        return {}
    try:
        data = _json.loads(raw)
    except ValueError:
        raise HTTPException(400, "malformed JSON body")
    if not isinstance(data, dict):
        raise HTTPException(400, "JSON object expected")
    return data


async def _draft_or_404(request: Request, session_id: str, user,
                        *, mutate: bool = False) -> draft.DraftPlan:
    """Owner-only access to a draft; returns its current DraftPlan.

    Guards the DeckPlan-as-truth lifecycle: after a successful engine rebuild
    plan.json is dropped (the session becomes HTML-as-truth), so draft endpoints
    must refuse instead of silently operating on an empty plan — otherwise a
    stale tab could overwrite the built deck with a near-empty one. Mutations
    are also refused while a rebuild is running, so edits aren't silently lost
    to the worker that loaded the plan at start."""
    await _owned_or_404(request, session_id, user)
    if not draft.plan_path(session_id).is_file():
        raise HTTPException(
            409, "черновик уже собран движком — правьте деку в редакторе")
    if mutate:
        st = runner.status(session_id)
        if st is not None and not st.get("terminal"):
            raise HTTPException(
                409, "идёт пересборка черновика — дождитесь завершения")
    return draft.load_plan(session_id)


@app.get("/api/drafts/{session_id}")
async def get_draft(session_id: str, request: Request,
                    user=Depends(get_current_user)) -> JSONResponse:
    plan = await _draft_or_404(request, session_id, user)
    return JSONResponse(plan.model_dump())


@app.delete("/api/drafts/{session_id}")
async def delete_draft(session_id: str, request: Request,
                       user=Depends(get_current_user)) -> JSONResponse:
    """Delete a draft session (owner only). 404 if it doesn't exist or is not a
    draft (a real build must go through history/clear, never here). Cleanup mirrors
    clear_history: drop the Job row, drop any export state, remove the session dir."""
    import shutil
    async with request.app.state.sessionmaker() as s:
        job = await jobs_repo.get_owned(s, session_id, user.id)
        if job is None or job.status != "draft":
            raise HTTPException(404, "not found")
        await s.delete(job)
        await s.commit()
    exports.registry.drop(session_id)
    d = session_dir(session_id)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
    return JSONResponse({"ok": True})


def _persist_draft(session_id: str, plan: draft.DraftPlan) -> None:
    """Save the plan and re-render the derived deck.html from it."""
    draft.save_plan(session_id, plan)
    draft_render.render_draft(session_id, plan)


@app.put("/api/drafts/{session_id}")
async def replace_draft(session_id: str, request: Request,
                        user=Depends(get_current_user)) -> JSONResponse:
    """Replace the whole draft plan in one call. The editor's undo/redo restores
    a previously-captured snapshot this way; the derived deck re-renders from it.
    Same ownership / lifecycle guards as the slide endpoints."""
    from pydantic import ValidationError
    await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    try:
        plan = draft.DraftPlan.model_validate(data)
    except ValidationError:
        raise HTTPException(400, "invalid plan")
    _persist_draft(session_id, plan)
    return JSONResponse(plan.model_dump())


@app.post("/api/drafts/{session_id}/slides")
async def add_draft_slide(session_id: str, request: Request,
                          user=Depends(get_current_user)) -> JSONResponse:
    from webapp import templates_api
    plan = await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    template_id = data.get("template_id")
    if not template_id or template_id not in {t["id"] for t in templates_api.catalog()}:
        raise HTTPException(400, "valid template_id required")
    at = data.get("at")
    # Fields start EMPTY (plan.json keeps the user's raw content); the representative
    # filler ("рыба-текст") is supplied at render time by draft_render, so a fresh
    # master shows example text in the slide while its input stays empty. A layout
    # swap still carries over the caller's overlapping slots.
    plan = draft.add_slide(plan, draft.DraftSlide(
        template_id=template_id, content=data.get("content") or {}), at=at)
    _persist_draft(session_id, plan)
    return JSONResponse(plan.model_dump())


@app.put("/api/drafts/{session_id}/slides/{index}")
async def update_draft_slide(session_id: str, index: int, request: Request,
                             user=Depends(get_current_user)) -> JSONResponse:
    plan = await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    content = data.get("content")
    if not isinstance(content, dict):
        raise HTTPException(400, "content object required")
    try:
        target = plan.slides[index - 1]
        plan = draft.update_slide(plan, index, content=content)
    except IndexError:
        raise HTTPException(404, "slide not found")
    _persist_draft(session_id, plan)
    # Soft validation: the deck still renders (clamped), but report any contract
    # issues so the UI can flag fields without blocking the edit.
    errors = (_validation_errors(target.template_id, content)
              if target.template_id else [])
    return JSONResponse({"plan": plan.model_dump(), "errors": errors})


@app.put("/api/drafts/{session_id}/slides/{index}/html")
async def update_draft_slide_html(session_id: str, index: int, request: Request,
                                  user=Depends(get_current_user)) -> JSONResponse:
    """Persist an in-place (contenteditable) edit of a draft slide. The edited
    slide becomes freeform (its <section> HTML is the content), mirroring how a
    chat-rewrite is stored — so a later form/agent edit doesn't clobber it."""
    plan = await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    html = (data.get("html") or "").strip()
    if not html:
        raise HTTPException(400, "html required")
    section = chat_edit.nth_section(html, 1) or html  # accept a bare <section>
    if not (1 <= index <= len(plan.slides)):
        raise HTTPException(404, "slide not found")
    plan = draft.update_slide(plan, index, content={"html": section})
    plan.slides[index - 1].freeform = True
    _persist_draft(session_id, plan)
    return JSONResponse(plan.model_dump())


@app.put("/api/drafts/{session_id}/slides/{index}/fields")
async def update_draft_slide_fields(session_id: str, index: int, request: Request,
                                    user=Depends(get_current_user)) -> JSONResponse:
    """Set a slide's typed structured content (slide_type + fields). Validated
    against the type contract; on success the slide renders deterministically
    (no LLM). Invalid fields → 400 (the slide is left untouched)."""
    from webapp import slide_types
    plan = await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    slide_type = data.get("slide_type")
    norm = slide_types.validate_fields(slide_type, data.get("fields"))
    if norm is None:
        raise HTTPException(400, "invalid slide_type or fields")
    if not 1 <= index <= len(plan.slides):
        raise HTTPException(404, "slide not found")
    plan.slides[index - 1] = plan.slides[index - 1].model_copy(
        update={"slide_type": slide_type, "fields": norm, "filled": False})
    _persist_draft(session_id, plan)
    return JSONResponse({"plan": plan.model_dump(), "errors": []})


@app.delete("/api/drafts/{session_id}/slides/{index}")
async def delete_draft_slide(session_id: str, index: int, request: Request,
                             user=Depends(get_current_user)) -> JSONResponse:
    plan = await _draft_or_404(request, session_id, user, mutate=True)
    try:
        plan = draft.delete_slide(plan, index)
    except IndexError:
        raise HTTPException(404, "slide not found")
    _persist_draft(session_id, plan)
    return JSONResponse(plan.model_dump())


@app.post("/api/drafts/{session_id}/slides/{index}/move")
async def move_draft_slide(session_id: str, index: int, request: Request,
                           user=Depends(get_current_user)) -> JSONResponse:
    plan = await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    try:
        to = int(data["to"])
        plan = draft.reorder(plan, index, to)
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "to (int) required")
    except IndexError:
        raise HTTPException(404, "slide not found")
    _persist_draft(session_id, plan)
    return JSONResponse(plan.model_dump())


@app.post("/api/drafts/{session_id}/rebuild")
async def rebuild_draft(session_id: str, request: Request,
                        user=Depends(get_current_user)) -> JSONResponse:
    """Rebuild a manual/chat draft through the engine (mode=htmlpolish): the same
    assemble → lint → vision-QA → autofix pass an uploaded doc gets. Runs on the
    SAME session via the job runner (progress over /events), overwrites deck.html
    in place, and on success the session becomes a normal built deck (plan.json is
    dropped — it's HTML-as-truth afterwards)."""
    from schemas.session import Mode, SessionInput
    plan = await _draft_or_404(request, session_id, user, mutate=True)
    if not plan.slides:
        raise HTTPException(400, "черновик пуст — добавьте хотя бы один слайд")
    inp = SessionInput(session_id=session_id, user_id=user.id, chat_id=0,
                       progress_message_id=0, mode=Mode.HTMLPOLISH,
                       input_s3_key=str(draft.plan_path(session_id)),
                       source_filename=None)
    try:
        runner.start(inp, user_id=user.id)
    except CapacityError as exc:
        raise HTTPException(429, str(exc))
    return JSONResponse({"session_id": session_id, "kind": "htmlpolish"})


@app.post("/api/drafts/{session_id}/agent")
async def draft_agent(session_id: str, request: Request,
                      user=Depends(get_current_user)) -> JSONResponse:
    """One turn of the slide-building chat agent (owner only). Classifies the
    user's message, mutates the DraftPlan accordingly, re-renders the deck, and
    returns the assistant's reply + whether the deck changed."""
    from webapp import chat_agent
    await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    message = (data.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message required")
    try:
        current_index = int(data.get("current_index") or 1)
    except (TypeError, ValueError):
        current_index = 1
    try:
        res = await run_in_threadpool(
            chat_agent.run_turn, session_id, message, current_index)
    except Exception as exc:  # noqa: BLE001 — logged; neutral message to user
        logger.exception("chat agent run_turn failed (session %s)", session_id)
        raise HTTPException(
            500,
            "Ассистент не смог обработать запрос — попробуйте переформулировать",
        ) from exc
    if res.changed:
        _persist_draft(session_id, draft.load_plan(session_id))
    return JSONResponse(res.model_dump())


@app.post("/api/drafts/{session_id}/build")
async def build_draft(session_id: str, request: Request,
                      user=Depends(get_current_user)) -> JSONResponse:
    """Fill the whole light outline: run every un-filled outline slide through
    the engine's per-slide fill + re-render, synchronously (owner only). The
    build/render happen inside ``chat_agent.build_outline``; we just return the
    now-filled plan."""
    from webapp import chat_agent
    plan = await _draft_or_404(request, session_id, user, mutate=True)
    targets = [s for s in plan.slides if s.brief and not s.filled
               and not s.freeform and not s.slide_type]
    if not targets:
        raise HTTPException(
            400, "в плане пока нет слайдов — добавьте их в чате")
    await run_in_threadpool(chat_agent.build_outline, session_id)
    return JSONResponse(draft.load_plan(session_id).model_dump())


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
        raise HTTPException(
            404, "Сессия не найдена — возможно, удалена по сроку хранения (24 часа)")
    return JSONResponse(st)


@app.get("/api/jobs/{session_id}/events")
async def job_events(session_id: str, request: Request,
                     user=Depends(get_current_user)):
    """Progress stream over SSE (owner only). The gateway proxies HTTP streaming
    (not WebSocket), so progress rides Server-Sent Events. Emits the current
    snapshot, then each queued event until a terminal one closes the stream."""
    import asyncio

    from sse_starlette.sse import EventSourceResponse

    await _owned_or_404(request, session_id, user)
    status = runner.status(session_id)
    queue = runner.queue(session_id)

    async def gen():
        if status is None and queue is None:
            yield {"data": _json.dumps({"stage": "failed", "terminal": True,
                                        "error": "Сессия не найдена — возможно, "
                                                 "удалена по сроку хранения (24 часа)"})}
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
    try:
        html = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "deck HTML must be valid UTF-8")
    async with _deck_lock(session_id):
        try:
            deck_edit.save_deck(session_id, html)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return JSONResponse({"ok": True})


@app.post("/api/jobs/{session_id}/chat")
async def post_chat(session_id: str, request: Request,
                    user=Depends(get_current_user)) -> JSONResponse:
    """Rewrite one slide of the deck per a chat instruction (owner only)."""
    from webapp import chat_edit
    await _owned_or_404(request, session_id, user)
    data = await _json_body(request)
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
    except Exception as exc:  # noqa: BLE001 — logged; neutral message to user
        logger.exception("chat edit failed (session %s)", session_id)
        raise HTTPException(
            500,
            "Не получилось применить правку — попробуйте переформулировать",
        ) from exc
    section = chat_edit.nth_section(new_html, slide_index)
    # The LLM call above takes seconds-to-minutes and runs UNLOCKED; the deck may
    # have been saved meanwhile (another tab, contenteditable autosave). Splice
    # only the rewritten <section> into a FRESH read under the lock, so a
    # concurrent edit of other slides is never clobbered by our stale snapshot.
    async with _deck_lock(session_id):
        fresh = deck.read_text("utf-8")
        if fresh != html and section:
            try:
                new_html = chat_edit._replace_nth_section(  # noqa: SLF001
                    fresh, slide_index, section)
            except ValueError:
                raise HTTPException(
                    409,
                    "презентация изменилась во время правки — повторите запрос")
        deck_edit.save_deck(session_id, new_html)
    # For a draft (DeckPlan-as-truth), persist the rewrite into plan.json as a
    # freeform slide, so a later form edit / re-render doesn't clobber it.
    if draft.plan_path(session_id).is_file():
        if section:
            plan = draft.load_plan(session_id)
            if 1 <= slide_index <= len(plan.slides):
                plan = draft.update_slide(plan, slide_index,
                                          content={"html": section})
                plan.slides[slide_index - 1].freeform = True
                draft.save_plan(session_id, plan)
    return JSONResponse({"ok": True})


# PNG-ZIP and PPTX both screenshot every slide via Chromium — seconds of work.
# Serving that synchronously froze the editor (looked like a hang), so exports are
# async: POST starts the render, GET polls state, GET .../file downloads the result.
# Renderers are resolved at call time (module attribute) so they stay monkeypatchable.
_EXPORT_META = {
    "png": {"out": "deck.zip", "download": "slides.zip",
            "mime": "application/zip",
            "render": lambda deck, out: render_png.export_zip(deck, out)},
    "pptx": {"out": "deck.pptx", "download": "slides.pptx",
             "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
             "render": lambda deck, out: render_pptx.export_pptx(deck, out)},
}


@app.post("/api/jobs/{session_id}/export/{fmt}")
async def start_export(session_id: str, fmt: str, request: Request,
                       user=Depends(get_current_user)) -> JSONResponse:
    await _owned_or_404(request, session_id, user)
    meta = _EXPORT_META.get(fmt)
    if meta is None:
        raise HTTPException(404, "unknown export format")
    deck = deck_edit.ensure_deck(session_id, runner.result_path(session_id))
    if deck is None:
        raise HTTPException(404, "deck not found")
    out = session_dir(session_id) / meta["out"]

    async def worker() -> Path:
        return await run_in_threadpool(meta["render"], deck, out)

    return JSONResponse(exports.registry.start(session_id, fmt, worker))


@app.get("/api/jobs/{session_id}/export/{fmt}")
async def poll_export(session_id: str, fmt: str, request: Request,
                      user=Depends(get_current_user)) -> JSONResponse:
    await _owned_or_404(request, session_id, user)
    if fmt not in _EXPORT_META:
        raise HTTPException(404, "unknown export format")
    return JSONResponse(exports.registry.status(session_id, fmt))


@app.get("/api/jobs/{session_id}/export/{fmt}/file")
async def download_export(session_id: str, fmt: str, request: Request,
                          user=Depends(get_current_user)) -> FileResponse:
    await _owned_or_404(request, session_id, user)
    meta = _EXPORT_META.get(fmt)
    if meta is None:
        raise HTTPException(404, "unknown export format")
    path = exports.registry.path(session_id, fmt)
    if path is None or not path.is_file():
        raise HTTPException(409, "export not ready")
    return FileResponse(path, filename=meta["download"], media_type=meta["mime"])


@app.get("/api/history")
async def get_history(request: Request,
                      user=Depends(get_current_user)) -> JSONResponse:
    async with request.app.state.sessionmaker() as s:
        jobs = await jobs_repo.list_for_user(s, user.id)

    def _draft_title(j) -> str | None:
        """Deck name for constructor/chat drafts (plan.title); None for uploads
        so the client falls back to the source filename. Best-effort I/O."""
        if j.kind != "draft":
            return None
        try:
            return draft.load_plan(j.session_id).title or None
        except Exception:  # noqa: BLE001 — missing/corrupt plan → no name
            return None

    return JSONResponse([
        {"id": j.session_id, "mode": j.mode, "kind": j.kind,
         "source_filename": j.source_filename, "status": j.status,
         "error": j.error,
         "display_name": _draft_title(j),
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
        exports.registry.drop(sid)
        d = session_dir(sid)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    return JSONResponse({"ok": True})
