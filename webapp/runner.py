"""Run the engine in a worker thread and bridge worker.progress -> asyncio queues.

Jobs run ONE AT A TIME (single worker thread = FIFO queue): the bottleneck is the
shared Cloud.ru account RPS budget (~18 req/s), not local CPU, and one job already
saturates it via the engine's internal parallelism. Up to MAX_ACTIVE jobs may sit
in the system (1 running + the rest queued); a 6th is rejected with CapacityError.

A single routing sink is installed on worker.progress.publish; it dispatches each
ProgressEvent to the right per-session asyncio.Queue by event.session_id, records
the latest status snapshot, and captures result_path on terminal `done`.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

MAX_ACTIVE = 5  # total jobs in the system: 1 running + up to 4 queued


class CapacityError(RuntimeError):
    """Raised when the active-job limit (MAX_ACTIVE) is reached."""


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
    def __init__(self, max_active: int = MAX_ACTIVE) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        # One worker = strict FIFO; queued jobs wait their turn. Matches the
        # shared Cloud.ru RPS ceiling (parallel jobs wouldn't go faster).
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._max_active = max_active
        self._queues: dict[str, asyncio.Queue] = {}
        self._results: dict[str, str | None] = {}
        self._status: dict[str, dict] = {}     # session_id -> latest event dict
        self._meta: dict[str, dict] = {}       # session_id -> {mode}
        self._active: set[str] = set()
        self._sink_installed = False

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

    def active_jobs(self) -> list[dict]:
        """Snapshot of jobs not yet terminal: id, mode, stage, progress_pct."""
        out = []
        for sid in self._active:
            st = self._status.get(sid) or {}
            out.append({
                "session_id": sid,
                "mode": self._meta.get(sid, {}).get("mode", ""),
                "stage": st.get("stage", "queued"),
                "progress_pct": st.get("progress_pct", 0),
            })
        return out

    # ── routing sink ─────────────────────────────────────────────────────
    def _install_sink(self) -> None:
        if self._sink_installed:
            return
        prog = _progress_module()

        def sink(event: Any) -> None:
            sid = getattr(event, "session_id", None)
            data = event.model_dump(mode="json")
            self._status[sid] = data
            if data.get("terminal"):
                if data.get("stage") == "done":
                    self._results[sid] = data.get("result_path")
                self._active.discard(sid)
            q = self._queues.get(sid)
            if q is not None and self._loop is not None:
                asyncio.run_coroutine_threadsafe(q.put(data), self._loop)

        prog.publish = sink  # type: ignore[assignment]
        self._sink_installed = True

    # ── job lifecycle ────────────────────────────────────────────────────
    def start(self, inp: Any) -> asyncio.Queue:
        assert self._loop is not None, "bind_loop() must be called at startup"
        if len(self._active) >= self._max_active:
            raise CapacityError(f"максимум {self._max_active} сборок одновременно")
        self._install_sink()
        session_id = inp.session_id
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[session_id] = queue
        self._results[session_id] = None
        self._meta[session_id] = {"mode": _mode_of(inp)}
        self._status[session_id] = {"stage": "queued", "progress_pct": 0,
                                    "terminal": False}
        self._active.add(session_id)

        def work() -> None:
            try:
                _pipeline_run(inp)
            except Exception as exc:  # noqa: BLE001
                self._active.discard(session_id)
                failed = {"stage": "failed", "terminal": True,
                          "error": f"{type(exc).__name__}: {exc}", "result_path": None}
                self._status[session_id] = failed
                if self._loop is not None:
                    asyncio.run_coroutine_threadsafe(queue.put(failed), self._loop)

        self._pool.submit(work)
        return queue
