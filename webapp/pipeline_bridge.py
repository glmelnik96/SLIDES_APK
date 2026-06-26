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


def _htmlpolish_runner() -> Callable[[Any], Any]:
    from worker.tasks.htmlpolish import run_htmlpolish
    return run_htmlpolish


# Indirection point for tests.
_ENGINE: dict[str, Callable[[], Any]] = {
    "verstai": _verstai_builder,
    "design": _design_builder,
    "htmlnew": _htmlnew_runner,
    "htmlpolish": _htmlpolish_runner,
}


def _state_from_input(inp: Any) -> Any:
    from schemas.session import SessionState
    return SessionState.from_input(inp)


def run(inp: Any) -> None:
    """Run the pipeline for one job. Progress (incl. terminal result_path) is emitted
    via worker.progress.* by the engine itself — the caller installs the sink first."""
    mode = inp.mode.value if hasattr(inp.mode, "value") else str(inp.mode)
    if mode not in ("htmlnew", "htmlpolish", "verstai", "design"):
        raise ValueError(f"unsupported mode: {mode!r}")
    state = _state_from_input(inp)
    if mode in ("htmlnew", "htmlpolish"):
        _ENGINE[mode]()(state)
        return
    graph = _ENGINE[mode]().compile()
    graph.invoke(state.model_dump())
