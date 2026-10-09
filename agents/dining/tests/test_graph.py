"""T047-T053 — subgraph-level tests against `FakeDiningTools`. Each test
exercises the compiled graph end-to-end (no live sims/gateway required),
which is what actually proves the node wiring, reducers, and conditional
edges agree with each other."""

from __future__ import annotations

from datetime import date

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.dining.graph import compile_dining_graph, new_session_state
from agents.dining.nodes.intent_parser import RuleBasedIntentExtractor
from agents.dining.tests.fakes import FakeDiningTools
from agents.dining.tools import DiningToolError
from graph.models import HoldStatus

TODAY = date(2026, 9, 11)


def make_extractor():
    class _Fixed(RuleBasedIntentExtractor):
        def extract(self, text, *, today):
            return super().extract(text, today=TODAY)

    return _Fixed()


@pytest.mark.asyncio
async def test_happy_path_produces_confirmation():
    tools = FakeDiningTools()
    app = compile_dining_graph(tools, intent_extractor=make_extractor(), trace=False)
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="table for 4 tomorrow at 7pm, steakhouse"
    )
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "happy-1"}})

    assert len(result["proposals"]) == 1
    assert len(result["confirmations"]) == 1
    assert result["holds"][0].status == HoldStatus.CONFIRMED
    assert result["awaiting_approval"] is False
    assert "hold_table" in tools.calls and "confirm_reservation" in tools.calls


@pytest.mark.asyncio
async def test_severe_allergen_conflict_blocks_before_any_hold():
    """Feature 006 (ADR-029, FR-006): a severe allergen conflict is now
    non-overridable — it routes straight to END, never through
    `human_approval`'s interrupt, since that block can never be approved
    past. `policy_blocked=True` replaces the old `awaiting_approval=True`
    signal for this specific case."""
    tools = FakeDiningTools()
    app = compile_dining_graph(tools, intent_extractor=make_extractor(), trace=False)
    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text="table for 2 tomorrow at 7pm, japanese, severe peanut allergy",
    )
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "conflict-1"}})

    assert result["policy_blocked"] is True
    assert result["awaiting_approval"] is False
    assert result["dietary_conflicts"]
    # ADR-061: blocked before hold_placement — no vendor inventory is tied up.
    assert result["holds"] == []
    assert "hold_table" not in tools.calls
    assert result["confirmations"] == []
    assert not result.get("__interrupt__")  # never reaches human_approval's interrupt
    kinds = {e.kind for e in result["saga_log"]}
    assert "dietary_conflict_flagged" in kinds
    flags = result.get("policy_flags", [])
    assert any(f.rule == "allergen_fidelity_mismatch" and f.disposition == "blocked" for f in flags)
    assert any(f.rule == "allergen_fidelity_mismatch" and f.disposition == "escalated" for f in flags)
    assert "can't confirm this booking yet" in result["messages"][-1].content


@pytest.mark.asyncio
async def test_preference_conflict_is_informational_and_does_not_block():
    tools = FakeDiningTools()
    app = compile_dining_graph(tools, intent_extractor=make_extractor(), trace=False)
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="table for 2 tomorrow at 7pm, vegetarian, japanese"
    )
    # 'japanese' + vegetarian preference: Sakura's menu has no vegetarian keyword hit
    # in the stub corpus, so there is no conflict object at all — proceeds straight
    # through to confirmation, proving PREFERENCE never blocks even when present.
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "pref-1"}})
    assert result["awaiting_approval"] is False
    assert len(result["confirmations"]) == 1


