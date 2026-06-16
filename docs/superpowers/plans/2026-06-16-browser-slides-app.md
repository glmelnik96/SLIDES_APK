# Browser Slides App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Standalone single-user browser app (FastAPI) at `SLIDES_APK` that runs the existing slide-generation engine (`Slides_bot` + `htmlslides`) without Telegram, with in-browser text editing of HTML decks and PNG/PPTX export.

**Architecture:** One FastAPI process. The engine runs in a background thread (no Redis/Celery/Docker); graphs are compiled with the raw builders (no checkpointer). Progress is bridged from `worker.progress.publish` into an asyncio queue → WebSocket. The terminal `done` event carries `result_path` for every mode. HTML decks are edited in an `<iframe>` via `contenteditable` and re-rendered to PNG server-side with Playwright.

**Tech Stack:** Python 3.10+, FastAPI, uvicorn, `websockets`/Starlette WS, `slides_bot` (path dep), `htmlslides` (path dep), Playwright (Chromium), pytest.

---

## Engine facts this plan relies on (verified)

- `schemas.session.SessionInput(user_id, chat_id, progress_message_id, mode, input_s3_key, source_filename)` — only `user_id/chat_id/progress_message_id/mode` are required; `session_id` auto-generated.
- `schemas.session.Mode` values: `VERSTAI="verstai"`, `DESIGN="design"`, `HTMLNEW="htmlnew"` (others out of scope).
- `schemas.session.SessionState.from_input(inp)` builds state; `state.model_dump()` is what graphs accept.
- `schemas.session.ProgressEvent(session_id, stage, progress_pct, detail, terminal, error, result_path)`.
- Graph builders (compile WITHOUT checkpointer, like `scripts/live_run.py`):
  - verstai → `graph.graph._build_graph().compile()` then `.invoke(state.model_dump())`
  - design → `graph.designer.graph.build_designer_graph().compile()` then `.invoke(state.model_dump())`
  - htmlnew → `worker.tasks.htmlnew.run_htmlnew(state)` (NOT LangGraph)
- Every mode's finalize publishes `worker.progress.done(session_id, detail=..., result_path=<abs path>)`:
  - verstai: `graph/nodes/pipeline.py:1619`; design: `graph/designer/nodes.py:285`; htmlnew: `worker/tasks/htmlnew.py:78`.
- `worker.progress.publish(event: ProgressEvent)` is the single global sink. Monkeypatching it (as `scripts/live_run.py` does) redirects all progress.
- `bot.config.get_settings()` requires `TELEGRAM_BOT_TOKEN` and `CLOUDRU_API_KEY` (others have defaults); `extra="ignore"`.
- `htmlslides.pipeline.screenshot.screenshot_slides(html_path, indices, out_dir, viewport=(1920,1080))` returns `{index: Path}`; indices are 1-based; raises `QAUnavailable` if Playwright/Chromium missing.
- HTML deck structure: one self-contained file with N `<section class="slide">` elements; JS engine exposes `window.deck.goTo(i)` (0-based).

---

## File Structure

```
webapp/
  __init__.py
  __main__.py        — uvicorn entrypoint
  config_shim.py     — set placeholder env BEFORE engine import
  paths.py           — workdir/session path helpers (pathlib, cross-platform)
  pipeline_bridge.py — mode → compiled graph / run_htmlnew; run(inp)
  runner.py          — thread execution + progress→asyncio bridge
  history.py         — JSON history index on disk
  deck_edit.py       — save edited deck.html; count slides
  render_png.py      — deck.html → PNGs → ZIP
  app.py             — FastAPI app, routes, WS
  static/
    index.html
    editor.html
    app.js
    editor.js
    styles.css
tests/
  test_config_shim.py
  test_paths.py
  test_pipeline_bridge.py
  test_runner.py
  test_history.py
  test_deck_edit.py
  test_render_png.py
  test_app.py
pyproject.toml
.env.example
.gitignore
start.sh
start.bat
README.md
```

---

### Task 0: Project scaffold

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `webapp/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "slides-app"
version = "0.1.0"
description = "Browser app for Cloud.ru slide generation (no Telegram)"
requires-python = ">=3.10"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "python-multipart>=0.0.9",
    "slides-bot",
    "htmlslides",
]

[project.optional-dependencies]
dev = ["pytest>=8", "httpx>=0.27"]

[tool.uv.sources]
slides-bot = { path = "../Slides_bot", editable = true }
htmlslides = { path = "../HTML_Slides_Skill/html-slides-skill", editable = true }

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["slow: hits live Cloud.ru", "playwright: needs Chromium"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["webapp"]
```

- [ ] **Step 2: Create `.gitignore`**

```gitignore
__pycache__/
*.pyc
.env
.venv/
.superpowers/
*.egg-info/
```

- [ ] **Step 3: Create `.env.example`**

```dotenv
# Только этот ключ обязателен для пользователя.
CLOUDRU_API_KEY=put-your-key-here
# Необязательно — переопределить базовый URL Cloud.ru FM:
# CLOUDRU_BASE_URL=https://foundation-models.api.cloud.ru/v1
```

