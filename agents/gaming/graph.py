"""T053 — the gaming subgraph: `StateGraph` over `GamingState` (a superset
of the frozen `ResortFlowState`). Mirrors `agents.dining.graph`/
`agents.wellness.graph`'s overall shape (see those modules' docstrings), but
two nodes instead of six-or-seven: `intent_parser -> lookup -> END`.

No `ledger` parameter exists here, unlike every other domain's
`build_*_graph`/`compile_*_graph`. That is deliberate, not an oversight:
`HoldLedger` exists to durably record a hold before the vendor confirms it
exists, so a crash between "we asked for a hold" and "we know whether we got
one" can still be resolved on resume. Gaming places no hold — accepting a
`ledger` parameter here (even one that defaults to a no-op) would advertise
a capability this domain's read-only-by-construction shape doesn't have. No
CLI or `postgres_checkpointer` helper either — feature 005's supervisor
mounts this subgraph directly under `graph.nodes.GAMING`.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agents.gaming.edges.conditions import INTENT_PARSER, LOOKUP, route_after_lookup
from agents.gaming.nodes.intent_parser import IntentExtractor, make_intent_parser_node
from agents.gaming.nodes.lookup import make_lookup_node
from agents.gaming.state import GamingState
from agents.gaming.tools import GamingTools
from observability.telemetry import wrap_node

ALL_NODE_NAMES: frozenset[str] = frozenset({INTENT_PARSER, LOOKUP})


def build_gaming_graph(
    tools: GamingTools,
    *,
    intent_extractor: IntentExtractor | None = None,
    trace: bool = False,
) -> StateGraph[GamingState, Any, Any, Any]:
    """Assemble (but do not compile) the gaming `StateGraph`. `trace=True`
    wraps every node in a span via `observability.telemetry.wrap_node`
    (feature 007, ADR-041) — the first span emission this domain has ever
    had."""

    def _wrap(name: str, fn: Any) -> Any:
        return wrap_node(name, fn, domain="gaming") if trace else fn

    graph = StateGraph(GamingState)
    graph.add_node(INTENT_PARSER, _wrap(INTENT_PARSER, make_intent_parser_node(intent_extractor)))
    graph.add_node(LOOKUP, _wrap(LOOKUP, make_lookup_node(tools)))

    graph.add_edge(START, INTENT_PARSER)
    graph.add_edge(INTENT_PARSER, LOOKUP)
    graph.add_conditional_edges(LOOKUP, route_after_lookup, {END: END})
    return graph


def compile_gaming_graph(
    tools: GamingTools,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    intent_extractor: IntentExtractor | None = None,
    trace: bool = False,
) -> Any:
    """Compile the gaming subgraph. With no `checkpointer`, LangGraph uses
    its own ephemeral in-memory default."""
    graph = build_gaming_graph(tools, intent_extractor=intent_extractor, trace=trace)
    return graph.compile(checkpointer=checkpointer)
