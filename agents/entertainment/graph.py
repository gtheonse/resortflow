"""T027 — the entertainment subgraph: `StateGraph` over `EntertainmentState`
(a superset of the frozen `ResortFlowState`). Mirrors
`agents/dining/graph.py`'s `build_dining_graph`/`compile_dining_graph`
shape.

No CLI, no `postgres_checkpointer` helper here (unlike dining's graph.py) —
per this task's scope, the supervisor built by a separate task owns
checkpointing and mounts this subgraph under the frozen `graph.nodes.
ENTERTAINMENT` name; this module only needs to build and export the compile
function.
"""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agents.entertainment.edges.conditions import (
    EVENT_DISCOVERY,
    HOLD_PLACEMENT,
    HUMAN_APPROVAL,
    INTENT_PARSER,
    INVENTORY_CHECK,
    PURCHASE,
    make_human_approval_node,
    route_after_event_discovery,
    route_after_hold_placement,
    route_after_human_approval,
    route_after_inventory_check,
)
from agents.entertainment.nodes.confirm import make_confirm_node
from agents.entertainment.nodes.event_discovery import make_event_discovery_node
from agents.entertainment.nodes.hold_placement import make_hold_placement_node
from agents.entertainment.nodes.intent_parser import IntentExtractor, make_intent_parser_node
from agents.entertainment.nodes.inventory_check import make_inventory_check_node
from agents.entertainment.state import EntertainmentState
from agents.entertainment.tools import EntertainmentTools
from graph.ledger import HoldLedger, NullHoldLedger
from observability.telemetry import wrap_node

ALL_NODE_NAMES: frozenset[str] = frozenset(
    {
        INTENT_PARSER,
        EVENT_DISCOVERY,
        INVENTORY_CHECK,
        HOLD_PLACEMENT,
        HUMAN_APPROVAL,
        PURCHASE,
    }
)


def build_entertainment_graph(
    tools: EntertainmentTools,
    *,
    intent_extractor: IntentExtractor | None = None,
    trace: bool = False,
    ledger: HoldLedger | None = None,
) -> StateGraph[EntertainmentState, Any, Any, Any]:
    """Assemble (but do not compile) the entertainment `StateGraph`.

    `trace=True` wraps every node in a span via
    `observability.telemetry.wrap_node` (feature 007, ADR-041) — the first
    span emission this domain has ever had.

    `ledger` defaults to `NullHoldLedger()` — same graceful-degradation
    shape `build_dining_graph` uses, keeping fast `MemorySaver`-backed unit
    tests independent of Postgres."""
    ledger = ledger or NullHoldLedger()

    def _wrap(name: str, fn: Any) -> Any:
        return wrap_node(name, fn, domain="entertainment") if trace else fn

    graph = StateGraph(EntertainmentState)
    graph.add_node(INTENT_PARSER, _wrap(INTENT_PARSER, make_intent_parser_node(intent_extractor)))
    graph.add_node(EVENT_DISCOVERY, _wrap(EVENT_DISCOVERY, make_event_discovery_node(tools)))
    graph.add_node(INVENTORY_CHECK, _wrap(INVENTORY_CHECK, make_inventory_check_node(tools)))
    graph.add_node(HOLD_PLACEMENT, _wrap(HOLD_PLACEMENT, make_hold_placement_node(tools, ledger)))
    graph.add_node(HUMAN_APPROVAL, _wrap(HUMAN_APPROVAL, make_human_approval_node(tools, ledger)))
    graph.add_node(PURCHASE, _wrap(PURCHASE, make_confirm_node(tools, ledger)))

    graph.add_edge(START, INTENT_PARSER)
    graph.add_edge(INTENT_PARSER, EVENT_DISCOVERY)
    graph.add_conditional_edges(
        EVENT_DISCOVERY, route_after_event_discovery, {INVENTORY_CHECK: INVENTORY_CHECK, END: END}
    )
    graph.add_conditional_edges(
        INVENTORY_CHECK, route_after_inventory_check, {HOLD_PLACEMENT: HOLD_PLACEMENT, END: END}
    )
    graph.add_conditional_edges(
        HOLD_PLACEMENT,
        route_after_hold_placement,
        {HUMAN_APPROVAL: HUMAN_APPROVAL, END: END},
    )
    graph.add_conditional_edges(
        HUMAN_APPROVAL, route_after_human_approval, {PURCHASE: PURCHASE, END: END}
    )
    graph.add_edge(PURCHASE, END)
    return graph


def compile_entertainment_graph(
    tools: EntertainmentTools,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    intent_extractor: IntentExtractor | None = None,
    trace: bool = False,
    ledger: HoldLedger | None = None,
) -> Any:
    """Compile the entertainment subgraph. With no `checkpointer`, LangGraph
    uses its own ephemeral in-memory default — fine for a single
    `.invoke()`, unusable for a resume story. Same shape for `ledger`: no
    ledger means `NullHoldLedger` (see `build_entertainment_graph`)."""
    graph = build_entertainment_graph(
        tools, intent_extractor=intent_extractor, trace=trace, ledger=ledger
    )
    return graph.compile(checkpointer=checkpointer)


def new_session_state(
    *, tenant_id: str, guest_id: str, session_id: str | None = None, text: str
) -> EntertainmentState:
    """Seed a fresh `EntertainmentState` the way a real conversation entry
    would: identity fields set once, never re-derived downstream (AGENTS.md
    hard rule — never derive `tenant_id` from model output). A test
    convenience mirroring `agents.dining.graph.new_session_state`."""
    return EntertainmentState(
        tenant_id=tenant_id,
        guest_id=guest_id,
        session_id=session_id or uuid.uuid4().hex,
        jurisdiction="NV" if tenant_id == "MGV" else "MA",
        entitlements=[],
        saga_id=uuid.uuid4().hex,
        messages=[HumanMessage(content=text)],
        plan=None,
        replan_count=0,
        proposals=[],
        holds=[],
        confirmations=[],
        failures=[],
        retrieved=[],
        citations=[],
        awaiting_approval=False,
        approval_scope=None,
        policy_flags=[],
        escalate=False,
        saga_log=[],
        graph_version="5.0",
        clarification_count=0,
        escalate_reason=None,
        routing=None,
        turn_baseline=None,
    )
