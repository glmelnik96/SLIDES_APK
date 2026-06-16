"""On-disk build history (last ~10). JSON index in the workdir root."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone

from webapp.paths import history_file, workdir_root

_CAP = 10


def _read() -> list[dict]:
    f = history_file()
    if not f.is_file():
        return []
    try:
        return json.loads(f.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def _write(items: list[dict]) -> None:
    history_file().write_text(json.dumps(items, ensure_ascii=False, indent=2), "utf-8")


def add(*, id: str, mode: str, source_filename: str | None,
        result_path: str | None, kind: str) -> None:
    items = _read()
    items.insert(0, {
        "id": id,
        "mode": mode,
        "source_filename": source_filename,
        "result_path": result_path,
        "kind": kind,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    _write(items[:_CAP])


def list_recent() -> list[dict]:
    return _read()[:_CAP]


def clear() -> None:
    for item in _read():
        sess = workdir_root() / item["id"]
        if sess.is_dir():
            shutil.rmtree(sess, ignore_errors=True)
    f = history_file()
    if f.is_file():
        f.unlink()
