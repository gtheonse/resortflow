"""T053/T054 (stay) — the stay subgraph: `StateGraph` over `StayState` (a
superset of the frozen `ResortFlowState`). Mirrors `agents.dining.graph`'s and
`agents.wellness.graph`'s overall shape (see those modules' docstrings). No
dedicated CLI or `postgres_checkpointer` helper here — feature 005's
supervisor mounts this subgraph directly under `graph.nodes.STAY`; that
wiring, and any standalone runner, is a separate task's concern.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agents.stay.edges.conditions import (
    CONFIRM,
    HOLD_PLACEMENT,
    HUMAN_APPROVAL,
    INTENT_PARSER,
    ROOM_DISCOVERY,
    make_human_approval_node,
    route_after_hold_placement,
    route_after_human_approval,
    route_after_room_discovery,
)
from agents.stay.nodes.confirm import make_confirm_node
from agents.stay.nodes.hold_placement import make_hold_placement_node
from agents.stay.nodes.intent_parser import IntentExtractor, make_intent_parser_node
from agents.stay.nodes.room_discovery import make_room_discovery_node
from agents.stay.state import StayState
from agents.stay.tools import StayTools
from graph.ledger import HoldLedger, NullHoldLedger
from observability.telemetry import wrap_node

ALL_NODE_NAMES: frozenset[str] = frozenset(
    {
        INTENT_PARSER,
        ROOM_DISCOVERY,
        HOLD_PLACEMENT,
        HUMAN_APPROVAL,
        CONFIRM,
    }
)


def build_stay_graph(
    tools: StayTools,
    *,
    intent_extractor: IntentExtractor | None = None,
    trace: bool = False,
    ledger: HoldLedger | None = None,
) -> StateGraph[StayState, Any, Any, Any]:
    """Assemble (but do not compile) the stay `StateGraph`. `ledger` defaults
    to `NullHoldLedger()` — same graceful-degradation shape as
    `agents.dining.graph.build_dining_graph`'s `ledger` parameter: fast,
    `MemorySaver`-backed unit tests stay independent of Postgres. Callers
    that need the write-ahead guarantee pass a real `PostgresHoldLedger`
    explicitly.

    `trace=True` wraps every node in a span via
    `observability.telemetry.wrap_node` (feature 007, ADR-041) — the first
    span emission this domain has ever had."""
    ledger = ledger or NullHoldLedger()

    def _wrap(name: str, fn: Any) -> Any:
        return wrap_node(name, fn, domain="stay") if trace else fn

    graph = StateGraph(StayState)
    graph.add_node(INTENT_PARSER, _wrap(INTENT_PARSER, make_intent_parser_node(intent_extractor)))
    graph.add_node(ROOM_DISCOVERY, _wrap(ROOM_DISCOVERY, make_room_discovery_node(tools)))
    graph.add_node(HOLD_PLACEMENT, _wrap(HOLD_PLACEMENT, make_hold_placement_node(tools, ledger)))
    graph.add_node(HUMAN_APPROVAL, _wrap(HUMAN_APPROVAL, make_human_approval_node(tools, ledger)))
    graph.add_node(CONFIRM, _wrap(CONFIRM, make_confirm_node(tools, ledger)))

    graph.add_edge(START, INTENT_PARSER)
    graph.add_edge(INTENT_PARSER, ROOM_DISCOVERY)
    graph.add_conditional_edges(
        ROOM_DISCOVERY, route_after_room_discovery, {HOLD_PLACEMENT: HOLD_PLACEMENT, END: END}
    )
    graph.add_conditional_edges(
        HOLD_PLACEMENT,
        route_after_hold_placement,
        {HUMAN_APPROVAL: HUMAN_APPROVAL, CONFIRM: CONFIRM, END: END},
    )
    graph.add_conditional_edges(
        HUMAN_APPROVAL, route_after_human_approval, {CONFIRM: CONFIRM, END: END}
    )
    graph.add_edge(CONFIRM, END)
    return graph


def compile_stay_graph(
    tools: StayTools,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    intent_extractor: IntentExtractor | None = None,
    trace: bool = False,
    ledger: HoldLedger | None = None,
) -> Any:
    """Compile the stay subgraph. With no `checkpointer`, LangGraph uses its
    own ephemeral in-memory default — fine for a single `.invoke()`,
    unusable for a resume story. Same shape for `ledger`: no ledger means
    `NullHoldLedger` (see `build_stay_graph`)."""
    graph = build_stay_graph(tools, intent_extractor=intent_extractor, trace=trace, ledger=ledger)
    return graph.compile(checkpointer=checkpointer)