@pytest.mark.asyncio
async def test_approving_past_a_severe_conflict_has_no_effect():
    """T027 [CRIT] — the concrete regression this feature closes (ADR-029):
    before this fix, `human_approval` could be resumed with
    `{"approved": True}` and the booking would confirm despite a severe
    allergen conflict. Now the graph never pauses for approval on this path
    at all — the first `ainvoke` already runs to completion (no
    `__interrupt__`), so attempting to resume with `{"approved": True}`
    anyway (simulating a misconfigured HITL client) must have no effect:
    there is nothing pending to resume, and no confirmation must ever
    appear."""
    tools = FakeDiningTools()
    checkpointer = MemorySaver()
    app = compile_dining_graph(
        tools, checkpointer=checkpointer, intent_extractor=make_extractor(), trace=False
    )
    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text="table for 2 tomorrow at 7pm, japanese, severe peanut allergy",
    )
    config = {"configurable": {"thread_id": "resume-approve-1"}}
    first = await app.ainvoke(state, config=config)
    assert first["policy_blocked"] is True
    assert not first.get("__interrupt__")
    assert first["confirmations"] == []

    # Attempt to resume anyway — there is no pending interrupt on this
    # thread, so this must not confirm the booking.
    second = await app.ainvoke(Command(resume={"approved": True}), config=config)
    assert second["confirmations"] == []
    assert second["holds"] == []


@pytest.mark.asyncio
async def test_human_approval_reject_path_still_releases_hold_as_a_unit():
    """Feature 006 (ADR-029): `make_human_approval_node`'s reject-then-
    release logic is no longer reachable through the real dining graph's own
    routing (allergen_check's only blocking case is now non-overridable and
    never sets `awaiting_approval`) — but the node itself is deliberately
    kept, not deleted, for a future feature that adds a genuinely-overridable
    block type. This test preserves coverage of that still-live logic via a
    minimal synthetic graph, decoupled from dining's real topology, the same
    way `tests/test_reducers.py` builds throwaway graphs to test reducer
    wiring in isolation."""
    from langgraph.graph import END, START, StateGraph

    from agents.dining.edges.conditions import HUMAN_APPROVAL, make_human_approval_node
    from agents.dining.state import DiningState
    from graph.ledger import NullHoldLedger
    from graph.models import Domain, Hold, Proposal

    tools = FakeDiningTools()
    ledger = NullHoldLedger()

    def _force_awaiting_approval(state: DiningState) -> dict:
        return {"awaiting_approval": True}

    builder = StateGraph(DiningState)
    builder.add_node("force", _force_awaiting_approval)
    builder.add_node(HUMAN_APPROVAL, make_human_approval_node(tools, ledger))
    builder.add_edge(START, "force")
    builder.add_edge("force", HUMAN_APPROVAL)
    builder.add_edge(HUMAN_APPROVAL, END)
    checkpointer = MemorySaver()
    app = builder.compile(checkpointer=checkpointer)

    from datetime import UTC, datetime, timedelta

    held_hold = Hold(
        idempotency_key="synthetic-key-1", hold_id="vendor-hold-1", domain=Domain.DINING,
        tenant_id="MGV", saga_id="s1", status=HoldStatus.HELD,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    proposal = Proposal(
        proposal_id="p1", domain=Domain.DINING, tenant_id="MGV", title="Venue",
        hold_idempotency_key="synthetic-key-1",
    )
    state = new_session_state(tenant_id="MGV", guest_id="g1", text="table for 2")
    state["proposals"] = [proposal]
    state["holds"] = [held_hold]
    state["selected_proposal_id"] = "p1"
    config = {"configurable": {"thread_id": "reject-unit-1"}}

    first = await app.ainvoke(state, config=config)
    assert first.get("__interrupt__")

    second = await app.ainvoke(Command(resume={"approved": False}), config=config)
    resolved = next(h for h in second["holds"] if h.idempotency_key == "synthetic-key-1")
    assert resolved.status == HoldStatus.RELEASED
    assert "release_hold" in tools.calls


@pytest.mark.asyncio
async def test_no_availability_joins_waitlist_and_never_holds():
    tools = FakeDiningTools(available_times=[])
    app = compile_dining_graph(tools, intent_extractor=make_extractor(), trace=False)
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="table for 4 tomorrow at 7pm, steakhouse"
    )
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "waitlist-1"}})

    assert result["waitlisted"] is True
    assert result["holds"] == []
    assert result["confirmations"] == []
    assert "join_waitlist" in tools.calls
    assert "hold_table" not in tools.calls


