"""Run the engine in worker threads and bridge worker.progress -> asyncio queues.

Several builds run in parallel (BUILD_WORKERS threads); the rest WAIT in the pool's
queue — submissions are accepted up to MAX_ACTIVE and only then rejected with
CapacityError, so under normal load tasks queue rather than fail. The hard ceiling
on concurrent Cloud.ru calls lives in the pipeline client (CLOUDRU_MAX_INFLIGHT
semaphore), shared across all running builds — that, not the worker count, is what
protects the account, so build_workers can be raised safely.

A single routing sink is installed on worker.progress.publish; it dispatches each
ProgressEvent to the right per-session asyncio.Queue by event.session_id (parallel
builds are isolated because every event carries its own session_id), records the
latest status snapshot, and captures result_path on terminal `done`.
"""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

MAX_ACTIVE = 60  # total jobs in the system (running + waiting) before 429
MAX_PER_USER = 15  # how many jobs one user may have in the system at once
BUILD_WORKERS = 3  # how many builds run in parallel (rest wait in the queue)
BUILD_TIMEOUT_SEC = 2400  # per-build watchdog: force-fail a build past this (~40 min)


class CapacityError(RuntimeError):
    """Raised when a capacity limit (global MAX_ACTIVE or per-user) is reached."""


class JobCancelled(Exception):
    """Raised inside the worker thread to abort a job on user request.

    Cancellation is cooperative: the routing sink raises this from within
    ``worker.progress.publish`` the next time the engine emits progress, so the
    abort lands at a safe checkpoint (between slides / pipeline nodes) and unwinds
    the engine call stack back to ``work()``.
    """


def _progress_module():
    from worker import progress
    return progress


def _pipeline_run(inp: Any) -> None:
    from webapp.pipeline_bridge import run
    run(inp)


def _mode_of(inp: Any) -> str:
    m = getattr(inp, "mode", "")
    return m.value if hasattr(m, "value") else str(m)


