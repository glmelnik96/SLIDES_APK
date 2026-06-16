"""Run the engine in a worker thread and bridge worker.progress -> asyncio queue.

worker.progress.publish is a single global sink, so one active job at a time. The sink
forwards each ProgressEvent (as a JSON-able dict) into the session's asyncio.Queue via
run_coroutine_threadsafe, and records the terminal event so the HTTP layer can read
result_path after completion.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def _progress_module():
    from worker import progress
    return progress


def _pipeline_run(inp: Any) -> None:
    from webapp.pipeline_bridge import run
    run(inp)


class JobRunner:
    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pool = ThreadPoolExecutor(max_workers=1)
        self._queues: dict[str, asyncio.Queue] = {}
        self._results: dict[str, str | None] = {}

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def queue(self, session_id: str) -> "asyncio.Queue | None":
        return self._queues.get(session_id)

    def result_path(self, session_id: str) -> str | None:
        return self._results.get(session_id)

    def start(self, inp: Any) -> asyncio.Queue:
        assert self._loop is not None, "bind_loop() must be called at startup"
        session_id = inp.session_id
        queue: asyncio.Queue = asyncio.Queue()
        self._queues[session_id] = queue
        self._results[session_id] = None

        def sink(event: Any) -> None:
            data = event.model_dump(mode="json")
            if data.get("terminal") and data.get("stage") == "done":
                self._results[session_id] = data.get("result_path")
            asyncio.run_coroutine_threadsafe(queue.put(data), self._loop)

        prog = _progress_module()
        prog.publish = sink  # type: ignore[assignment]

        def work() -> None:
            try:
                _pipeline_run(inp)
            except Exception as exc:  # noqa: BLE001
                asyncio.run_coroutine_threadsafe(
                    queue.put({"stage": "failed", "terminal": True,
                               "error": f"{type(exc).__name__}: {exc}",
                               "result_path": None}),
                    self._loop,
                )

        self._pool.submit(work)
        return queue
