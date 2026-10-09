"""T031 — the wellness subgraph: `StateGraph` over `WellnessState` (a
superset of the frozen `ResortFlowState`). Mirrors `agents.dining.graph`'s overall
shape (see that module's docstring). No dedicated CLI or
`postgres_checkpointer` helper here — feature 005's supervisor mounts this
subgraph directly under `graph.nodes.WELLNESS`; that wiring, and any
standalone runner, is a separate task's concern.

`trace` defaults to `False` here (unlike dining's `True`): no wellness
tracing module exists yet (`agents.dining.tracing` is dining-specific — its
`_span_attributes` hardcodes `"dining"` as the domain label, so reusing it
verbatim would mislabel every wellness span). `trace=True` is accepted for
call-site parity with `compile_dining_graph` but is currently a no-op; a
`agents/wellness/tracing.py` mirroring dining's module is a natural follow-up
once this subgraph is wired into the supervisor.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agents.wellness.edges.conditions import (
    AVAILABILITY,
    CONFIRM,
    HOLD_PLACEMENT,
    HUMAN_APPROVAL,
    INTENT_PARSER,
    SERVICE_DISCOVERY,
    make_human_approval_node,
    route_after_availability,
    route_after_hold_placement,
    route_after_human_approval,
    route_after_service_discovery,
)
from agents.wellness.nodes.availability import make_availability_node
from agents.wellness.nodes.confirm import make_confirm_node
from agents.wellness.nodes.hold_placement import make_hold_placement_node
from agents.wellness.nodes.intent_parser import IntentExtractor, make_intent_parser_node
from agents.wellness.nodes.service_discovery import make_service_discovery_node
from agents.wellness.state import WellnessState
from agents.wellness.tools import WellnessTools
from graph.ledger import HoldLedger, NullHoldLedger
from observability.telemetry import wrap_node

ALL_NODE_NAMES: frozenset[str] = frozenset(
    {
        INTENT_PARSER,
        SERVICE_DISCOVERY,
        AVAILABILITY,
        HOLD_PLACEMENT,
        HUMAN_APPROVAL,
        CONFIRM,
    }
)


def build_wellness_graph(
    tools: WellnessTools,
    *,
    intent_extractor: IntentExtractor | None = None,
    trace: bool = False,
    ledger: HoldLedger | None = None,
) -> StateGraph[WellnessState, Any, Any, Any]:
    """Assemble (but do not compile) the wellness `StateGraph`. `ledger`
    defaults to `NullHoldLedger()` — same graceful-degradation shape as
    `agents.dining.graph.build_dining_graph`'s `ledger` parameter: fast,
    `MemorySaver`-backed unit tests stay independent of Postgres. Callers
    that need the write-ahead guarantee pass a real `PostgresHoldLedger`
    explicitly.

    `trace=True` wraps every node in a span via
    `observability.telemetry.wrap_node` (feature 007, ADR-041) — the first
    span emission this domain has ever had."""
    ledger = ledger or NullHoldLedger()

    def _wrap(name: str, fn: Any) -> Any:
        return wrap_node(name, fn, domain="wellness") if trace else fn

    graph = StateGraph(WellnessState)
    graph.add_node(INTENT_PARSER, _wrap(INTENT_PARSER, make_intent_parser_node(intent_extractor)))
    graph.add_node(SERVICE_DISCOVERY, _wrap(SERVICE_DISCOVERY, make_service_discovery_node(tools)))
    graph.add_node(AVAILABILITY, _wrap(AVAILABILITY, make_availability_node(tools)))
    graph.add_node(HOLD_PLACEMENT, _wrap(HOLD_PLACEMENT, make_hold_placement_node(tools, ledger)))
    graph.add_node(HUMAN_APPROVAL, _wrap(HUMAN_APPROVAL, make_human_approval_node(tools, ledger)))
    graph.add_node(CONFIRM, _wrap(CONFIRM, make_confirm_node(tools, ledger)))

    graph.add_edge(START, INTENT_PARSER)
    graph.add_edge(INTENT_PARSER, SERVICE_DISCOVERY)
    graph.add_conditional_edges(
        SERVICE_DISCOVERY, route_after_service_discovery, {AVAILABILITY: AVAILABILITY, END: END}
    )
    graph.add_conditional_edges(
        AVAILABILITY, route_after_availability, {HOLD_PLACEMENT: HOLD_PLACEMENT, END: END}
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


def compile_wellness_graph(
    tools: WellnessTools,
    *,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    intent_extractor: IntentExtractor | None = None,
    trace: bool = False,
    ledger: HoldLedger | None = None,
) -> Any:
    """Compile the wellness subgraph. With no `checkpointer`, LangGraph uses
    its own ephemeral in-memory default — fine for a single `.invoke()`,
    unusable for a resume story. Same shape for `ledger`: no ledger means
    `NullHoldLedger` (see `build_wellness_graph`)."""
    graph = build_wellness_graph(tools, intent_extractor=intent_extractor, trace=trace, ledger=ledger)
    return graph.compile(checkpointer=checkpointer)