class JobRunner:
    def __init__(self, max_active: int = MAX_ACTIVE,
                 max_per_user: int = MAX_PER_USER,
                 build_workers: int = BUILD_WORKERS,
                 build_timeout_sec: int = BUILD_TIMEOUT_SEC) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        # N workers run builds in parallel; submissions beyond N wait in the pool's
        # internal FIFO queue. Real Cloud.ru concurrency is capped separately by the
        # client's CLOUDRU_MAX_INFLIGHT semaphore, so more workers ≠ overload.
        self._pool = ThreadPoolExecutor(max_workers=max(1, build_workers))
        self._max_active = max_active
        self._max_per_user = max_per_user
        self._build_timeout = build_timeout_sec
        self._timed_out: set[str] = set()      # sessions killed by the watchdog
        self._timers: dict[str, threading.Timer] = {}
        self._queues: dict[str, asyncio.Queue] = {}
        self._results: dict[str, str | None] = {}
        self._status: dict[str, dict] = {}     # session_id -> latest event dict
        self._meta: dict[str, dict] = {}       # session_id -> {mode}
        self._active: set[str] = set()
        self._futures: dict[str, Any] = {}     # session_id -> Future
        self._cancel: set[str] = set()         # session_ids asked to stop
        self._sink_installed = False
        # Optional async hook(session_id, data) scheduled on the loop when a job
        # reaches a terminal event — lets the app persist final status to the DB
        # without the runner knowing about the DB.
        self._terminal_hook: Any = None

    def set_terminal_hook(self, hook: Any) -> None:
        self._terminal_hook = hook

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # ── introspection ────────────────────────────────────────────────────
    def queue(self, session_id: str) -> "asyncio.Queue | None":
        return self._queues.get(session_id)

    def result_path(self, session_id: str) -> str | None:
        return self._results.get(session_id)

    def status(self, session_id: str) -> dict | None:
        return self._status.get(session_id)

    def active_count(self) -> int:
        return len(self._active)

    def active_jobs(self, user_id: int | None = None) -> list[dict]:
        """Snapshot of jobs not yet terminal: id, mode, stage, progress_pct.
        If user_id is given, only that user's jobs are returned."""
        out = []
        for sid in self._active:
            meta = self._meta.get(sid, {})
            if user_id is not None and meta.get("user_id") != user_id:
                continue
            st = self._status.get(sid) or {}
            out.append({
                "session_id": sid,
                "mode": meta.get("mode", ""),
                "stage": st.get("stage", "queued"),
                "progress_pct": st.get("progress_pct", 0),
            })
        return out

    def owner(self, session_id: str) -> int | None:
        return self._meta.get(session_id, {}).get("user_id")

    # ── routing sink ─────────────────────────────────────────────────────
    def _install_sink(self) -> None:
        if self._sink_installed:
            return
        prog = _progress_module()

        def sink(event: Any) -> None:
            sid = getattr(event, "session_id", None)
            # Cooperative cancellation: abort the engine at this checkpoint.
            if sid in self._cancel and not getattr(event, "terminal", False):
                raise JobCancelled(sid)
            data = event.model_dump(mode="json")
            if data.get("terminal"):
                if data.get("stage") == "done":
                    # Safety net: never deliver an empty "success". If the engine
                    # reported done but produced no usable file (None path or a
                    # missing file — e.g. a degenerate empty plan), surface it as a
                    # clear failure instead of a silent empty result.
                    rp = data.get("result_path")
                    if rp and Path(rp).is_file():
                        self._results[sid] = rp
                    else:
                        data = {"session_id": sid, "stage": "failed",
                                "terminal": True, "progress_pct": 0,
                                "result_path": None,
                                "error": "движок вернул пустой результат "
                                         "(файл не создан) — повторите запуск"}
                self._active.discard(sid)
            self._status[sid] = data
            if data.get("terminal") and self._terminal_hook and self._loop:
                asyncio.run_coroutine_threadsafe(
                    self._terminal_hook(sid, data), self._loop)
            q = self._queues.get(sid)
            if q is not None and self._loop is not None:
                asyncio.run_coroutine_threadsafe(q.put(data), self._loop)

        prog.publish = sink  # type: ignore[assignment]
        self._sink_installed = True

    # ── watchdog ─────────────────────────────────────────────────────────
    def _arm_watchdog(self, session_id: str) -> None:
        """Start a timer that force-aborts a build past the deadline. Python can't
        kill a thread, so we reuse cooperative cancellation: mark the session and
        the sink raises JobCancelled at the next progress checkpoint. Each LLM call
        returns within its own client timeout, so a checkpoint always arrives soon
        after — bounding total runtime to ~deadline + one LLM timeout."""
        if self._build_timeout <= 0:
            return

        def _fire() -> None:
            if session_id not in self._active:
                return
            self._timed_out.add(session_id)
            self._cancel.add(session_id)       # sink raises JobCancelled next emit

        t = threading.Timer(self._build_timeout, _fire)
        t.daemon = True
        self._timers[session_id] = t
        t.start()

    def _disarm_watchdog(self, session_id: str) -> None:
        t = self._timers.pop(session_id, None)
        if t is not None:
            t.cancel()

    # ── job lifecycle ────────────────────────────────────────────────────
    def start(self, inp: Any, *, user_id: int | None = None) -> asyncio.Queue:
        assert self._loop is not None, "bind_loop() must be called at startup"
        if len(self._active) >= self._max_active:
            raise CapacityError(f"очередь занята: максимум {self._max_active} "
                                "сборок одновременно, попробуйте позже")
        if user_id is not None:
            mine = sum(1 for sid in self._active
                       if self._meta.get(sid, {}).get("user_id") == user_id)
            if mine >= self._max_per_user:
                raise CapacityError(
                    f"у вас уже {mine} активных сборок (лимит "
                    f"{self._max_per_user}) — дождитесь завершения")
        self._install_sink()
        session_id = inp.session_id
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[session_id] = queue
        self._results[session_id] = None
        self._meta[session_id] = {"mode": _mode_of(inp), "user_id": user_id}
        self._status[session_id] = {"stage": "queued", "progress_pct": 0,
                                    "terminal": False}
        self._active.add(session_id)

        def work() -> None:
            self._arm_watchdog(session_id)
            try:
                _pipeline_run(inp)
            except Exception as exc:  # noqa: BLE001
                self._active.discard(session_id)
                # Watchdog: a build past the deadline was marked for abort; report
                # it as a clear timeout failure, NOT a user cancel.
                if session_id in self._timed_out:
                    ev = {"session_id": session_id, "stage": "failed",
                          "terminal": True, "progress_pct": 0, "result_path": None,
                          "error": "сборка превысила лимит времени и была "
                                   "остановлена — повторите запуск"}
                # A JobCancelled (or any error after a stop was requested, e.g. the
                # engine framework re-wrapping it) is reported as a clean cancel.
                elif isinstance(exc, JobCancelled) or session_id in self._cancel:
                    ev = {"session_id": session_id, "stage": "cancelled",
                          "terminal": True, "progress_pct": 0, "result_path": None}
                else:
                    ev = {"session_id": session_id, "stage": "failed",
                          "terminal": True, "result_path": None,
                          "error": f"{type(exc).__name__}: {exc}"}
                self._status[session_id] = ev
                if self._loop is not None:
                    if self._terminal_hook:
                        asyncio.run_coroutine_threadsafe(
                            self._terminal_hook(session_id, ev), self._loop)
                    asyncio.run_coroutine_threadsafe(queue.put(ev), self._loop)
            finally:
                self._disarm_watchdog(session_id)
                self._cancel.discard(session_id)
                self._timed_out.discard(session_id)
                self._futures.pop(session_id, None)

        self._futures[session_id] = self._pool.submit(work)
        return queue

    def cancel(self, session_id: str) -> bool:
        """Request stop of an active job. Returns False if it isn't active.

        A queued (not-yet-started) job is cancelled instantly via its Future and a
        terminal `cancelled` event is emitted here. A running job is flagged; the
        routing sink raises JobCancelled at the next progress checkpoint."""
        if session_id not in self._active:
            return False
        self._cancel.add(session_id)
        fut = self._futures.get(session_id)
        if fut is not None and fut.cancel():  # was still queued — never ran
            self._active.discard(session_id)
            self._cancel.discard(session_id)
            self._futures.pop(session_id, None)
            ev = {"stage": "cancelled", "terminal": True,
                  "progress_pct": 0, "result_path": None}
            self._status[session_id] = ev
            q = self._queues.get(session_id)
            if q is not None and self._loop is not None:
                asyncio.run_coroutine_threadsafe(q.put(ev), self._loop)
        return True
