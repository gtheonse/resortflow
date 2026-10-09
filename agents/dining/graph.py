"""T053/T054/T056 — the dining subgraph: `StateGraph` over `DiningState`
(a superset of the frozen `ResortFlowState`), Postgres checkpointing with
`EncryptedSerializer`, and Langfuse tracing per node.

No `graph/dining_entrypoint.py` exists (and none should — see plan.md):
this module is what feature 005's supervisor imports directly and mounts
under the frozen `graph.nodes.DINING` name. `build_dining_graph` returns
the *uncompiled* `StateGraph` so a caller controls its own checkpointer;
`compile_dining_graph` is the batteries-included version this package's
own standalone runner and tests use.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agents.dining.edges.conditions import (
    ALLERGEN_CHECK,
    AVAILABILITY,
    CONFIRM,
    HOLD_PLACEMENT,
    HUMAN_APPROVAL,
    INTENT_PARSER,
    VENUE_DISCOVERY,
    make_human_approval_node,
    route_after_allergen_check,
    route_after_availability,
    route_after_hold_placement,
    route_after_human_approval,
    route_after_venue_discovery,
)
from agents.dining.nodes.allergen_check import make_allergen_check_node
from agents.dining.nodes.availability import make_availability_node
from agents.dining.nodes.confirm import make_confirm_node
from agents.dining.nodes.hold_placement import make_hold_placement_node
from agents.dining.nodes.intent_parser import IntentExtractor, make_intent_parser_node
from agents.dining.nodes.venue_discovery import make_venue_discovery_node
from agents.dining.serde import EncryptedSerializer
from agents.dining.state import DiningState
from agents.dining.tools import DiningTools, McpDiningTools
from agents.dining.tracing import traced_node
from graph.ledger import HoldLedger, NullHoldLedger, PostgresHoldLedger

ALL_NODE_NAMES: frozenset[str] = frozenset(
    {
        INTENT_PARSER,
        VENUE_DISCOVERY,
        AVAILABILITY,
        ALLERGEN_CHECK,
        HOLD_PLACEMENT,
        HUMAN_APPROVAL,
        CONFIRM,
    }
)


def build_dining_graph(
    tools: DiningTools,
    *,
    intent_extractor: IntentExtractor | None = None,
    trace: bool = True,
    ledger: HoldLedger | None = None,
) -> StateGraph:
    """Assemble (but do not compile) the dining `StateGraph`. `trace=True`
    wraps every node with `agents.dining.tracing.traced_node` (a no-op when
    Langfuse isn't configured — see that module).

    `ledger` defaults to `NullHoldLedger()` — same shape of graceful
    degradation as the `checkpointer` parameter (`compile_dining_graph`'s
    "no checkpointer -> LangGraph's own ephemeral default" applies
    identically here: a `NullHoldLedger` default keeps fast,
    `MemorySaver`-backed unit tests independent of Postgres). Callers that
    need the write-ahead guarantee this exists for (production, and
    `agents/dining/eval/test_resume.py`) pass a real `PostgresHoldLedger`
    explicitly."""
    ledger = ledger or NullHoldLedger()

    def _n(name: str, fn):
        return traced_node(name, fn) if trace else fn

    graph = StateGraph(DiningState)
    graph.add_node(INTENT_PARSER, _n(INTENT_PARSER, make_intent_parser_node(intent_extractor)))
    graph.add_node(VENUE_DISCOVERY, _n(VENUE_DISCOVERY, make_venue_discovery_node(tools)))
    graph.add_node(AVAILABILITY, _n(AVAILABILITY, make_availability_node(tools)))
    graph.add_node(ALLERGEN_CHECK, _n(ALLERGEN_CHECK, make_allergen_check_node()))
    graph.add_node(HOLD_PLACEMENT, _n(HOLD_PLACEMENT, make_hold_placement_node(tools, ledger)))
    graph.add_node(HUMAN_APPROVAL, _n(HUMAN_APPROVAL, make_human_approval_node(tools, ledger)))
    graph.add_node(CONFIRM, _n(CONFIRM, make_confirm_node(tools, ledger)))

    graph.add_edge(START, INTENT_PARSER)
    graph.add_edge(INTENT_PARSER, VENUE_DISCOVERY)
    graph.add_conditional_edges(
        VENUE_DISCOVERY, route_after_venue_discovery, {AVAILABILITY: AVAILABILITY, END: END}
    )
    graph.add_conditional_edges(
        AVAILABILITY, route_after_availability, {ALLERGEN_CHECK: ALLERGEN_CHECK, END: END}
    )
    graph.add_conditional_edges(
        ALLERGEN_CHECK, route_after_allergen_check, {HOLD_PLACEMENT: HOLD_PLACEMENT, END: END}
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


def compile_dining_graph(
    tools: DiningTools,
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    intent_extractor: IntentExtractor | None = None,
    trace: bool = True,
    ledger: HoldLedger | None = None,
):
    """Compile the dining subgraph. With no `checkpointer`, LangGraph uses
    its own ephemeral in-memory default — fine for a single `.invoke()`,
    unusable for the resume story T054/T055 exist to prove. Same shape for
    `ledger`: no ledger means `NullHoldLedger` (see `build_dining_graph`)."""
    graph = build_dining_graph(tools, intent_extractor=intent_extractor, trace=trace, ledger=ledger)
    return graph.compile(checkpointer=checkpointer)


# --- Postgres checkpointer (T054) --------------------------------------------


@asynccontextmanager
async def postgres_checkpointer(conn_string: str | None = None):
    """Yields an `AsyncPostgresSaver` wrapped in `EncryptedSerializer`
    (AGENTS.md tech stack). Async, not sync `PostgresSaver` — this
    subgraph's nodes are async (`ainvoke`/MCP tool calls), and
    `PostgresSaver`'s sync implementation doesn't implement the `a*`
    checkpoint methods `ainvoke` requires (raises `NotImplementedError`).

    No fallback when `LANGGRAPH_AES_KEY` is unconfigured: `EncryptedSerializer
    .from_env()` is deliberately built to refuse construction rather than
    silently write plaintext (see its own module docstring — "a durability
    feature that silently drops its own guarantee is worse than one that
    fails loudly at startup"). An earlier version of this function caught
    that refusal and substituted `serde=None` anyway, defeating it —
    Constitution Principle V requires checkpoints be encrypted
    unconditionally, with no local-dev carve-out, so this now lets
    `EncryptedSerializerConfigError` propagate. Generate a key
    (`openssl rand -base64 32` into `LANGGRAPH_AES_KEY`, per
    `.env.example`) rather than running without one.
    """
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    conn_string = conn_string or os.environ["POSTGRES_URL"]
    serde = EncryptedSerializer.from_env()

    async with AsyncPostgresSaver.from_conn_string(conn_string, serde=serde) as saver:
        await saver.setup()
        yield saver


# --- Standalone runner (Phase 4 has no supervisor yet) -----------------------


def new_session_state(
    *, tenant_id: str, guest_id: str, session_id: str | None = None, text: str
) -> DiningState:
    """Seed a fresh `DiningState` the way a real conversation entry would:
    identity fields set once, never re-derived downstream (AGENTS.md hard
    rule — never derive `tenant_id` from model output)."""
    return DiningState(
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
    )


async def run_booking(
    text: str,
    *,
    tenant_id: str,
    guest_id: str,
    session_id: str | None = None,
    tools: DiningTools | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
    ledger: HoldLedger | None = None,
) -> dict:
    """One-shot convenience entry point — the "thin standalone runner"
    plan.md calls for so the vertical slice is exercisable before
    feature 005's supervisor exists."""
    tools = tools or McpDiningTools(tenant_id=tenant_id, guest_id=guest_id)
    app = compile_dining_graph(tools, checkpointer=checkpointer, ledger=ledger)
    state = new_session_state(
        tenant_id=tenant_id, guest_id=guest_id, session_id=session_id, text=text
    )
    thread_id = state["session_id"]
    return await app.ainvoke(state, config={"configurable": {"thread_id": thread_id}})


def _cli() -> None:  # pragma: no cover — operator convenience
    import argparse

    parser = argparse.ArgumentParser(prog="python -m agents.dining.graph")
    parser.add_argument("text", help="Guest's dining request, e.g. 'table for 4 tonight at 7pm'")
    parser.add_argument("--tenant", default="MGV", choices=["MGV", "MHB"])
    parser.add_argument("--guest", default="demo-guest")
    args = parser.parse_args()

    async def _run():
        async with postgres_checkpointer() as checkpointer:
            result = await run_booking(
                args.text,
                tenant_id=args.tenant,
                guest_id=args.guest,
                checkpointer=checkpointer,
                ledger=PostgresHoldLedger(),
            )
        print(f"proposals: {len(result.get('proposals', []))}")
        print(f"holds: {[h.status.value for h in result.get('holds', [])]}")
        print(f"confirmations: {len(result.get('confirmations', []))}")
        if result.get("awaiting_approval"):
            print(f"AWAITING APPROVAL: {result.get('approval_scope')}")

    asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover
    _cli()