- [ ] **Step 4: Create empty `webapp/__init__.py` and `tests/__init__.py`**

```python
```

- [ ] **Step 5: Commit**

```bash
git init
git add pyproject.toml .gitignore .env.example webapp/__init__.py tests/__init__.py
git commit -m "chore: scaffold slides-app project"
```

---

### Task 1: Config shim

Engine imports (`llm.client`) call `bot.config.get_settings()`, which requires
`TELEGRAM_BOT_TOKEN`. The shim sets a placeholder so the user only needs `CLOUDRU_API_KEY`.

**Files:**
- Create: `webapp/config_shim.py`
- Test: `tests/test_config_shim.py`

- [ ] **Step 1: Write the failing test**

```python
import os
import importlib


def test_apply_sets_placeholder_when_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("CLOUDRU_API_KEY", "k")
    shim = importlib.import_module("webapp.config_shim")
    shim.apply()
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "unused"


def test_apply_does_not_overwrite_existing(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "real")
    monkeypatch.setenv("CLOUDRU_API_KEY", "k")
    shim = importlib.import_module("webapp.config_shim")
    shim.apply()
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "real"


def test_apply_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("CLOUDRU_API_KEY", raising=False)
    shim = importlib.import_module("webapp.config_shim")
    import pytest
    with pytest.raises(SystemExit):
        shim.apply()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config_shim.py -v`
Expected: FAIL (module/function not found)

- [ ] **Step 3: Write minimal implementation**

```python
"""Set placeholder env vars BEFORE the engine imports bot.config.get_settings().

bot.config.Settings requires TELEGRAM_BOT_TOKEN. This standalone app does not use
Telegram, so we inject a placeholder. The user only supplies CLOUDRU_API_KEY (.env).
Call apply() at process start, before importing any engine module.
"""
from __future__ import annotations

import os
import sys

_PLACEHOLDERS = {
    "TELEGRAM_BOT_TOKEN": "unused",
}


def apply() -> None:
    if not os.environ.get("CLOUDRU_API_KEY"):
        print("ERROR: CLOUDRU_API_KEY missing. Put it in .env.", file=sys.stderr)
        raise SystemExit(2)
    for key, value in _PLACEHOLDERS.items():
        os.environ.setdefault(key, value)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config_shim.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add webapp/config_shim.py tests/test_config_shim.py
git commit -m "feat: config shim for engine settings"
```

---

### Task 2: Path helpers

**Files:**
- Create: `webapp/paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
import webapp.paths as paths


def test_workdir_root_uses_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    assert paths.workdir_root() == tmp_path


def test_session_dir_created(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    d = paths.session_dir("abc123")
    assert d == tmp_path / "abc123"
    assert d.is_dir()


def test_default_root_is_tempdir(monkeypatch):
    monkeypatch.delenv("SLIDESBOT_WORKDIR", raising=False)
    root = paths.workdir_root()
    assert root.name == "sessions"
    assert "slidesapp" in str(root)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_paths.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_paths.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add webapp/paths.py tests/test_paths.py
git commit -m "feat: cross-platform path helpers"
```

---

### Task 3: Pipeline bridge

Selects and runs the right engine path for a mode. Engine symbols are imported lazily
inside `run()` so tests can monkeypatch them without importing the whole engine.

**Files:**
- Create: `webapp/pipeline_bridge.py`
- Test: `tests/test_pipeline_bridge.py`

- [ ] **Step 1: Write the failing test**

```python
import types
import webapp.pipeline_bridge as pb


class _FakeInp:
    def __init__(self, mode):
        self.mode = mode
        self.session_id = "s1"


def test_run_htmlnew_path(monkeypatch):
    calls = {}
    monkeypatch.setattr(pb, "_state_from_input", lambda inp: "STATE")
    fake_htmlnew = types.SimpleNamespace(run_htmlnew=lambda s: calls.setdefault("htmlnew", s))
    monkeypatch.setitem(pb._ENGINE, "htmlnew", lambda: fake_htmlnew.run_htmlnew)
    pb.run(_FakeInp("htmlnew"))
    assert calls["htmlnew"] == "STATE"


def test_run_verstai_compiles_and_invokes(monkeypatch):
    invoked = {}

    class _Graph:
        def invoke(self, payload):
            invoked["payload"] = payload

    class _Builder:
        def compile(self):
            return _Graph()

    monkeypatch.setattr(pb, "_state_from_input", lambda inp: types.SimpleNamespace(
        model_dump=lambda: {"k": "v"}))
    monkeypatch.setitem(pb._ENGINE, "verstai", lambda: _Builder())
    pb.run(_FakeInp("verstai"))
    assert invoked["payload"] == {"k": "v"}


def test_unknown_mode_raises():
    import pytest
    with pytest.raises(ValueError):
        pb.run(_FakeInp("nope"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline_bridge.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
"""Map a SessionInput.mode to the engine path that runs it.

verstai/design compile a LangGraph WITHOUT a checkpointer (no Redis), exactly like
scripts/live_run.py. htmlnew calls run_htmlnew directly (it is not a LangGraph).
Engine imports are lazy so unit tests can stub them via the _ENGINE registry.
"""
from __future__ import annotations

from typing import Any, Callable


def _verstai_builder():
    from graph.graph import _build_graph
    return _build_graph()


def _design_builder():
    from graph.designer.graph import build_designer_graph
    return build_designer_graph()


def _htmlnew_runner() -> Callable[[Any], Any]:
    from worker.tasks.htmlnew import run_htmlnew
    return run_htmlnew


# Indirection point for tests.
_ENGINE: dict[str, Callable[[], Any]] = {
    "verstai": _verstai_builder,
    "design": _design_builder,
    "htmlnew": _htmlnew_runner,
}


def _state_from_input(inp: Any) -> Any:
    from schemas.session import SessionState
    return SessionState.from_input(inp)


def run(inp: Any) -> None:
    """Run the pipeline for one job. Progress (incl. terminal result_path) is emitted
    via worker.progress.* by the engine itself — the caller installs the sink first."""
    mode = inp.mode.value if hasattr(inp.mode, "value") else str(inp.mode)
    state = _state_from_input(inp)
    if mode == "htmlnew":
        _ENGINE["htmlnew"]()(state)
        return
    if mode in ("verstai", "design"):
        graph = _ENGINE[mode]().compile()
        graph.invoke(state.model_dump())
        return
    raise ValueError(f"unsupported mode: {mode!r}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_pipeline_bridge.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add webapp/pipeline_bridge.py tests/test_pipeline_bridge.py
git commit -m "feat: pipeline bridge (mode -> engine path)"
```

