"""Constitution Principle II: "a tenant-crossing call MUST be impossible by
construction, proven by test rather than asserted." The convergence audit
(F4-5) found no test in this domain proved that — every existing test used
`tenant_id="MGV"` only. This file is that proof, mirroring the pattern
`mcp/tests/integration/test_surface_uniformity.py` already established for
the MCP tool layer (no tool input model accepts `tenant_id`), extended to
the dining subgraph's own tool Protocol and its actual runtime behaviour.
"""

from __future__ import annotations

import inspect
from datetime import date

import pytest

from agents.dining.graph import compile_dining_graph, new_session_state
from agents.dining.nodes.intent_parser import RuleBasedIntentExtractor
from agents.dining.tests.fakes import FakeDiningTools
from agents.dining.tools import DiningTools

TODAY = date(2026, 9, 11)


def make_extractor():
    class _Fixed(RuleBasedIntentExtractor):
        def extract(self, text, *, today):
            return super().extract(text, today=TODAY)

    return _Fixed()


# --- Structural: impossible by construction, not just untested -------------


def test_no_dining_tool_method_accepts_tenant_id():
    """Every method on the `DiningTools` Protocol — the graph's only path
    to a vendor — must lack a `tenant_id` parameter. Tenant identity is
    fixed at tool construction (`McpDiningTools(tenant_id=...)` /
    `FakeDiningTools(tenant_id=...)`), never passed per-call, so no graph
    node (and no LLM-influenced value) can select a different tenant for a
    single call."""
    for name, member in inspect.getmembers(DiningTools):
        if name.startswith("_"):
            continue
        sig = inspect.signature(member)
        assert "tenant_id" not in sig.parameters, (
            f"DiningTools.{name} must not accept tenant_id — it would let a "
            f"call target a tenant other than the one bound to this graph run"
        )


# --- Behavioral: running two tenants side by side never cross-leaks --------


@pytest.mark.asyncio
async def test_venue_discovery_never_returns_other_tenants_venues():
    """`FakeDiningTools` is seeded with both MGV and MHB venues (mirroring
    real production data — one Qdrant/Postgres instance holds both
    tenants). A run bound to MGV must never see an MHB venue in its
    proposals, and vice versa — this is the behavioral half of the
    structural proof above."""
    for tenant_id, other_tenant_prefix in (("MGV", "ven-mhb-"), ("MHB", "ven-mgv-")):
        tools = FakeDiningTools(tenant_id=tenant_id)
        app = compile_dining_graph(tools, intent_extractor=make_extractor(), trace=False)
        state = new_session_state(
            tenant_id=tenant_id, guest_id="g1", text="table for 4 tomorrow at 7pm"
        )
        result = await app.ainvoke(
            state, config={"configurable": {"thread_id": f"isolation-{tenant_id}"}}
        )

        for proposal in result["proposals"]:
            assert proposal.tenant_id == tenant_id, (
                f"proposal {proposal.proposal_id} carries tenant_id="
                f"{proposal.tenant_id!r} in a {tenant_id} run"
            )
            assert not proposal.proposal_id.split(":")[-1].startswith(other_tenant_prefix), (
                f"{tenant_id} run surfaced a venue belonging to the other tenant: "
                f"{proposal.proposal_id}"
            )


@pytest.mark.asyncio
async def test_hold_and_confirmation_tenant_id_always_matches_session_tenant():
    """`Hold.tenant_id`/the session's `tenant_id` must always match
    `state["tenant_id"]` — set once at entry from identity (AGENTS.md hard
    rule) — never anything a tool response or the LLM-driven intent parser
    could influence."""
    tools = FakeDiningTools(tenant_id="MHB")
    app = compile_dining_graph(tools, intent_extractor=make_extractor(), trace=False)
    state = new_session_state(
        tenant_id="MHB", guest_id="g1", text="table for 2 tomorrow at 7pm, seafood"
    )
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "isolation-hold"}})

    assert result["holds"], "expected at least one hold to have been placed"
    for hold in result["holds"]:
        assert hold.tenant_id == "MHB"
