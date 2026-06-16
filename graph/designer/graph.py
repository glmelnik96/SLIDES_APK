"""Standalone LangGraph for the /design from-scratch designer skill.

    START → parse → brief → classify → art_director → compose → native_assemble → finalize → END

parse/brief/classify are reused verbatim from the donor pipeline (read-only);
the designer-specific nodes live in ``graph.designer.nodes``. Kept fully
separate from ``graph.graph`` so /verstai can never be affected.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

from langgraph.graph import END, START, StateGraph

from graph.designer.nodes import (
    art_director_node,
    compose_node,
    finalize_node,
    native_assemble_node,
)
from graph.nodes.agents import brief_node, classify_node
from graph.nodes.pipeline import parse_node
from schemas.session import SessionState
from storage.redis_client import DB, url_for

N_PARSE = "parse"
N_BRIEF = "brief"
N_CLASSIFY = "classify"
N_ART = "art_director"
N_COMPOSE = "compose"
N_ASSEMBLE = "native_assemble"
N_FINALIZE = "finalize"


def build_designer_graph() -> StateGraph:
    g = StateGraph(SessionState)
    g.add_node(N_PARSE, parse_node)
    g.add_node(N_BRIEF, brief_node)
    g.add_node(N_CLASSIFY, classify_node)
    g.add_node(N_ART, art_director_node)
    g.add_node(N_COMPOSE, compose_node)
    g.add_node(N_ASSEMBLE, native_assemble_node)
    g.add_node(N_FINALIZE, finalize_node)

    g.add_edge(START, N_PARSE)
    g.add_edge(N_PARSE, N_BRIEF)
    g.add_edge(N_BRIEF, N_CLASSIFY)
    g.add_edge(N_CLASSIFY, N_ART)
    g.add_edge(N_ART, N_COMPOSE)
    g.add_edge(N_COMPOSE, N_ASSEMBLE)
    g.add_edge(N_ASSEMBLE, N_FINALIZE)
    g.add_edge(N_FINALIZE, END)
    return g


@lru_cache(maxsize=1)
def get_compiled_designer_graph() -> Any:
    from langgraph.checkpoint.redis import RedisSaver

    saver = RedisSaver.from_conn_string(url_for(DB.LANGGRAPH))
    if hasattr(saver, "__enter__"):
        saver = saver.__enter__()  # noqa: PLC2801 — long-lived process owns it
    if hasattr(saver, "setup"):
        saver.setup()
    return build_designer_graph().compile(checkpointer=saver)
