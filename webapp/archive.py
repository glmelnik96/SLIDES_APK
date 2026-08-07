"""Temporary archive of finished sessions — the app's real inputs and outputs.

A session dir is the only place holding what the user fed in (input.<ext>,
plan.json, chat.json) and what the app produced (the engine's as-built html, the
final deck.html after edits). Retention wipes it on the 24h TTL, so the evidence
we need to see WHERE the app gets things wrong disappears with it. Just before
that delete, pack the dir into ARCHIVE_DIR as <sid>.tar.gz plus a <sid>.json
sidecar of the job's metrics (greppable without unpacking anything).

Off by default: empty ARCHIVE_DIR means no archiving at all, so local dev and
tests never write. Archiving is best-effort — see retention.purge_once: a failure
here must never stop the purge, or the disk fills with un-deleted sessions.
"""
from __future__ import annotations

import json
import os
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SUFFIX = ".tar.gz"


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def archive_session(root: Path, session_id: str, src: Path,
                    meta: dict[str, Any]) -> Path | None:
    """Pack ``src`` into ``root/<session_id>.tar.gz`` + a meta sidecar.
    Returns the archive path, or None if there was nothing to archive."""
    if not src.is_dir() or not any(p.is_file() for p in src.rglob("*")):
        return None
    root.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(root, 0o700)  # raw user content — not world-readable
    except OSError:
        pass

    # Write to a temp name and rename: a crash mid-write would otherwise leave a
    # truncated .tar.gz that looks like a real archive.
    tmp = root / f".{session_id}{_SUFFIX}.part"
    try:
        with tarfile.open(tmp, "w:gz") as tf:
            tf.add(src, arcname=session_id)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    final = root / f"{session_id}{_SUFFIX}"
    tmp.replace(final)

    payload = _jsonable(dict(meta))
    payload["session_id"] = session_id
    payload["archived_at"] = datetime.now(timezone.utc).isoformat()
    (root / f"{session_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return final


def _drop(tar: Path) -> None:
    tar.unlink(missing_ok=True)
    tar.with_name(tar.name[:-len(_SUFFIX)] + ".json").unlink(missing_ok=True)


def purge_archive(root: Path, *, ttl_days: int, max_bytes: int) -> int:
    """Drop archives older than ttl_days, then evict oldest-first until the
    total fits max_bytes. Returns how many archives were removed."""
    if not root.is_dir():
        return 0
    tars = sorted(root.glob(f"*{_SUFFIX}"), key=lambda p: p.stat().st_mtime)
    cutoff = time.time() - ttl_days * 86400
    removed, keep = 0, []
    for p in tars:
        if p.stat().st_mtime < cutoff:
            _drop(p)
            removed += 1
        else:
            keep.append(p)
    total = sum(p.stat().st_size for p in keep)
    for p in keep:
        if total <= max_bytes:
            break
        total -= p.stat().st_size
        _drop(p)
        removed += 1
    return removed