---

### Task 4: Runner (thread + progress bridge)

Runs `pipeline_bridge.run` in a thread pool, redirects `worker.progress.publish` into a
per-session `asyncio.Queue`, and records the terminal event (with `result_path`). Single
active job at a time (the global `publish` sink allows only one).

**Files:**
- Create: `webapp/runner.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: Write the failing test**

```python
import asyncio
import types
import pytest
import webapp.runner as runner


class _Event:
    def __init__(self, terminal=False, stage="parsing", result_path=None):
        self.terminal = terminal
        self.stage = stage
        self.result_path = result_path
        self.session_id = "s1"

    def model_dump(self, mode="json"):
        return {"stage": self.stage, "terminal": self.terminal,
                "result_path": self.result_path}


@pytest.mark.asyncio
async def test_run_forwards_events_and_captures_result(monkeypatch):
    r = runner.JobRunner()
    r.bind_loop(asyncio.get_running_loop())

    # Fake progress module the runner will patch.
    prog = types.SimpleNamespace(publish=None)
    monkeypatch.setattr(runner, "_progress_module", lambda: prog)

    def fake_run(inp):
        prog.publish(_Event(stage="parsing"))
        prog.publish(_Event(terminal=True, stage="done", result_path="/tmp/out.pptx"))

    monkeypatch.setattr(runner, "_pipeline_run", fake_run)

    inp = types.SimpleNamespace(session_id="s1", mode="verstai")
    q = r.start(inp)
    events = []
    while True:
        ev = await asyncio.wait_for(q.get(), timeout=2)
        events.append(ev)
        if ev["terminal"]:
            break
    assert events[0]["stage"] == "parsing"
    assert events[-1]["result_path"] == "/tmp/out.pptx"
    assert r.result_path("s1") == "/tmp/out.pptx"
```

(Add `pytest-asyncio` to dev deps if not present: `pip install pytest-asyncio` and put
`asyncio_mode = "auto"` under `[tool.pytest.ini_options]`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_runner.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_runner.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add webapp/runner.py tests/test_runner.py pyproject.toml
git commit -m "feat: job runner with progress->asyncio bridge"
```

---

### Task 5: History index

**Files:**
- Create: `webapp/history.py`
- Test: `tests/test_history.py`

- [ ] **Step 1: Write the failing test**

```python
import webapp.history as history


def test_add_and_list(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    history.add(id="a", mode="htmlnew", source_filename="x.md",
                result_path=str(tmp_path / "a" / "deck.html"), kind="html")
    history.add(id="b", mode="verstai", source_filename="y.pptx",
                result_path=str(tmp_path / "b" / "out.pptx"), kind="pptx")
    items = history.list_recent()
    assert [i["id"] for i in items] == ["b", "a"]  # newest first


def test_list_caps_at_ten(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    for i in range(12):
        history.add(id=str(i), mode="htmlnew", source_filename=f"{i}.md",
                    result_path="p", kind="html")
    assert len(history.list_recent()) == 10


def test_clear_removes_index_and_files(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    sess = tmp_path / "a"
    sess.mkdir()
    (sess / "deck.html").write_text("hi", encoding="utf-8")
    history.add(id="a", mode="htmlnew", source_filename="x.md",
                result_path=str(sess / "deck.html"), kind="html")
    history.clear()
    assert history.list_recent() == []
    assert not sess.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_history.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_history.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add webapp/history.py tests/test_history.py
git commit -m "feat: on-disk build history"
```

---

### Task 6: Deck edit (save HTML + count slides)

