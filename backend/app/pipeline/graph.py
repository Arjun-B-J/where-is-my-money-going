"""LangGraph wiring for the ingest-and-categorise pipeline.

    seed ─┬─▶ generate ─▶ store ─┐
          └─▶ load_files ────────┴─▶ rule_tag ─▶ llm_tag ─▶ friend_discover ─▶ validator ─▶ finalize

The branch after `seed` is the only conditional edge: `mode="demo"` generates
synthetic data, `mode="files"` parses real statements from a directory. Both
converge on the same categorisation path.

That branch is a correctness fix, not decoration. Previously the graph could
only generate synthetic data, and real statements were ingested by a standalone
script that reimplemented the tagging steps. Two code paths meant the
documented pipeline and the one that actually ran on real data could drift —
and they had.
"""
from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.clock import utc_now
from app.llm.client import LLMClient, get_llm
from app.models import PipelineRun
from app.pipeline import nodes
from app.pipeline.nodes import PipelineState

logger = logging.getLogger(__name__)


def _bind(fn, *args):
    """Adapt `fn(state, *deps)` to the `fn(state)` signature LangGraph expects."""
    def node(state: PipelineState):
        return fn(state, *args)
    return node


def _bind_async(fn, *args):
    async def node(state: PipelineState):
        return await fn(state, *args)
    return node


def _choose_source(state: PipelineState) -> str:
    return "load_files" if state.get("mode") == "files" else "generate"


def build_graph(db: Session, llm: LLMClient, run: PipelineRun):
    """Compile the graph. One graph per run, because nodes close over `run`."""
    graph = StateGraph(PipelineState)

    graph.add_node("seed", _bind(nodes.node_seed, db))
    graph.add_node("generate", _bind(nodes.node_generate))
    graph.add_node("load_files", _bind(nodes.node_load_files, db))
    graph.add_node("store", _bind(nodes.node_store, db))
    graph.add_node("rule_tag", _bind(nodes.node_rule_tag, db))
    graph.add_node("llm_tag", _bind_async(nodes.node_llm_tag, db, llm))
    graph.add_node("friend_discover", _bind(nodes.node_friend_discover, db))
    graph.add_node("validator", _bind_async(nodes.node_validator, db, llm))
    graph.add_node("finalize", _bind(nodes.node_finalize, db, run))

    graph.add_edge(START, "seed")
    graph.add_conditional_edges(
        "seed", _choose_source, {"generate": "generate", "load_files": "load_files"}
    )
    graph.add_edge("generate", "store")
    graph.add_edge("store", "rule_tag")
    graph.add_edge("load_files", "rule_tag")
    graph.add_edge("rule_tag", "llm_tag")
    graph.add_edge("llm_tag", "friend_discover")
    graph.add_edge("friend_discover", "validator")
    graph.add_edge("validator", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile()


async def run_pipeline(
    db: Session,
    *,
    mode: str = "demo",
    seed: int = 42,
    months: int = 12,
    directory: str | None = None,
    llm: LLMClient | None = None,
) -> PipelineRun:
    """Execute one pipeline run and return its audit record."""
    run = PipelineRun(started_at=utc_now(), status="running", trigger=mode)
    db.add(run)
    db.commit()
    db.refresh(run)

    compiled = build_graph(db, llm or get_llm(), run)
    state: PipelineState = {
        "mode": "files" if mode == "files" else "demo",
        "seed": seed,
        "months": months,
        "directory": directory or ".",
    }

    try:
        await compiled.ainvoke(state)
    except Exception as e:
        logger.exception("Pipeline run %d failed", run.id)
        run.status = "error"
        run.error_message = f"{type(e).__name__}: {e}"
        run.finished_at = utc_now()
        db.commit()
        raise

    db.refresh(run)
    return run


# Consumed by the frontend's pipeline visualiser. Kept next to the graph so the
# two cannot drift apart.
TOPOLOGY: dict[str, Any] = {
    "nodes": [
        {"id": "seed", "label": "Seed defaults", "kind": "deterministic"},
        {"id": "generate", "label": "Generate demo data", "kind": "deterministic",
         "mode": "demo"},
        {"id": "load_files", "label": "Parse statements", "kind": "deterministic",
         "mode": "files"},
        {"id": "store", "label": "Store + dedupe", "kind": "deterministic"},
        {"id": "rule_tag", "label": "Rule engine", "kind": "deterministic"},
        {"id": "llm_tag", "label": "Categorise (local model)", "kind": "llm"},
        {"id": "friend_discover", "label": "Detect people", "kind": "deterministic"},
        {"id": "validator", "label": "Validator agent", "kind": "llm"},
        {"id": "finalize", "label": "Write audit record", "kind": "deterministic"},
    ],
    "edges": [
        {"from": "seed", "to": "generate", "conditional": True},
        {"from": "seed", "to": "load_files", "conditional": True},
        {"from": "generate", "to": "store"},
        {"from": "store", "to": "rule_tag"},
        {"from": "load_files", "to": "rule_tag"},
        {"from": "rule_tag", "to": "llm_tag"},
        {"from": "llm_tag", "to": "friend_discover"},
        {"from": "friend_discover", "to": "validator"},
        {"from": "validator", "to": "finalize"},
    ],
}


def pipeline_topology() -> dict[str, Any]:
    return TOPOLOGY
