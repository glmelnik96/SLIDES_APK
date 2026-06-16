"""Cross-platform working-directory helpers (pathlib only — Mac + Windows)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def workdir_root() -> Path:
    env = os.environ.get("SLIDESBOT_WORKDIR")
    root = Path(env) if env else Path(tempfile.gettempdir()) / "slidesapp" / "sessions"
    root.mkdir(parents=True, exist_ok=True)
    return root


def session_dir(session_id: str) -> Path:
    d = workdir_root() / session_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def history_file() -> Path:
    return workdir_root() / "history.json"