**Files:**
- Create: `webapp/deck_edit.py`
- Test: `tests/test_deck_edit.py`

- [ ] **Step 1: Write the failing test**

```python
import webapp.deck_edit as deck_edit


def test_count_slides():
    html = ('<div class="deck-stage">'
            '<section class="slide">a</section>'
            '<section class="slide slide--chrome-sm">b</section>'
            '</div>')
    assert deck_edit.count_slides(html) == 2


def test_save_deck_writes_file(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    path = deck_edit.save_deck("sess1", "<html>edited</html>")
    assert path.read_text("utf-8") == "<html>edited</html>"
    assert path.name == "deck.html"
    assert path.parent.name == "sess1"


def test_save_deck_rejects_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    import pytest
    with pytest.raises(ValueError):
        deck_edit.save_deck("sess1", "   ")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_deck_edit.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
"""Persist the browser-edited deck HTML and count its slides."""
from __future__ import annotations

import re
from pathlib import Path

from webapp.paths import session_dir

_SLIDE_RE = re.compile(r'<section[^>]*\bclass="[^"]*\bslide\b', re.IGNORECASE)


def count_slides(html: str) -> int:
    return len(_SLIDE_RE.findall(html))


def deck_path(session_id: str) -> Path:
    return session_dir(session_id) / "deck.html"


def save_deck(session_id: str, html: str) -> Path:
    if not html or not html.strip():
        raise ValueError("empty deck HTML")
    path = deck_path(session_id)
    path.write_text(html, encoding="utf-8")
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_deck_edit.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add webapp/deck_edit.py tests/test_deck_edit.py
git commit -m "feat: deck edit save + slide count"
```

---

### Task 7: Render PNG → ZIP

**Files:**
- Create: `webapp/render_png.py`
- Test: `tests/test_render_png.py`

- [ ] **Step 1: Write the failing test**

```python
import zipfile
import types
import webapp.render_png as render_png


def test_zip_pngs_packs_files(tmp_path):
    a = tmp_path / "qa-slide-01.png"
    b = tmp_path / "qa-slide-02.png"
    a.write_bytes(b"\x89PNG-a")
    b.write_bytes(b"\x89PNG-b")
    out = tmp_path / "deck.zip"
    render_png.zip_pngs({1: a, 2: b}, out)
    with zipfile.ZipFile(out) as zf:
        assert sorted(zf.namelist()) == ["slide-01.png", "slide-02.png"]


def test_export_zip_invokes_screenshotter(monkeypatch, tmp_path):
    deck = tmp_path / "deck.html"
    deck.write_text('<section class="slide">a</section>'
                    '<section class="slide">b</section>', encoding="utf-8")

    captured = {}

    def fake_shot(html_path, indices, out_dir, viewport=(1920, 1080)):
        captured["indices"] = list(indices)
        captured["viewport"] = viewport
        paths = {}
        for i in indices:
            p = tmp_path / f"qa-slide-{i:02d}.png"
            p.write_bytes(b"\x89PNG")
            paths[i] = p
        return paths

    monkeypatch.setattr(render_png, "_screenshot_slides", fake_shot)
    out = render_png.export_zip(deck, tmp_path / "out.zip")
    assert captured["indices"] == [1, 2]
    assert captured["viewport"] == (1920, 1080)
    assert out.is_file()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_render_png.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
"""Render a saved deck.html to per-slide PNGs (1920x1080) and pack into a ZIP.

Reuses htmlslides' Playwright screenshotter. _screenshot_slides is an indirection
point so tests don't need Chromium.
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Iterable

from webapp.deck_edit import count_slides


def _screenshot_slides(html_path, indices, out_dir, viewport=(1920, 1080)):
    from htmlslides.pipeline.screenshot import screenshot_slides
    return screenshot_slides(html_path, indices, out_dir, viewport=viewport)


def zip_pngs(pngs: dict[int, Path], out_zip: Path) -> Path:
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for index in sorted(pngs):
            zf.write(pngs[index], arcname=f"slide-{index:02d}.png")
    return out_zip


def export_zip(deck_html: Path, out_zip: Path) -> Path:
    n = count_slides(deck_html.read_text("utf-8"))
    indices: Iterable[int] = range(1, n + 1)
    out_dir = deck_html.parent / "png"
    out_dir.mkdir(parents=True, exist_ok=True)
    pngs = _screenshot_slides(deck_html, indices, out_dir, viewport=(1920, 1080))
    return zip_pngs(pngs, out_zip)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_render_png.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add webapp/render_png.py tests/test_render_png.py
git commit -m "feat: deck PNG export to ZIP"
```

---

### Task 8: FastAPI app + routes + WebSocket

Uses a module-level `JobRunner`. Engine-running is isolated behind the runner, so the
HTTP tests mock the runner. `config_shim.apply()` runs at import of `app` only when
`SLIDES_APP_SKIP_SHIM` is unset (tests set it to avoid requiring a real key).

**Files:**
- Create: `webapp/app.py`, `webapp/__main__.py`
- Test: `tests/test_app.py`

