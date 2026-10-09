"""T040/T041/T042 — User Story 5: a policy block on one domain in a
multi-domain itinerary doesn't block unrelated domains (FR-013, FR-014,
SC-005). Supervisor-level, matching `test_gaming_refusal.py`'s pattern."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from agents.dining.tests.fakes import FakeDiningTools
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.tests.conftest import thread_config
from graph.models import Domain
from graph.supervisor import compile_supervisor_graph, new_session_state


def _multi_turn_state(*, allergy_text: str, booking_text: str):
    """`Proposal.dietary` is built fresh from only the *latest* turn's
    parsed intent (`agents/dining/nodes/venue_discovery.py`) — a fidelity
    mismatch only arises when the allergy is stated in an *earlier* turn
    than the one that actually books. `new_session_state` only takes a
    single `text`, so this constructs the two-message history directly
    rather than round-tripping through a real CLARIFY interrupt/resume
    cycle (`_route_from_intent_router` reads only the latest human message
    for domain detection, so a single no-domain-keyword first message would
    otherwise route to CLARIFY — this sidesteps that entirely)."""
    state = new_session_state(tenant_id="MGV", guest_id="g1", text=booking_text)
    state["messages"] = [HumanMessage(content=allergy_text), HumanMessage(content=booking_text)]
    return state


@pytest.mark.asyncio
async def test_dining_allergen_block_excludes_only_dining_from_multi_domain_itinerary():
    """T040: check_dietary_fidelity now runs in the fanout path too (closing
    the gap this feature found where it never ran there at all) — a
    fidelity mismatch on the dining leg means no hold is ever placed for it,
    so `make_proposal_assembly_node`'s existing `held` filter excludes it
    for free; the entertainment leg proceeds through the ordinary
    cross-domain `HUMAN_APPROVAL` gate untouched."""
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    # Allergy stated in an earlier turn; the booking turn doesn't repeat
    # it — venue_discovery builds Proposal.dietary from only the latest
    # turn's intent, so this is a genuine conversation-vs-payload mismatch.
    state = _multi_turn_state(
        allergy_text="I have a severe shellfish allergy",
        booking_text="Book me dinner and get me tickets to a show tonight 2026-09-12",
    )
    result = await app.ainvoke(state, config=thread_config("granular-block-1"))

    flags = result.get("policy_flags", [])
    assert any(f.rule == "allergen_fidelity_mismatch" and f.disposition == "blocked" for f in flags)

    # Dining never reached HELD — hold_table was never called for it.
    assert "hold_table" not in dining.calls

    # Entertainment's own proposal, if one was found, should still be
    # eligible for the ordinary approval gate — not silently dropped by the
    # dining block.
    held_domains = {h.domain for h in result.get("holds", []) if h.status.value == "held"}
    assert Domain.DINING not in held_domains


@pytest.mark.asyncio
async def test_menu_content_conflict_also_blocks_the_dining_leg_in_fanout_path():
    """T053 (feature 006 convergence): menu-content matching — the *other*
    half of `agents/dining/nodes/allergen_check.py`'s check, previously
    missing from the fanout path entirely (T040 only added the fidelity
    half) — now also blocks a dining leg whose *structured* payload is
    faithful to the conversation (no fidelity mismatch) but whose venue's
    actual menu contains the allergen. Severe peanut allergy stated in the
    SAME turn as the booking (so `Proposal.dietary` faithfully carries it —
    no fidelity mismatch), against a japanese venue whose stub menu
    (`agents/dining/menu.py`) contains peanut."""
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text="table for 2 tomorrow at 7pm, japanese, severe peanut allergy, and get me tickets to a show tonight 2026-09-12",
    )
    result = await app.ainvoke(state, config=thread_config("menu-conflict-fanout-1"))

    flags = result.get("policy_flags", [])
    assert any(f.rule == "allergen_fidelity_mismatch" and f.disposition == "blocked" for f in flags)
    assert "hold_table" not in dining.calls
    held_domains = {h.domain for h in result.get("holds", []) if h.status.value == "held"}
    assert Domain.DINING not in held_domains


@pytest.mark.asyncio
async def test_allergen_and_self_exclusion_flags_both_survive_in_one_conversation():
    """T042a (FR-017): an allergen-fidelity block and a self-exclusion
    conflict both fire within the same guest interaction — one is neither
    resolved nor hidden in favor of the other. Gaming never participates in
    a multi-domain FanOut (it's read-only, never a PlanTask), so "the same
    interaction" here means the same conversation thread across two turns —
    both flags accumulate in `policy_flags` via its `append_only` reducer
    (ADR-029), regardless of which turn raised which."""
    from agents.gaming.tests.fakes import FakeGamingTools

    dining = FakeDiningTools()
    gaming = FakeGamingTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.GAMING: lambda t, g: gaming}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)
    config = thread_config("combined-flags-1")

    # Turn 1: self-excluded guest asks a gaming question.
    turn1 = new_session_state(
        tenant_id="MGV", guest_id="guest-self-excluded", text="what's on the casino floor tonight"
    )
    await app.ainvoke(turn1, config=config)

    # Turn 2 (same thread): allergy stated in an earlier message (turn 1 in
    # this pair's own sub-history), booking doesn't repeat it.
    turn2 = _multi_turn_state(
        allergy_text="I have a severe shellfish allergy",
        booking_text="table for 2 tonight at 7pm",
    )
    turn2["guest_id"] = "guest-self-excluded"
    result = await app.ainvoke(turn2, config=config)

    flags = result.get("policy_flags", [])
    assert any(f.rule == "self_exclusion_active" and f.disposition == "blocked" for f in flags)
    assert any(f.rule == "allergen_fidelity_mismatch" and f.disposition == "blocked" for f in flags)


@pytest.mark.asyncio
async def test_unblocked_domain_proceeds_through_ordinary_human_approval():
    """T041: the clean (non-blocked) leg of the same itinerary reaches the
    ordinary cross-domain HUMAN_APPROVAL gate and can be approved normally —
    the block on one leg doesn't turn into an all-or-nothing decision for
    the other."""
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = _multi_turn_state(
        allergy_text="I have a severe shellfish allergy",
        booking_text="Book me dinner and get me tickets to a show tonight 2026-09-12",
    )
    result = await app.ainvoke(state, config=thread_config("granular-block-2"))

    # Entertainment's own proposal/hold is unaffected by dining's block and
    # is part of the (dining-free) approval scope.
    ent_holds = [h for h in result.get("holds", []) if h.domain == Domain.ENTERTAINMENT and h.status.value == "held"]
    assert ent_holds, "expected entertainment to still hold a proposal despite dining's block"
    assert "dining" not in (result.get("approval_scope") or "").lower()
    assert "entertainment" in (result.get("approval_scope") or "").lower()
