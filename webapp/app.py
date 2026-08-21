"""FastAPI app: home, job creation, progress WS, result/deck/png/history endpoints."""
from __future__ import annotations

import asyncio
import datetime as _dt
import json as _json
import logging
import os
from contextlib import contextmanager
from functools import partial
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
    render_pptx, stats,
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

    async def _persist_terminal(session_id: str, data: dict) -> None:
        status = data.get("stage", "failed")
        async with app.state.sessionmaker() as s:
            await jobs_repo.mark_terminal(
                s, session_id, status=status,
                result_path=data.get("result_path"), error=data.get("error"),
                # Провод несёт prompt_tokens/completion_tokens → колонки БД
                # in_tokens/out_tokens; cost_rub/duration_ms — как есть.
                in_tokens=data.get("prompt_tokens"),
                out_tokens=data.get("completion_tokens"),
                cost_rub=data.get("cost_rub"),
                duration_ms=data.get("duration_ms"))
            await s.commit()
        # Usage log — best-effort and isolated in its OWN transaction so a logging
        # failure can never roll back / block the terminal status above.
        try:
            from webapp import usage
            async with app.state.sessionmaker() as s:
                await usage.log_render(
                    s, session_id=session_id,
                    owner_user_id=runner.owner(session_id), status=status,
                    workflow=runner.workflow(session_id),
                    started_at=runner.started_at(session_id),
                    result_path=data.get("result_path"),
                    # Разрезы, которые знает только раннер (память процесса) или
                    # только терминальное событие: строка jobs про них не в курсе.
                    section_count=runner.section_count(session_id),
                    truncated=runner.truncated(session_id),
                    error_code=data.get("error_code"))
                await s.commit()
        except Exception:  # noqa: BLE001 — analytics must never affect the build
            pass

    runner.set_terminal_hook(_persist_terminal)

    # In-memory queue is gone after a restart → fail orphaned non-terminal rows.
    from webapp import retention
    await retention.reconcile_interrupted(app.state.sessionmaker)
    app.state._retention_task = asyncio.create_task(
        retention.retention_loop(
            app.state.sessionmaker,
            ttl_hours=settings.retention_hours,
            archive_dir=settings.archive_root(),
            archive_ttl_days=settings.archive_ttl_days,
            archive_max_bytes=settings.archive_max_mb * 1024 * 1024),
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
        raise HTTPException(404, "не найдено")
    return job


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return _serve_shell("index.html",
                        email=request.headers.get("X-User-Email", ""))


@app.get("/editor", response_class=HTMLResponse)
def editor(request: Request) -> HTMLResponse:
    return _serve_shell("editor.html",
                        email=request.headers.get("X-User-Email", ""))


# Раздача крупных файлов вне репозитория (см. settings.downloads_dir). Явный
# allowlist вместо {name}-подстановки: никакого обхода путей и никакой раздачи
# случайно оказавшегося в каталоге файла. Auth как у всех — заголовки шлюза.
_DOWNLOADS = {"cloud-ru-slides.skill": "application/zip"}


@app.get("/downloads/{name}")
async def download_file(name: str,
                        user=Depends(get_current_user)) -> FileResponse:
    mime = _DOWNLOADS.get(name)
    if mime is None:
        raise HTTPException(404, "не найдено")
    path = _settings.downloads_dir / name
    if not path.is_file():
        raise HTTPException(404, "file not provisioned")
    return FileResponse(path, filename=name, media_type=mime)


@app.post("/api/jobs")
async def create_job(request: Request, mode: str = Form(...),
                     exact_transfer: str = Form(default="false"),
                     file: UploadFile = File(...),
                     user=Depends(get_current_user)) -> JSONResponse:
    from schemas.session import Mode, SessionInput
    if mode not in _ALLOWED:
        raise HTTPException(400, f"неизвестный режим сборки: {mode}")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED[mode]:
        allowed = ", ".join(sorted(_ALLOWED[mode]))
        raise HTTPException(
            400, f"формат {suffix or 'без расширения'} не поддерживается — "
                 f"загрузите {allowed}")

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
    # so only the plain-text formats are whitespace-checked. Decode with the same
    # encoding guess the build uses — plain utf-8 would drop a cp1251/utf-16 doc's
    # Cyrillic and falsely flag a real file as empty.
    from htmlslides.parsers.base import decode_smart as _decode_smart
    if not raw.strip() or (suffix in (".md", ".txt") and not _decode_smart(raw).strip()):
        raise HTTPException(400, "файл пустой — загрузите документ с содержимым")

    inp = SessionInput(user_id=user.id, chat_id=0, progress_message_id=0,
                       mode=Mode(mode), input_s3_key=None,
                       source_filename=file.filename,
                       exact_transfer=exact)
    dest = session_dir(inp.session_id) / f"input{suffix}"
    dest.write_bytes(raw)
    inp = inp.model_copy(update={"input_s3_key": str(dest)})

    return await _start_build(request, inp, user, source=dest)


async def _start_build(request: Request, inp, user, *,
                       source: Path) -> JSONResponse:
    """Общий хвост постановки сборки в очередь: оценка объёма, строка владения,
    старт раннера, единая форма ответа.

    Один код на загрузку и на перезапуск — чтобы перезапуск не стал дырой в
    лимитах очереди и не разошёлся с загрузкой по форме ответа."""
    # Оценка размера для фронта: считаем разделы тем же парсером, что и сборка
    # (двойной парсинг — миллисекунды на фоне LLM-этапов). Ошибка парсинга НЕ
    # блокирует запуск — реальную причину покажет воркер со своей диагностикой.
    # Модуль импортируем целиком и зовём атрибут, чтобы monkeypatch в тестах
    # (htmlslides.parsers.base.parse_file) действовал и здесь.
    try:
        from htmlslides.parsers import base as _parsers_base
        doc = await run_in_threadpool(_parsers_base.parse_file, source)
        section_count: int | None = len(doc.sections)
    except Exception:  # noqa: BLE001
        section_count = None

    kind = "html"
    # Persist ownership BEFORE starting so even a fast terminal can update it.
    async with request.app.state.sessionmaker() as s:
        await jobs_repo.create(s, session_id=inp.session_id, user_id=user.id,
                               mode=inp.mode.value, kind=kind,
                               source_filename=inp.source_filename,
                               exact_transfer=inp.exact_transfer)
        await s.commit()
    try:
        runner.start(inp, user_id=user.id, section_count=section_count)
    except CapacityError as exc:
        raise HTTPException(429, str(exc))
    return JSONResponse({"session_id": inp.session_id, "kind": kind,
                         "section_count": section_count})


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
        raise HTTPException(400, f"неизвестный режим черновика: {mode}")
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
    """Slide-template catalog with slot contracts (drives the manual builder).

    Скрытые макеты приходят помеченными ``hidden``: пикер их отфильтровывает, а
    формы и имена слайдов, которые поставил конвейер, строятся по ним же."""
    from webapp import templates_api
    return JSONResponse(templates_api.catalog(include_hidden=True))


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


@app.get("/api/diagrams/catalog")
def diagrams_catalog(user=Depends(get_current_user)) -> JSONResponse:
    """Каталог типов диаграмм для «большого блока выбора» в редакторе:
    все карточки, будущие волны — с available=false («скоро»)."""
    from webapp import diagrams_api
    return JSONResponse(diagrams_api.catalog())


@app.get("/api/diagrams/{kind}/preview", response_class=HTMLResponse)
def diagram_kind_preview(kind: str, static: bool = False,
                         user=Depends(get_current_user)):
    """Однослайдовая дека-превью конкретного ТИПА диаграммы (пикер типов).
    Тот же движок, что и боевой рендер, — превью совпадает с результатом 1:1."""
    from htmlslides.assembler import assemble
    from htmlslides.models import DeckPlan, SlidePlan
    from webapp import diagrams_api
    try:
        content = diagrams_api.preview_content(kind)
    except KeyError:
        raise HTTPException(404, "неизвестный или недоступный тип схемы")
    plan = DeckPlan(title="", slides=[SlidePlan(
        index=1, type="content", template_id="diagram", content=content)])
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
        raise HTTPException(400, "тело запроса — не корректный JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "ожидался JSON-объект")
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
        # 1-2 (аудит раунда 2, 2026-08-15): «plan.json нет» ≠ «собран движком».
        # После чистки/ретеншена каталога сессии нет ни плана, ни деки — прежний
        # 409 отправлял «править деку в редакторе», которого не существует.
        # Критерий деки — тот же, что у GET deck (ensure_deck), чтобы ответы
        # двух эндпоинтов не противоречили друг другу.
        if deck_edit.ensure_deck(session_id,
                                 runner.result_path(session_id)) is None:
            raise HTTPException(
                404, "черновик не найден — файлы сессии удалены "
                     "(мы храним их 24 часа)")
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
    """Delete a draft session (owner only). Removes an editable draft — status
    "draft", or a draft-kind build that ended in failed/cancelled (the card shows
    «Удалить» for those too). A real UPLOAD build must go through history/clear,
    and an in-flight run is never deletable here (would orphan the active build).
    Cleanup mirrors clear_history: drop the Job row, export state, session dir."""
    import shutil
    async with request.app.state.sessionmaker() as s:
        job = await jobs_repo.get_owned(s, session_id, user.id)
        deletable = job is not None and (
            job.status == "draft"
            or (job.kind == "draft" and job.status in ("failed", "cancelled")))
        if not deletable:
            raise HTTPException(404, "не найдено")
        await s.delete(job)
        await s.commit()
    exports.registry.drop(session_id)
    d = session_dir(session_id)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)
    return JSONResponse({"ok": True})


def _to_freeform(plan: draft.DraftPlan, index: int,
                 section: str) -> draft.DraftPlan:
    """Перевести слайд в свободный режим, запомнив макет, из которого ушли.

    Запоминаем один раз — при ПЕРВОМ уходе с макета: дальше слайд правится уже
    как свободный, и перезапись стёрла бы то единственное состояние, куда
    осмысленно возвращаться."""
    was = plan.slides[index - 1]
    plan = draft.update_slide(plan, index, content={"html": section})
    s = plan.slides[index - 1]
    s.freeform = True
    if not was.freeform:
        s.prev_layout = {"template_id": was.template_id, "content": was.content}
    return plan


def _persist_draft(session_id: str, plan: draft.DraftPlan) -> None:
    """Save the plan and re-render the derived deck.html from it.

    Сначала собираем HTML и только потом пишем оба файла. Порядок «сохранить
    план, затем отрисовать» разводил plan.json и deck.html навсегда: сборка
    падала (битый шаблон, неожиданный контент) — план уже уехал вперёд, ответ
    500, а редактор дальше показывал СТАРУЮ деку, и при перезагрузке тоже.
    Человек видел, что слайд «прыгнул обратно», тянул его второй раз — и
    переставлял дважды. Дека — дериват плана, расходиться они не должны."""
    html = draft_render.build_draft_html(plan)
    draft.save_plan(session_id, plan)
    deck_edit.save_deck(session_id, html)


@contextmanager
def _plan_txn(session_id: str):
    """Замок плана на всё read-modify-write редакторской мутации.

    Фоновая досборка (glass) вклеивает результат шага в план атомарно под
    glass._plan_lock, а редакторские мутации ходили без замка: между их load
    и persist сплайс шага успевал закоммитить заполненный слайд — и persist
    устаревшего снимка молча откатывал его в «сырое» состояние (после done —
    навсегда, конвейер уже не перезаполнит). Поэтому каждая мутация читает
    СВЕЖИЙ план под тем же замком и пишет, не отпуская его."""
    from webapp import glass
    with glass._plan_lock(session_id):  # noqa: SLF001 — общий замок плана
        yield draft.load_plan(session_id)


@app.put("/api/drafts/{session_id}")
async def replace_draft(session_id: str, request: Request,
                        user=Depends(get_current_user)) -> JSONResponse:
    """Replace the whole draft plan in one call. The editor's undo/redo restores
    a previously-captured snapshot this way; the derived deck re-renders from it.
    Same ownership / lifecycle guards as the slide endpoints."""
    from pydantic import ValidationError
    from htmlslides.library import SlotValidationError, TemplateLibrary
    from webapp import slide_types
    await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    try:
        plan = draft.DraftPlan.model_validate(data)
    except ValidationError:
        raise HTTPException(400, "план не проходит по форме — обновите страницу и попробуйте ещё раз")
    # Аудит 2026-08-14 (B-1/B-3): форма плана — не вся правда. Неизвестный
    # template_id раньше доезжал до рендера и падал голым 500 (SlotValidationError
    # не ловился), а битые typed-поля сохранялись молча и рендерились в blank —
    # хотя per-slide эндпоинт /fields тот же спек честно отвергает. Контракт один.
    library = TemplateLibrary.load()
    for i, s in enumerate(plan.slides, start=1):
        if s.freeform:
            continue
        if s.slide_type:
            norm, claims = slide_types.validate_fields_verbose(s.slide_type,
                                                               s.fields)
            if norm is None:
                raise HTTPException(400, {
                    "error": f"слайд {i}: поля не проходят контракт типа",
                    "errors": claims})
            continue
        tid = s.template_id or "blank"
        try:
            library.get(tid)
        except SlotValidationError:
            raise HTTPException(400, f"слайд {i}: неизвестный макет «{tid}»")
    try:
        with _plan_txn(session_id):
            _persist_draft(session_id, plan)
    except SlotValidationError as exc:
        # Страховка: что бы рендер ни отверг сверх пред-проверки — это претензия
        # к плану, а не сбой сервера (план на диске цел: рендер идёт до записи).
        raise HTTPException(400, str(exc))
    return JSONResponse(plan.model_dump())


@app.post("/api/drafts/{session_id}/slides")
async def add_draft_slide(session_id: str, request: Request,
                          user=Depends(get_current_user)) -> JSONResponse:
    from webapp import templates_api
    await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    template_id = data.get("template_id")
    # B-11: скрытый макет (есть в библиотеке, но не в пикере) и неизвестный id —
    # разные ошибки; «valid template_id required» врал, что cards-6 невалиден.
    visible = {t["id"] for t in templates_api.catalog()}
    if not template_id:
        raise HTTPException(400, "не указан макет — выберите его из каталога")
    if template_id not in visible:
        all_ids = {t["id"] for t in templates_api.catalog(include_hidden=True)}
        if template_id in all_ids:
            raise HTTPException(
                400, f"макет «{template_id}» служебный — вручную его добавить "
                     "нельзя, выберите макет из каталога")
        raise HTTPException(400, f"неизвестный макет «{template_id}»")
    at = data.get("at")
    # Fields start EMPTY (plan.json keeps the user's raw content); the representative
    # filler ("рыба-текст") is supplied at render time by draft_render, so a fresh
    # master shows example text in the slide while its input stays empty. A layout
    # swap still carries over the caller's overlapping slots.
    with _plan_txn(session_id) as plan:
        plan = draft.add_slide(plan, draft.DraftSlide(
            template_id=template_id, content=data.get("content") or {}), at=at)
        _persist_draft(session_id, plan)
    return JSONResponse(plan.model_dump())


@app.put("/api/drafts/{session_id}/slides/{index}")
async def update_draft_slide(session_id: str, index: int, request: Request,
                             user=Depends(get_current_user)) -> JSONResponse:
    await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    content = data.get("content")
    if not isinstance(content, dict):
        raise HTTPException(400, "не передано содержимое слайда (content)")
    with _plan_txn(session_id) as plan:
        try:
            target = plan.slides[index - 1]
            plan = draft.update_slide(plan, index, content=content)
        except IndexError:
            raise HTTPException(404, "такого слайда нет")
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
    await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    html = (data.get("html") or "").strip()
    if not html:
        raise HTTPException(400, "не передан HTML слайда")
    section = chat_edit.nth_section(html, 1) or html  # accept a bare <section>
    with _plan_txn(session_id) as plan:
        if not (1 <= index <= len(plan.slides)):
            raise HTTPException(404, "такого слайда нет")
        plan = _to_freeform(plan, index, section)
        _persist_draft(session_id, plan)
    return JSONResponse(plan.model_dump())


@app.put("/api/drafts/{session_id}/slides/{index}/fields")
async def update_draft_slide_fields(session_id: str, index: int, request: Request,
                                    user=Depends(get_current_user)) -> JSONResponse:
    """Set a slide's typed structured content (slide_type + fields). Validated
    against the type contract; on success the slide renders deterministically
    (no LLM). Invalid fields → 400 (the slide is left untouched), with the list
    of readable claims in the body: this endpoint answers a человек, and a bare
    400 left the diagram panel with a silent «error» — правку отвергли, а что
    именно сломано (узел без связей, одна дорожка вместо двух) не сказали."""
    from webapp import slide_types
    await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    slide_type = data.get("slide_type")
    norm, claims = slide_types.validate_fields_verbose(slide_type,
                                                       data.get("fields"))
    if norm is None:
        raise HTTPException(400, {"error": "поля не проходят контракт типа",
                                  "errors": claims})
    with _plan_txn(session_id) as plan:
        if not 1 <= index <= len(plan.slides):
            raise HTTPException(404, "такого слайда нет")
        plan.slides[index - 1] = plan.slides[index - 1].model_copy(
            update={"slide_type": slide_type, "fields": norm, "filled": False})
        _persist_draft(session_id, plan)
    return JSONResponse({"plan": plan.model_dump(), "errors": []})


@app.delete("/api/drafts/{session_id}/slides/{index}")
async def delete_draft_slide(session_id: str, index: int, request: Request,
                             user=Depends(get_current_user)) -> JSONResponse:
    await _draft_or_404(request, session_id, user, mutate=True)
    with _plan_txn(session_id) as plan:
        try:
            plan = draft.delete_slide(plan, index)
        except IndexError:
            raise HTTPException(404, "такого слайда нет")
        _persist_draft(session_id, plan)
    return JSONResponse(plan.model_dump())


@app.post("/api/drafts/{session_id}/slides/{index}/move")
async def move_draft_slide(session_id: str, index: int, request: Request,
                           user=Depends(get_current_user)) -> JSONResponse:
    await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    try:
        to = int(data["to"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "не передана позиция назначения (to)")
    with _plan_txn(session_id) as plan:
        try:
            plan = draft.reorder(plan, index, to)
        except IndexError:
            raise HTTPException(404, "такого слайда нет")
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


@app.post("/api/drafts/{session_id}/slides/{index}/improve")
async def improve_draft_slide(session_id: str, index: int, request: Request,
                              user=Depends(get_current_user)) -> JSONResponse:
    """Точечное «Улучшить этот слайд»: QA + автофикс одного слайда (~1-2 мин).
    409 — пока сборка не завершена (кнопка в UI в это время выключена)."""
    from webapp import glass
    await _draft_or_404(request, session_id, user, mutate=True)
    try:
        out = await run_in_threadpool(glass.improve_slide, session_id, index)
    except glass.NotReady as exc:
        raise HTTPException(409, str(exc))
    except (IndexError, glass.NoContext) as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 — logged; нейтральная русская ошибка
        logger.exception("improve slide failed (session %s, slide %s)",
                         session_id, index)
        raise HTTPException(500, "не удалось улучшить слайд — "
                                 "попробуйте ещё раз") from exc
    return JSONResponse(out)


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
        raise HTTPException(400, "сообщение пустое — напишите, что сделать со слайдами")
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
        with _plan_txn(session_id) as plan:
            _persist_draft(session_id, plan)
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
        # B-4: текст уезжает в чат как есть — различаем «план пуст» и «все
        # слайды уже разложены/заполнены», иначе 400 врёт про пустой план.
        if plan.slides:
            raise HTTPException(
                400, "все слайды уже разложены или заполнены — "
                     "собирать нечего")
        raise HTTPException(
            400, "в плане пока нет слайдов — добавьте их в чате")
    await run_in_threadpool(chat_agent.build_outline, session_id)
    return JSONResponse(draft.load_plan(session_id).model_dump())


# ── «Стеклянная сборка» — прозрачный конвейер черновика (webapp/glass.py).
# Клиент-управляемый степпинг вместо раннера: каждый /glass/step заполняет ОДИН
# слайд (~15-60с, как /agent), лента растёт без SSE; сомнительные слайды ждут
# /glass/answer, не блокируя остальные шаги.

@app.post("/api/drafts/{session_id}/glass/start")
async def glass_start(session_id: str, request: Request,
                      file: UploadFile = File(...),
                      user=Depends(get_current_user)) -> JSONResponse:
    """Документ → прозрачный аутлайн (обложка + слайд на раздел с кандидатами
    макета и вопросами ИИ). Быстрый: parse + лёгкий скоринг, без заполнения."""
    from webapp import glass
    plan = await _draft_or_404(request, session_id, user, mutate=True)
    if plan.slides:
        raise HTTPException(409, "черновик не пуст — стеклянная сборка "
                                 "начинается с чистой сессии")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED["htmlnew"]:
        allowed = ", ".join(sorted(_ALLOWED["htmlnew"]))
        raise HTTPException(
            400, f"формат {suffix or 'без расширения'} не поддерживается — "
                 f"загрузите {allowed}")
    # Стеклянная сборка идёт мимо очереди раннера, а с ней и мимо его лимита
    # MAX_PER_USER. Свой потолок считаем по недозаполненным аутлайнам автора:
    # именно они держат вызовы модели и место на диске.
    async with request.app.state.sessionmaker() as s:
        # B-8: считаем по ВСЕМ черновикам — срез «MAX+1 новейших» позволял
        # обойти потолок, вытеснив незавершённые из окна свежими пустыми.
        drafts = await jobs_repo.list_drafts_for_user(s, user.id)
    busy = await run_in_threadpool(
        glass.unfinished_outlines,
        [j.session_id for j in drafts if j.session_id != session_id])
    if busy >= glass.MAX_ACTIVE_PER_USER:
        raise HTTPException(429, f"у вас {busy} незавершённых сборок — "
                                 "дособерите или удалите их, прежде чем "
                                 "начинать новую")
    raw = await file.read()
    from htmlslides.parsers.base import decode_smart as _decode_smart
    if not raw.strip() or (suffix in (".md", ".txt")
                           and not _decode_smart(raw).strip()):
        raise HTTPException(400, "файл пустой — загрузите документ с содержимым")
    dest = session_dir(session_id) / f"input{suffix}"
    dest.write_bytes(raw)
    try:
        # Имя файла автора передаём отдельно: dest — это всегда `input.<ext>`,
        # и у документа без титульного текста обложка выходила с «INPUT».
        out = await run_in_threadpool(partial(glass.start_glass, session_id, dest,
                                              origin_name=file.filename))
    except Exception as exc:  # noqa: BLE001 — parse/скоринг: причина пользователю
        logger.exception("glass start failed (session %s)", session_id)
        raise HTTPException(500, "не удалось разобрать документ — попробуйте "
                                 "другой файл") from exc
    return JSONResponse(out.model_dump())


@app.post("/api/drafts/{session_id}/glass/rest")
async def glass_rest(session_id: str, request: Request,
                     user=Depends(get_current_user)) -> JSONResponse:
    """Хвост длинного документа → вторая дека (новая сессия с тем же исходником).

    Потолок в 40 разделов остаётся: сорок слайдов автор и так проходит по
    одному больше получаса. Обратимой делаем потерю — не подъёмом потолка, а
    кнопкой, которая заводит продолжение ровно с первого невзятого раздела."""
    from uuid import uuid4

    from webapp import glass
    plan = await _draft_or_404(request, session_id, user)
    if not plan.rest_from:
        raise HTTPException(400, "все разделы документа уже разобраны")
    # Тот же потолок незавершённых сборок, что и у /glass/start: продолжение —
    # такая же ручная сборка и так же держит вызовы модели и место на диске.
    async with request.app.state.sessionmaker() as s:
        # B-8: считаем по ВСЕМ черновикам — срез «MAX+1 новейших» позволял
        # обойти потолок, вытеснив незавершённые из окна свежими пустыми.
        drafts = await jobs_repo.list_drafts_for_user(s, user.id)
    busy = await run_in_threadpool(
        glass.unfinished_outlines,
        [j.session_id for j in drafts if j.session_id != session_id])
    if busy >= glass.MAX_ACTIVE_PER_USER:
        raise HTTPException(429, f"у вас {busy} незавершённых сборок — "
                                 "дособерите или удалите их, прежде чем "
                                 "начинать новую")
    new_id = uuid4().hex[:16]
    async with request.app.state.sessionmaker() as s:
        await jobs_repo.create(s, session_id=new_id, user_id=user.id,
                               mode="manual", kind="draft",
                               source_filename=None, status="draft")
        await s.commit()
    try:
        out = await run_in_threadpool(
            partial(glass.continue_glass, session_id, new_id))
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:  # noqa: BLE001 — parse/скоринг: причина пользователю
        logger.exception("glass rest failed (session %s)", session_id)
        raise HTTPException(500, "не удалось собрать оставшиеся разделы") from exc
    return JSONResponse({"session_id": new_id, "slides": len(out.slides) - 1})


@app.post("/api/drafts/{session_id}/glass/step")
async def glass_step(session_id: str, request: Request,
                     user=Depends(get_current_user)) -> JSONResponse:
    """Один шаг конвейера: заполнить готовый слайд (или разметить следующий);
    вопрос (needs_input) сборку не останавливает — слайд откладывается."""
    from webapp import glass
    await _draft_or_404(request, session_id, user, mutate=True)
    return JSONResponse(await run_in_threadpool(glass.step_fill, session_id))


@app.post("/api/drafts/{session_id}/glass/score")
async def glass_score(session_id: str, request: Request,
                      user=Depends(get_current_user)) -> JSONResponse:
    """Разметить один неразмеченный слайд (кандидаты макета/вопрос) — клиентский
    «разведчик» зовёт это параллельно шагам заполнения."""
    from webapp import glass
    await _draft_or_404(request, session_id, user, mutate=True)
    return JSONResponse(await run_in_threadpool(glass.score_next, session_id))


@app.post("/api/drafts/{session_id}/glass/stop")
async def glass_stop(session_id: str, request: Request,
                     user=Depends(get_current_user)) -> JSONResponse:
    """«Остановить сборку»: paused в плане. Новые шаги (step/score) с этого
    момента отвечают action=None и модель не трогают; уже улетевший в модель
    шаг доработает и доклеит результат — он оплачен."""
    from webapp import glass
    await _draft_or_404(request, session_id, user, mutate=True)
    plan = await run_in_threadpool(glass.set_paused, session_id, True)
    return JSONResponse({"paused": plan.paused})


@app.post("/api/drafts/{session_id}/glass/resume")
async def glass_resume(session_id: str, request: Request,
                       user=Depends(get_current_user)) -> JSONResponse:
    """«Продолжить сборку»: снять paused — клиент заново запускает цикл шагов."""
    from webapp import glass
    await _draft_or_404(request, session_id, user, mutate=True)
    plan = await run_in_threadpool(glass.set_paused, session_id, False)
    return JSONResponse({"paused": plan.paused})


@app.post("/api/drafts/{session_id}/glass/answer")
async def glass_answer(session_id: str, request: Request,
                       user=Depends(get_current_user)) -> JSONResponse:
    """Ответ на вопрос ИИ (в любой момент): чип-кандидат и/или текст уточнения.
    Мгновенный: записывает выбор в план, слайд заполняет конвейер шагов."""
    from htmlslides.library import SlotValidationError
    from webapp import glass
    await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    try:
        index = int(data.get("index") or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, "не передан номер слайда (index)")
    template_id = (data.get("template_id") or "").strip() or None
    # Тип схемы из мастера «Схема» — данными, а не текстом: см. glass.answer.
    kind = (data.get("kind") or "").strip() or None
    message = str(data.get("message") or "")
    try:
        out = await run_in_threadpool(
            lambda: glass.answer(session_id, index, template_id=template_id,
                                 kind=kind, message=message))
    except IndexError as exc:
        raise HTTPException(400, str(exc))
    except KeyError as exc:
        raise HTTPException(400, str(exc))
    except glass.AnswerNotExpected:
        # Вкладка отстала: вопрос уже снят (отвечено с другой вкладки или слайд
        # заполнился сам). 409, а не 400 — запрос корректный, изменилось состояние.
        raise HTTPException(409, "вопрос уже закрыт — обновите страницу")
    except SlotValidationError:
        raise HTTPException(400, f"неизвестный макет: {template_id}")
    return JSONResponse(out)


@app.post("/api/drafts/{session_id}/slides/{index}/refill")
async def draft_slide_refill(session_id: str, index: int, request: Request,
                             user=Depends(get_current_user)) -> JSONResponse:
    """Сменить макет слайда и заново заполнить его ИИ по исходному фрагменту.

    Механическая смена макета в редакторе переносит текст слот-в-слот; здесь
    содержимое пишется заново под возможности нового макета."""
    from htmlslides.library import SlotValidationError
    from webapp import glass
    await _draft_or_404(request, session_id, user, mutate=True)
    data = await _json_body(request)
    template_id = (data.get("template_id") or "").strip() or None
    kind = (data.get("kind") or "").strip() or None
    try:
        out = await run_in_threadpool(
            lambda: glass.refill_slide(session_id, index,
                                       template_id=template_id, kind=kind))
    except IndexError as exc:
        raise HTTPException(400, str(exc))
    except KeyError as exc:
        raise HTTPException(400, str(exc))
    except glass.NoContext:
        raise HTTPException(400, "на слайде нет текста — заполнять нечем: "
                                 "смените макет с переносом текста")
    except SlotValidationError:
        raise HTTPException(400, f"неизвестный макет: {template_id}")
    return JSONResponse(out)


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


# Перезапустить можно только то, что не доехало. У done есть «Открыть», и «собрать
# ещё раз то же самое» — другая фича с другим смыслом.
_RETRYABLE = {"failed", "cancelled"}


@app.post("/api/jobs/{session_id}/retry")
async def retry_job(session_id: str, request: Request,
                    user=Depends(get_current_user)) -> JSONResponse:
    """Перезапустить неудавшуюся сборку из сохранённого исходника (владелец).

    Раньше «Повторить» жила целиком в браузере: скролл к дропзоне + повтор, если
    файл ещё выбран в <input>. После F5 (а перезагрузка — обычное дело, когда
    сборка упала) выбор пропадал, и кнопка превращалась в «прокрутить страницу».

    Работает ровно в окне ретеншена — пока жив исходник сессии. Хранить его
    дольше ради этой кнопки нельзя (это обещание о клиентских данных), поэтому
    когда файла нет, отвечаем 410 с объяснением, а не молча ничего не делаем."""
    job = await _owned_or_404(request, session_id, user)
    if job.kind == "draft":
        # У черновика своя кнопка «Продолжить» — он правится, а не пересобирается.
        raise HTTPException(400, "черновик не перезапускается — откройте его в "
                                 "редакторе")
    if job.status not in _RETRYABLE:
        raise HTTPException(
            409, "перезапустить можно только неудавшуюся или остановленную сборку")
    source = next(iter(sorted(session_dir(session_id).glob("input.*"))), None)
    if source is None:
        raise HTTPException(
            410, f"Исходник больше не хранится — мы удаляем файлы через "
                 f"{int(_settings.retention_hours)} часа. Загрузите файл заново.")

    from schemas.session import Mode, SessionInput
    inp = SessionInput(user_id=user.id, chat_id=0, progress_message_id=0,
                       mode=Mode(job.mode), input_s3_key=None,
                       source_filename=job.source_filename,
                       # NULL у строк, созданных до появления колонки: тогда флага
                       # не существовало, все сборки были обычными.
                       exact_transfer=bool(job.exact_transfer))
    dest = session_dir(inp.session_id) / source.name
    dest.write_bytes(source.read_bytes())
    inp = inp.model_copy(update={"input_s3_key": str(dest)})
    return await _start_build(request, inp, user, source=dest)


@app.get("/api/models/health")
async def models_health(user=Depends(get_current_user)) -> JSONResponse:
    """Доступность сервиса ИИ для плашки на главной — роли, а не имена моделей.

    Кэш с TTL живёт в model_health; ответ мгновенный (stale-while-revalidate),
    протухшие данные обновляются фоном."""
    from webapp import model_health
    return JSONResponse(model_health.snapshot())


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
    # Своя очередь на каждый стрим: вторая вкладка на той же сборке не должна
    # разбирать события у первой (см. JobRunner.subscribe).
    queue = runner.subscribe(session_id)

    async def gen():
        try:
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
        finally:
            # Отписка обязательна: иначе очередь мёртвой вкладки копит события
            # сессии до самого ретеншена.
            if queue is not None:
                runner.unsubscribe(session_id, queue)

    return EventSourceResponse(gen())


@app.get("/api/jobs/{session_id}/deck", response_class=HTMLResponse)
async def get_deck(session_id: str, request: Request, download: int = 0,
                   slide: int = 0, user=Depends(get_current_user)):
    await _owned_or_404(request, session_id, user)
    path = deck_edit.ensure_deck(session_id, runner.result_path(session_id))
    if path is None:
        raise HTTPException(404, "дека не найдена")
    if download:
        return FileResponse(path, filename="deck.html", media_type="text/html")
    html = path.read_text("utf-8")
    if slide:
        # Лёгкие миниатюры редактора: документ с одним слайдом вместо всей деки.
        one = deck_edit.extract_slide(html, slide)
        if one is None:
            raise HTTPException(404, f"слайда {slide} в деке нет")
        return HTMLResponse(one)
    return HTMLResponse(html)


@app.post("/api/jobs/{session_id}/deck")
async def post_deck(session_id: str, request: Request,
                    user=Depends(get_current_user)) -> JSONResponse:
    await _owned_or_404(request, session_id, user)
    # 1-3 (аудит раунда 2, 2026-08-15): у черновика правда — plan.json, а его
    # deck.html — производная. Принятый сюда HTML молча откатывался первым же
    # пере-рендером формы (DeckPlan-as-truth в обход). Честный отказ вместо
    # тихой потери; правки черновика сохраняются per-slide эндпоинтами.
    if draft.plan_path(session_id).is_file():
        raise HTTPException(
            409, "это черновик — правки сохраняются по слайдам, "
                 "целиком деку сохранять не нужно")
    body = await request.body()
    try:
        html = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "HTML деки должен быть в кодировке UTF-8")
    async with _deck_lock(session_id):
        try:
            deck_edit.save_deck(session_id, html)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return JSONResponse({"ok": True})


@app.post("/api/jobs/{session_id}/theme")
async def post_theme(session_id: str, request: Request,
                     user=Depends(get_current_user)) -> JSONResponse:
    """Repaint the saved deck (dark|light). Free: every component color in
    deck.css resolves from tokens keyed by <html data-theme>, so this is one
    attribute flip in deck.html — no engine/LLM pass; PNG/PPTX exports re-render
    from deck.html on demand and pick the theme up automatically."""
    await _owned_or_404(request, session_id, user)
    data = await _json_body(request)
    theme = data.get("theme")
    if theme not in deck_edit.THEMES:
        raise HTTPException(400, "тема должна быть 'dark' или 'light'")
    async with _deck_lock(session_id):
        deck = deck_edit.ensure_deck(session_id, runner.result_path(session_id))
        if deck is None:
            raise HTTPException(404, "дека не найдена")
        html = deck.read_text("utf-8")
        deck.write_text(deck_edit.set_theme(html, theme), encoding="utf-8")
        # Drafts re-derive deck.html from plan.json on every form edit, which
        # would silently revert the flip — persist the choice in the plan too.
        if draft.plan_path(session_id).is_file():
            with _plan_txn(session_id) as plan:
                draft.save_plan(session_id,
                                plan.model_copy(update={"theme": theme}))
    # Freeform slides (chat-edit/autofix) may carry literal colors authored
    # against the old background — surface them so the UI can suggest a check.
    check = deck_edit.slides_with_hardcoded_colors(html)
    return JSONResponse({"ok": True, "theme": theme, "check_slides": check})


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
        raise HTTPException(400, "не передан номер слайда (slide_index)")
    instruction = (data.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(400, "инструкция пустая — напишите, что поменять")
    deck = deck_edit.ensure_deck(session_id, runner.result_path(session_id))
    if deck is None:
        raise HTTPException(404, "дека не найдена")
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
            with _plan_txn(session_id) as plan:
                if 1 <= slide_index <= len(plan.slides):
                    plan = _to_freeform(plan, slide_index, section)
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
    job = await _owned_or_404(request, session_id, user)
    meta = _EXPORT_META.get(fmt)
    if meta is None:
        raise HTTPException(404, "неизвестный формат экспорта")
    # B-12: у пустого черновика deck.html — empty-state-заглушка «Добавьте
    # слайд…», и экспорт честно доводил её до «готового» PPTX. rebuild/build
    # на пустом плане отвечают 400 — экспорт обязан вести себя так же.
    if job.status == "draft" and not draft.load_plan(session_id).slides:
        raise HTTPException(400, "в черновике нет слайдов — нечего "
                                 "экспортировать")
    deck = deck_edit.ensure_deck(session_id, runner.result_path(session_id))
    if deck is None:
        raise HTTPException(404, "дека не найдена")
    out = session_dir(session_id) / meta["out"]

    async def worker() -> Path:
        return await run_in_threadpool(meta["render"], deck, out)

    return JSONResponse(exports.registry.start(session_id, fmt, worker))


@app.get("/api/jobs/{session_id}/export/{fmt}")
async def poll_export(session_id: str, fmt: str, request: Request,
                      user=Depends(get_current_user)) -> JSONResponse:
    await _owned_or_404(request, session_id, user)
    if fmt not in _EXPORT_META:
        raise HTTPException(404, "неизвестный формат экспорта")
    return JSONResponse(exports.registry.status(session_id, fmt))


@app.get("/api/jobs/{session_id}/export/{fmt}/file")
async def download_export(session_id: str, fmt: str, request: Request,
                          user=Depends(get_current_user)) -> FileResponse:
    await _owned_or_404(request, session_id, user)
    meta = _EXPORT_META.get(fmt)
    if meta is None:
        raise HTTPException(404, "неизвестный формат экспорта")
    path = exports.registry.path(session_id, fmt)
    if path is None or not path.is_file():
        raise HTTPException(409, "экспорт ещё не готов")
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
         "created_at": j.created_at.isoformat() if j.created_at else None,
         # Расход прогона для строки статистики (None у старых строк и исходов
         # без LLM — фронт просто не рисует соответствующие части).
         "in_tokens": j.in_tokens, "out_tokens": j.out_tokens,
         "cost_rub": j.cost_rub, "duration_ms": j.duration_ms}
        for j in jobs
    ])