- [ ] **Step 1: Write the failing test**

```python
import os
import asyncio
import types
os.environ["SLIDES_APP_SKIP_SHIM"] = "1"

from fastapi.testclient import TestClient
import webapp.app as appmod


def _client():
    return TestClient(appmod.app)


def test_index_served():
    r = _client().get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_history_endpoints(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    c = _client()
    assert c.get("/api/history").json() == []
    import webapp.history as history
    history.add(id="a", mode="htmlnew", source_filename="x.md",
                result_path="p", kind="html")
    assert len(c.get("/api/history").json()) == 1
    assert c.post("/api/history/clear").status_code == 200
    assert c.get("/api/history").json() == []


def test_create_job_starts_runner(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    started = {}

    def fake_start(inp):
        started["mode"] = inp.mode.value
        started["session"] = inp.session_id
        return asyncio.Queue()

    monkeypatch.setattr(appmod.runner, "start", fake_start)
    c = _client()
    r = c.post("/api/jobs", data={"mode": "htmlnew"},
               files={"file": ("x.md", b"# hi", "text/markdown")})
    assert r.status_code == 200
    assert "session_id" in r.json()
    assert started["mode"] == "htmlnew"


def test_create_job_rejects_bad_type(monkeypatch, tmp_path):
    monkeypatch.setenv("SLIDESBOT_WORKDIR", str(tmp_path))
    c = _client()
    r = c.post("/api/jobs", data={"mode": "verstai"},
               files={"file": ("x.md", b"hi", "text/markdown")})
    assert r.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_app.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Write minimal implementation**

```python
"""FastAPI app: home, job creation, progress WS, result/deck/png/history endpoints."""
from __future__ import annotations

import os
from pathlib import Path

if not os.environ.get("SLIDES_APP_SKIP_SHIM"):
    from webapp.config_shim import apply as _apply_shim
    _apply_shim()

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from webapp import deck_edit, history, render_png
from webapp.paths import session_dir
from webapp.runner import JobRunner

_STATIC = Path(__file__).parent / "static"

# Allowed upload extensions per mode.
_ALLOWED = {
    "verstai": {".pptx"},
    "design": {".pptx"},
    "htmlnew": {".md", ".txt", ".docx", ".pptx"},
}
_PPTX_MODES = {"verstai", "design"}

app = FastAPI(title="Slides App")
runner = JobRunner()


@app.on_event("startup")
async def _startup() -> None:
    import asyncio
    runner.bind_loop(asyncio.get_event_loop())


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return HTMLResponse((_STATIC / "index.html").read_text("utf-8"))


@app.get("/editor", response_class=HTMLResponse)
def editor() -> HTMLResponse:
    return HTMLResponse((_STATIC / "editor.html").read_text("utf-8"))


@app.post("/api/jobs")
async def create_job(mode: str = Form(...), file: UploadFile = File(...)) -> JSONResponse:
    from schemas.session import Mode, SessionInput
    if mode not in _ALLOWED:
        raise HTTPException(400, f"unsupported mode: {mode}")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED[mode]:
        raise HTTPException(400, f"bad file type {suffix} for mode {mode}")

    inp = SessionInput(user_id=0, chat_id=0, progress_message_id=0, mode=Mode(mode),
                       input_s3_key=None, source_filename=file.filename)
    dest = session_dir(inp.session_id) / f"input{suffix}"
    dest.write_bytes(await file.read())
    inp = inp.model_copy(update={"input_s3_key": str(dest)})

    runner.start(inp)
    kind = "pptx" if mode in _PPTX_MODES else "html"
    history.add(id=inp.session_id, mode=mode, source_filename=file.filename,
                result_path=None, kind=kind)
    return JSONResponse({"session_id": inp.session_id, "kind": kind})


@app.websocket("/ws/{session_id}")
async def ws_progress(ws: WebSocket, session_id: str) -> None:
    await ws.accept()
    queue = runner.queue(session_id)
    if queue is None:
        await ws.send_json({"stage": "failed", "terminal": True,
                            "error": "unknown session"})
        await ws.close()
        return
    while True:
        event = await queue.get()
        await ws.send_json(event)
        if event.get("terminal"):
            break
    await ws.close()


@app.get("/api/jobs/{session_id}/result")
def download_result(session_id: str) -> FileResponse:
    path = runner.result_path(session_id)
    if not path or not Path(path).is_file():
        raise HTTPException(404, "result not ready")
    return FileResponse(path, filename=Path(path).name)


@app.get("/api/jobs/{session_id}/deck", response_class=HTMLResponse)
def get_deck(session_id: str, download: int = 0):
    path = deck_edit.deck_path(session_id)
    if not path.is_file():
        raise HTTPException(404, "deck not found")
    if download:
        return FileResponse(path, filename="deck.html", media_type="text/html")
    return HTMLResponse(path.read_text("utf-8"))


@app.post("/api/jobs/{session_id}/deck")
async def post_deck(session_id: str, request) -> JSONResponse:  # type: ignore[no-untyped-def]
    body = await request.body()
    deck_edit.save_deck(session_id, body.decode("utf-8"))
    return JSONResponse({"ok": True})


