"""In-memory registry of async deck exports (PNG-ZIP / PPTX).

Both formats drive Chromium to screenshot every slide — seconds of work — so the
editor must not block on a synchronous download (that looked like a hang). Instead
it POSTs to start an export, polls its state, then downloads the finished file.

State lives in process memory keyed by (session_id, fmt); the produced file sits
in the session dir. A restart drops in-flight/ready entries — the editor simply
re-triggers on the next click, so no persistence is needed.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

FORMATS = ("png", "pptx", "figma")


@dataclass
class _Entry:
    state: str = "running"          # running | ready | error
    error: str = ""
    path: Path | None = None
    task: "asyncio.Task | None" = None


class ExportRegistry:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], _Entry] = {}

    def status(self, session_id: str, fmt: str) -> dict:
        entry = self._entries.get((session_id, fmt))
        if entry is None:
            return {"state": "idle"}
        return {"state": entry.state, "error": entry.error}

    def path(self, session_id: str, fmt: str) -> Path | None:
        entry = self._entries.get((session_id, fmt))
        if entry and entry.state == "ready":
            return entry.path
        return None

    def start(self, session_id: str, fmt: str,
              worker: Callable[[], Awaitable[Path]]) -> dict:
        """Begin an export unless one is already running for this (session, fmt).
        A finished/failed entry is replaced so a re-export picks up deck edits."""
        key = (session_id, fmt)
        existing = self._entries.get(key)
        if existing and existing.state == "running":
            return {"state": "running", "error": ""}

        entry = _Entry(state="running")
        self._entries[key] = entry

        async def _run() -> None:
            try:
                entry.path = await worker()
                entry.state = "ready"
            except Exception as exc:  # noqa: BLE001 — surface to the poller
                entry.error = str(exc)
                entry.state = "error"

        entry.task = asyncio.create_task(_run())
        return {"state": "running", "error": ""}

    def drop(self, session_id: str) -> None:
        """Forget a session's exports (e.g. on history clear)."""
        for fmt in FORMATS:
            self._entries.pop((session_id, fmt), None)


registry = ExportRegistry()