@app.get("/internal/stats")
async def internal_stats(request: Request, since: str | None = None,
                         until: str | None = None,
                         limit: int = 8) -> JSONResponse:
    """Метрики /slides для блока «Слайды» в админке платформы.

    БЕЗ get_current_user: шлюз ходит машинным запросом, X-User-Id у него нет.
    Граница — общий с платформой секрет в X-Ingest-Token: loopback на общей VM
    границей не является (соседние приложения ходят на 127.0.0.1:8012 так же
    свободно), а метрики несут email'ы. Пустой секрет = эндпоинт выключен (404).
    """
    import secrets
    token = _settings.stats_token
    if not token:
        raise HTTPException(status_code=404)
    if not secrets.compare_digest(request.headers.get("X-Ingest-Token", ""),
                                  token):
        raise HTTPException(status_code=401, detail="invalid token")

    def _ts(name: str, raw: str | None):
        if not raw:
            return None
        try:
            return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            # Молча игнорировать кривой параметр нельзя: шлюз показал бы данные
            # за другой период, не заметив этого.
            raise HTTPException(status_code=400,
                                detail=f"{name} must be ISO-8601 UTC")

    since_dt, until_dt = _ts("since", since), _ts("until", until)
    try:
        async with request.app.state.sessionmaker() as s:
            body = await stats.collect(s, since=since_dt, until=until_dt,
                                       limit=max(0, min(limit, 100)))
    except Exception:  # noqa: BLE001
        # Честный 5xx вместо 200 с нулями: нули читаются админкой как «работы не
        # было», а это неправда (условие App1).
        logger.exception("stats aggregation failed")
        raise HTTPException(status_code=503, detail="stats unavailable")
    return JSONResponse(body)


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