@app.get("/api/jobs/{session_id}/png.zip")
def get_png_zip(session_id: str) -> FileResponse:
    deck = deck_edit.deck_path(session_id)
    if not deck.is_file():
        raise HTTPException(404, "deck not found")
    out = session_dir(session_id) / "deck.zip"
    try:
        render_png.export_zip(deck, out)
    except Exception as exc:  # noqa: BLE001 — surface a clear hint
        raise HTTPException(500, f"PNG export failed: {exc}. "
                                 f"Try: playwright install chromium") from exc
    return FileResponse(out, filename="slides.zip", media_type="application/zip")


@app.get("/api/history")
def get_history() -> JSONResponse:
    return JSONResponse(history.list_recent())


@app.post("/api/history/clear")
def clear_history() -> JSONResponse:
    history.clear()
    return JSONResponse({"ok": True})
```

Also add `queue()` to `JobRunner` (Task 4 file) — append this method:

```python
    def queue(self, session_id: str) -> "asyncio.Queue | None":
        return self._queues.get(session_id)
```

And the `post_deck` signature needs the Starlette `Request`. Replace its definition with:

```python
from fastapi import Request

@app.post("/api/jobs/{session_id}/deck")
async def post_deck(session_id: str, request: Request) -> JSONResponse:
    body = await request.body()
    deck_edit.save_deck(session_id, body.decode("utf-8"))
    return JSONResponse({"ok": True})
