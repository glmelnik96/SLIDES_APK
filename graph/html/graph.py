"""Standalone LangGraph for the /html pipeline (Path B).

    START → parse → brief → classify → html_compose → html_pack → finalize → END

parse/brief/classify are reused verbatim from the donor pipeline (read-only);
the HTML-specific nodes live in ``graph.html.nodes``. Kept fully separate from
``graph.graph`` and ``graph.designer.graph`` so /verstai and /design can never
be affected.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph

from graph.html.nodes import (
    html_compose_node,
    html_finalize_node,
    html_pack_node,
)
from graph.nodes.agents import brief_node, classify_node
from graph.nodes.pipeline import parse_node
from schemas.session import SessionState
from storage.redis_client import DB, url_for

N_PARSE = "parse"
N_BRIEF = "brief"
N_CLASSIFY = "classify"
N_COMPOSE = "html_compose"
N_PACK = "html_pack"
N_FINALIZE = "finalize"


def build_html_graph() -> StateGraph:
    g = StateGraph(SessionState)
    g.add_node(N_PARSE, parse_node)
    g.add_node(N_BRIEF, brief_node)
    g.add_node(N_CLASSIFY, classify_node)
    g.add_node(N_COMPOSE, html_compose_node)
    g.add_node(N_PACK, html_pack_node)
    g.add_node(N_FINALIZE, html_finalize_node)

    g.add_edge(START, N_PARSE)
    g.add_edge(N_PARSE, N_BRIEF)
    g.add_edge(N_BRIEF, N_CLASSIFY)
    g.add_edge(N_CLASSIFY, N_COMPOSE)
    g.add_edge(N_COMPOSE, N_PACK)
    g.add_edge(N_PACK, N_FINALIZE)
    g.add_edge(N_FINALIZE, END)
    return g


@lru_cache(maxsize=1)
def get_compiled_html_graph() -> Any:
    from langgraph.checkpoint.redis import RedisSaver

    saver = RedisSaver.from_conn_string(url_for(DB.LANGGRAPH))
    if hasattr(saver, "__enter__"):
        saver = saver.__enter__()  # noqa: PLC2801 — long-lived process owns it
    if hasattr(saver, "setup"):
        saver.setup()
    return build_html_graph().compile(checkpointer=saver)