@pytest.mark.asyncio
async def test_zero_matching_venues_ends_gracefully_with_suggestion_message():
    tools = FakeDiningTools(venues=[])
    app = compile_dining_graph(tools, intent_extractor=make_extractor(), trace=False)
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="table for 4 tomorrow at 7pm, steakhouse"
    )
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "zero-1"}})

    assert result["proposals"] == []
    assert result["selected_proposal_id"] is None
    assert "broadening" in result["messages"][-1].content


@pytest.mark.asyncio
async def test_availability_terminal_failure_classified_and_falls_back_to_waitlist():
    tools = FakeDiningTools(
        fail_availability=DiningToolError(
            "party size out of range", vendor_code="INVALID_PARTY_SIZE", http_status=400
        )
    )
    app = compile_dining_graph(tools, intent_extractor=make_extractor(), trace=False)
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="table for 4 tomorrow at 7pm, steakhouse"
    )
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "fail-avail-1"}})

    assert len(result["failures"]) == 1
    assert result["failures"][0].error_class.value == "TERMINAL"
    assert result["waitlisted"] is True


@pytest.mark.asyncio
async def test_hold_placement_failure_is_classified_and_never_confirms():
    """eval/saga_suite.py's dining/hold cell (T032 matrix audit) — mirrors
    `agents/wellness/tests/test_graph.py`'s
    `test_hold_placement_failure_is_classified_and_never_confirms`: the
    vendor rejects `hold_table` itself (slot taken between availability-check
    and hold), distinct from `fail_availability` above (which fails one step
    earlier) and from `hold_expired_on_revalidate` below (which fails at
    confirm-time revalidation of an already-placed hold)."""
    tools = FakeDiningTools(
        fail_hold=DiningToolError("table taken", vendor_code="SLOT_UNAVAILABLE", http_status=409)
    )
    app = compile_dining_graph(tools, intent_extractor=make_extractor(), trace=False)
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="table for 4 tomorrow at 7pm, steakhouse"
    )
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "fail-hold-1"}})

    assert result["confirmations"] == []
    assert result["holds"][0].status == HoldStatus.RELEASED
    assert result["failures"][0].error_class.value == "TERMINAL"
    assert "confirm_reservation" not in tools.calls


@pytest.mark.asyncio
async def test_hold_expired_at_confirm_time_escalates_requires_human():
    tools = FakeDiningTools(hold_expired_on_revalidate=True)
    app = compile_dining_graph(tools, intent_extractor=make_extractor(), trace=False)
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="table for 4 tomorrow at 7pm, steakhouse"
    )
    result = await app.ainvoke(state, config={"configurable": {"thread_id": "hold-expired-1"}})

    assert result["confirmations"] == []
    assert result["awaiting_approval"] is True
    assert result["approval_scope"].startswith("hold_expired:")
    assert result["failures"][0].error_class.value == "REQUIRES_HUMAN"
    assert result["holds"][0].status == HoldStatus.EXPIRED


def test_graph_compiles_with_no_orphan_nodes():
    tools = FakeDiningTools()
    app = compile_dining_graph(tools, intent_extractor=make_extractor(), trace=False)
    graph_repr = app.get_graph()
    node_names = set(graph_repr.nodes) - {"__start__", "__end__"}
    from agents.dining.graph import ALL_NODE_NAMES

    assert node_names == ALL_NODE_NAMES
    # every node other than the terminal one has at least one outgoing edge
    sources = {edge.source for edge in graph_repr.edges}
    for name in ALL_NODE_NAMES - {"confirm"}:
        assert name in sources
