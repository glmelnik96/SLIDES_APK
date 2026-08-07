"""Temporary archive of finished sessions (inputs + outputs) for product analysis."""
import json
import os
import tarfile
import time
from pathlib import Path

from webapp import archive


def _session(tmp_path: Path, sid: str) -> Path:
    d = tmp_path / "sessions" / sid
    d.mkdir(parents=True)
    (d / "input.md").write_text("# исходник", encoding="utf-8")
    (d / "deck.html").write_text("<html>дека</html>", encoding="utf-8")
    return d


def test_archive_session_packs_files_and_meta(tmp_path):
    src = _session(tmp_path, "s1")
    root = tmp_path / "archive"
    out = archive.archive_session(root, "s1", src, {"mode": "htmlnew",
                                                   "status": "done"})
    assert out == root / "s1.tar.gz"
    with tarfile.open(out) as tf:
        names = set(tf.getnames())
        assert "s1/input.md" in names and "s1/deck.html" in names
        assert tf.extractfile("s1/input.md").read().decode("utf-8") == "# исходник"
    meta = json.loads((root / "s1.json").read_text("utf-8"))
    assert meta["mode"] == "htmlnew" and meta["status"] == "done"
    assert meta["archived_at"]  # stamped by the archiver


def test_archive_session_serializes_datetimes(tmp_path):
    from datetime import datetime, timezone
    src = _session(tmp_path, "s1")
    root = tmp_path / "archive"
    archive.archive_session(root, "s1", src, {
        "created_at": datetime(2026, 8, 7, 12, 0, tzinfo=timezone.utc),
        "finished_at": None})
    meta = json.loads((root / "s1.json").read_text("utf-8"))
    assert meta["created_at"].startswith("2026-08-07T12:00")
    assert meta["finished_at"] is None


def test_archive_session_skips_empty_and_missing(tmp_path):
    root = tmp_path / "archive"
    empty = tmp_path / "sessions" / "s2"
    empty.mkdir(parents=True)
    assert archive.archive_session(root, "s2", empty, {}) is None
    assert archive.archive_session(root, "s3", tmp_path / "nope", {}) is None
    assert not root.exists()  # nothing written for nothing


def test_archive_leaves_no_partial_file_on_failure(tmp_path, monkeypatch):
    # A crash mid-write must not leave a truncated .tar.gz that looks valid.
    src = _session(tmp_path, "s1")
    root = tmp_path / "archive"

    def boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(archive.tarfile, "open", boom)
    try:
        archive.archive_session(root, "s1", src, {})
    except OSError:
        pass
    assert not (root / "s1.tar.gz").exists()
    assert list(root.glob("*.tar.gz")) == []


def test_archive_dir_is_private(tmp_path):
    src = _session(tmp_path, "s1")
    root = tmp_path / "archive"
    archive.archive_session(root, "s1", src, {})
    if os.name != "nt":  # POSIX perms only
        assert oct(root.stat().st_mode)[-3:] == "700"


def test_purge_archive_drops_expired_pairs(tmp_path):
    root = tmp_path / "archive"
    for sid in ("old", "new"):
        archive.archive_session(root, sid, _session(tmp_path, sid), {})
    old_ts = time.time() - 15 * 86400
    os.utime(root / "old.tar.gz", (old_ts, old_ts))
    assert archive.purge_archive(root, ttl_days=14, max_bytes=10**9) == 1
    assert not (root / "old.tar.gz").exists()
    assert not (root / "old.json").exists()   # sidecar goes with the tar
    assert (root / "new.tar.gz").exists()


def test_purge_archive_evicts_oldest_over_size_cap(tmp_path):
    root = tmp_path / "archive"
    for i, sid in enumerate(("a", "b", "c")):
        archive.archive_session(root, sid, _session(tmp_path, sid), {})
        ts = time.time() - (10 - i) * 3600      # a oldest, c newest
        os.utime(root / f"{sid}.tar.gz", (ts, ts))
    keep = (root / "c.tar.gz").stat().st_size + 10
    assert archive.purge_archive(root, ttl_days=14, max_bytes=keep) == 2
    assert {p.name for p in root.glob("*.tar.gz")} == {"c.tar.gz"}


def test_purge_archive_noop_without_dir(tmp_path):
    assert archive.purge_archive(tmp_path / "nope", ttl_days=14,
                                 max_bytes=10**9) == 0