```

- [ ] **Step 4: Create `webapp/__main__.py`**

```python
"""Run: python -m webapp"""
from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("webapp.app:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_app.py -v`
Expected: PASS (5 passed). Fix the `queue()`/`Request` additions if any test errors.

- [ ] **Step 6: Commit**

```bash
git add webapp/app.py webapp/__main__.py webapp/runner.py tests/test_app.py
git commit -m "feat: FastAPI app, routes, progress WebSocket"
```

---

### Task 9: Frontend — home screen

Frontend is verified manually (no JS test runner). Keep logic minimal and readable.

**Files:**
- Create: `webapp/static/index.html`, `webapp/static/styles.css`, `webapp/static/app.js`

- [ ] **Step 1: Create `webapp/static/index.html`**

```html
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Slides App</title>
<link rel="stylesheet" href="/static/styles.css">
</head>
<body>
<main class="wrap">
  <h1>Генерация презентаций</h1>
  <section class="modes">
    <label class="mode"><input type="radio" name="mode" value="verstai" checked>
      <span>Ребрендинг PPTX по шаблону</span><small>вход: .pptx → .pptx</small></label>
    <label class="mode"><input type="radio" name="mode" value="design">
      <span>Генерация PPTX без шаблона</span><small>вход: .pptx → .pptx</small></label>
    <label class="mode"><input type="radio" name="mode" value="htmlnew">
      <span>HTML-презентация</span><small>вход: md/txt/docx/pptx → HTML</small></label>
  </section>

  <div id="drop" class="drop">Перетащите файл или
    <input type="file" id="file"></div>
  <button id="create" class="btn">Создать</button>

  <section id="progress" class="progress hidden">
    <div class="bar"><div id="barfill"></div></div>
    <p id="stage">…</p>
  </section>

  <section id="result" class="result hidden"></section>

  <section class="history">
    <div class="hist-head"><h2>История</h2>
      <button id="clear" class="btn-link">Очистить</button></div>
    <ul id="histlist"></ul>
  </section>
</main>
<script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `webapp/static/styles.css`**

```css
* { box-sizing: border-box; }
body { font-family: system-ui, sans-serif; margin: 0; background:#0f0f12; color:#eee; }
.wrap { max-width: 760px; margin: 0 auto; padding: 32px 20px; }
h1 { font-size: 22px; }
.modes { display:flex; flex-direction:column; gap:10px; margin:16px 0; }
.mode { display:flex; flex-direction:column; padding:12px; border:1px solid #333;
  border-radius:8px; cursor:pointer; }
.mode small { color:#888; }
.drop { border:2px dashed #444; border-radius:8px; padding:24px; text-align:center;
  margin:12px 0; }
.btn { background:#2a6; color:#fff; border:0; border-radius:8px; padding:10px 18px;
  cursor:pointer; font-size:15px; }
.btn-link { background:none; color:#88a; border:0; cursor:pointer; }
.hidden { display:none; }
.progress .bar { height:8px; background:#222; border-radius:4px; overflow:hidden; }
#barfill { height:100%; width:0; background:#2a6; transition:width .3s; }
.result { margin-top:16px; padding:16px; border:1px solid #2a6; border-radius:8px; }
.history { margin-top:32px; }
.hist-head { display:flex; justify-content:space-between; align-items:center; }
#histlist { list-style:none; padding:0; }
#histlist li { display:flex; justify-content:space-between; padding:8px 0;
  border-bottom:1px solid #222; }
a { color:#6ad; }
```

- [ ] **Step 3: Create `webapp/static/app.js`**

```javascript
const $ = (s) => document.querySelector(s);

function selectedMode() {
  return document.querySelector('input[name="mode"]:checked').value;
}

async function loadHistory() {
  const items = await (await fetch('/api/history')).json();
  const ul = $('#histlist');
  ul.innerHTML = '';
  for (const it of items) {
    const li = document.createElement('li');
    const when = new Date(it.created_at).toLocaleString();
    const action = it.kind === 'html'
      ? `<a href="/editor?session=${it.id}">открыть</a>`
      : `<a href="/api/jobs/${it.id}/result">скачать .pptx</a>`;
    li.innerHTML = `<span>${it.mode} · ${it.source_filename || ''} · ${when}</span>${action}`;
    ul.appendChild(li);
  }
}

$('#clear').onclick = async () => { await fetch('/api/history/clear', {method:'POST'}); loadHistory(); };

$('#create').onclick = async () => {
  const file = $('#file').files[0];
  if (!file) { alert('Выберите файл'); return; }
  const fd = new FormData();
  fd.append('mode', selectedMode());
  fd.append('file', file);
  const res = await fetch('/api/jobs', {method:'POST', body: fd});
  if (!res.ok) { alert('Ошибка: ' + (await res.text())); return; }
  const { session_id, kind } = await res.json();
  streamProgress(session_id, kind);
};

function streamProgress(sessionId, kind) {
  $('#progress').classList.remove('hidden');
  $('#result').classList.add('hidden');
  const ws = new WebSocket(`ws://${location.host}/ws/${sessionId}`);
  ws.onmessage = (e) => {
    const ev = JSON.parse(e.data);
    $('#barfill').style.width = (ev.progress_pct || 0) + '%';
    $('#stage').textContent = (ev.stage || '') + ' — ' + (ev.detail || '');
    if (ev.terminal) {
      ws.close();
      $('#progress').classList.add('hidden');
      showResult(sessionId, kind, ev);
      loadHistory();
    }
  };
}

function showResult(sessionId, kind, ev) {
  const box = $('#result');
  box.classList.remove('hidden');
  if (ev.stage === 'failed') {
    box.innerHTML = `<p>Ошибка: ${ev.error || 'сбой'}</p>
      <button class="btn" onclick="location.reload()">Начать заново</button>`;
    return;
  }
  if (kind === 'pptx') {
    box.innerHTML = `<p>Готово.</p>
      <a class="btn" href="/api/jobs/${sessionId}/result">Скачать .pptx</a>`;
  } else {
    location.href = `/editor?session=${sessionId}`;
  }
}

loadHistory();
```

- [ ] **Step 4: Manual verification**

Run: `pytest tests/test_app.py -v` (still green). Then `python -m webapp`, open
`http://127.0.0.1:8000/`, confirm the page renders, modes show, history loads (empty),
"Очистить" works without errors. (No real job yet — that's Task 11 end-to-end.)

- [ ] **Step 5: Commit**

```bash
git add webapp/static/index.html webapp/static/styles.css webapp/static/app.js
git commit -m "feat: home screen UI"
```

---

### Task 10: Frontend — editor (iframe + contenteditable + export)

**Files:**
- Create: `webapp/static/editor.html`, `webapp/static/editor.js`

- [ ] **Step 1: Create `webapp/static/editor.html`**

```html
<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Редактор деки</title>
<link rel="stylesheet" href="/static/styles.css">
<style>
  .editor { display:flex; gap:12px; height:100vh; }
  .thumbs { width:120px; overflow-y:auto; padding:8px; border-right:1px solid #333; }
  .thumb { padding:8px; border:1px solid #333; border-radius:4px; margin-bottom:6px;
    cursor:pointer; font-size:12px; text-align:center; }
  .thumb.active { border-color:#2a6; color:#2a6; }
  .stage { flex:1; display:flex; flex-direction:column; }
  .toolbar { display:flex; gap:8px; padding:8px; border-bottom:1px solid #333; align-items:center; }
  #frameWrap { flex:1; display:flex; align-items:center; justify-content:center; background:#000; }
  iframe { width:100%; height:100%; border:0; }
</style>
</head>
<body>
<div class="editor">
  <div class="thumbs" id="thumbs"></div>
  <div class="stage">
    <div class="toolbar">
      <button class="btn" id="prev">◀</button>
      <span id="counter">1 / 1</span>
      <button class="btn" id="next">▶</button>
      <span style="flex:1"></span>
      <button class="btn" id="save">Сохранить</button>
      <button class="btn" id="png">Скачать PNG (ZIP)</button>
      <a class="btn" id="html">Скачать HTML</a>
    </div>
    <div id="frameWrap"><iframe id="deck"></iframe></div>
  </div>
</div>
<script src="/static/editor.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `webapp/static/editor.js`**

```javascript
const params = new URLSearchParams(location.search);
const sessionId = params.get('session');
const frame = document.getElementById('deck');
document.getElementById('html').href = `/api/jobs/${sessionId}/deck?download=1`;

let slides = [];
let current = 0;

frame.src = `/api/jobs/${sessionId}/deck`;
frame.onload = () => {
  const doc = frame.contentDocument;
  slides = [...doc.querySelectorAll('.slide')];
  // Make text editable: every leaf element with text gets contenteditable.
  slides.forEach((s) => s.querySelectorAll('*').forEach((el) => {
    if (el.children.length === 0 && el.textContent.trim()) {
      el.setAttribute('contenteditable', 'true');
    }
  }));
  buildThumbs(doc);
  goTo(0);
};

function buildThumbs(doc) {
  const box = document.getElementById('thumbs');
  box.innerHTML = '';
  slides.forEach((_, i) => {
    const t = document.createElement('div');
    t.className = 'thumb';
    t.textContent = 'Слайд ' + (i + 1);
    t.onclick = () => goTo(i);
    box.appendChild(t);
  });
}

function goTo(i) {
  current = Math.max(0, Math.min(slides.length - 1, i));
  const win = frame.contentWindow;
  if (win.deck && win.deck.goTo) win.deck.goTo(current);
  document.getElementById('counter').textContent = `${current + 1} / ${slides.length}`;
  [...document.querySelectorAll('.thumb')].forEach((t, idx) =>
    t.classList.toggle('active', idx === current));
}

document.getElementById('prev').onclick = () => goTo(current - 1);
document.getElementById('next').onclick = () => goTo(current + 1);

document.getElementById('save').onclick = async () => {
  const html = '<!DOCTYPE html>' + frame.contentDocument.documentElement.outerHTML;
  const r = await fetch(`/api/jobs/${sessionId}/deck`, {method:'POST', body: html});
  alert(r.ok ? 'Сохранено' : 'Ошибка сохранения');
};

document.getElementById('png').onclick = async () => {
  // Save current edits first so the render reflects them.
  const html = '<!DOCTYPE html>' + frame.contentDocument.documentElement.outerHTML;
  await fetch(`/api/jobs/${sessionId}/deck`, {method:'POST', body: html});
  location.href = `/api/jobs/${sessionId}/png.zip`;
};
```

- [ ] **Step 3: Manual verification**

Run `python -m webapp`. Generate an HTML-презентация end-to-end (Task 11), then on the
editor: confirm slides list, ◀▶ navigation moves the deck, clicking text lets you type,
"Сохранить" alerts success, "Скачать PNG (ZIP)" downloads a non-empty zip, "Скачать HTML"
downloads the deck.

- [ ] **Step 4: Commit**

```bash
git add webapp/static/editor.html webapp/static/editor.js
git commit -m "feat: deck editor (iframe contenteditable + export)"
```

---

### Task 11: Launch scripts, README, end-to-end verification

**Files:**
- Create: `start.sh`, `start.bat`, `README.md`

- [ ] **Step 1: Create `start.sh` (Mac/Linux)**

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python -m webapp
```

- [ ] **Step 2: Create `start.bat` (Windows)**

```bat
@echo off
cd /d "%~dp0"
python -m webapp
```

- [ ] **Step 3: Create `README.md`**

````markdown
# Slides App (браузерный интерфейс, без Telegram)

Отдельное приложение для одного пользователя. Переиспользует движок из
`../Slides_bot` и `../HTML_Slides_Skill`.

## Установка

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ../Slides_bot
pip install -e ../HTML_Slides_Skill/html-slides-skill
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env   # вписать CLOUDRU_API_KEY
```

## Запуск

```bash
python -m webapp          # или ./start.sh (Mac) / start.bat (Windows)
```

Открыть http://127.0.0.1:8000

## Тесты

```bash
pytest                    # без сети
```
````

- [ ] **Step 4: Run the full test suite**

Run: `pytest -v`
Expected: all unit/integration tests pass (playwright/slow tests skipped if unavailable).

- [ ] **Step 5: End-to-end manual check (requires real `.env` + Chromium)**

1. `python -m webapp`, open the app.
2. HTML-презентация: upload a small `.md`, watch progress to done, land in editor.
3. Edit some text, Save, export PNG ZIP (non-empty), download HTML.
4. Ребрендинг PPTX: upload a small `.pptx`, watch progress, download `.pptx`.
5. Confirm both appear in История; "Очистить" empties it and removes session folders.

- [ ] **Step 6: Commit**

```bash
git add start.sh start.bat README.md
git commit -m "docs: launch scripts and README; e2e verified"
```

---

## Self-Review notes

- **Spec coverage:** modes verstai/design/htmlnew (Tasks 3,8); UI rename (Task 9 labels);
  single-process FastAPI no Redis/Celery (Tasks 4,8); .env-only key + shim (Task 1);
  HTML edit + PNG/HTML export, PPTX as-is (Tasks 6,8,10); fixed 1920×1080 (Task 6);
  editor layout A (Task 10); history B + clear (Tasks 5,8,9); cross-platform paths
  (Task 2) + start scripts (Task 11); no auth (implicit). All covered.
- **result_path uniform across modes** via terminal `done` event (Task 4) — verified in engine.
- **One active job** acceptable for single-user (global `publish` sink) — documented in runner.
- **Out of scope** respected: no `html`/`audit`/`brief`, no PPTX editing, no engine changes.
