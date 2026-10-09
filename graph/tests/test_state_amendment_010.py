"""Feature 010, T014 — the `ResortFlowState` amendment (ADR-056,
`specs/010-tiered-intent-routing/contracts/state-schema-amendment-010.md`):
`routing`/`turn_baseline`, additive, single-writer, no reducer.

Written first; must fail before T015 (`RoutingDecision`/`TurnBaseline` model
invariants) and T016 (serializer allowlist, `ResortFlowState` keys, `GRAPH_VERSION`
bump) exist."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agents.dining.serde import EncryptedSerializer, plain_serde
from graph.bookings import booking_view
from graph.models import (
    Confirmation,
    Domain,
    Hold,
    HoldStatus,
    Proposal,
    RequestKind,
    RoutingDecision,
    RoutingTier,
    Tier2Outcome,
    TurnBaseline,
)
from graph.state import ResortFlowState

# --- Resume safety: a pre-010 checkpoint lacks both keys --------------------


def _fresh_pre010_state() -> dict:
    """A `ResortFlowState`-shaped dict with no `routing`/`turn_baseline` key at
    all — exactly what a checkpoint written before this feature contains."""
    return {
        "tenant_id": "MGV",
        "guest_id": "guest-1",
        "session_id": "session-1",
        "jurisdiction": "NV",
        "entitlements": [],
        "saga_id": "saga-1",
        "messages": [],
        "plan": None,
        "replan_count": 0,
        "proposals": [],
        "holds": [],
        "confirmations": [],
        "failures": [],
        "retrieved": [],
        "citations": [],
        "awaiting_approval": False,
        "approval_scope": None,
        "policy_flags": [],
        "escalate": False,
        "saga_log": [],
        "graph_version": "5.0",
        "clarification_count": 0,
        "escalate_reason": None,
    }


def test_pre010_state_reads_both_new_keys_as_none() -> None:
    state = _fresh_pre010_state()
    assert state.get("routing") is None
    assert state.get("turn_baseline") is None


# --- Round-trip through both serializers, no pickle fallback ---------------


def _sample_decision() -> RoutingDecision:
    return RoutingDecision(
        kind=RequestKind.BOOK,
        services=[Domain.DINING],
        tier=RoutingTier.KIND_RULES,
        confidence=0.95,
        tier2_outcome=Tier2Outcome.NOT_CALLED,
    )


def _sample_baseline() -> TurnBaseline:
    return TurnBaseline(
        confirmation_keys=["k1"],
        hold_statuses={"k2": "held"},
        turn_index=3,
    )


@pytest.mark.parametrize(
    "serializer_factory",
    [
        lambda: plain_serde(),
        lambda: EncryptedSerializer(key=b"0" * 32),
    ],
    ids=["plain_serde", "EncryptedSerializer"],
)
def test_routing_decision_and_turn_baseline_round_trip_no_pickle_fallback(serializer_factory) -> None:
    serde = serializer_factory()
    decision = _sample_decision()
    baseline = _sample_baseline()

    typed = serde.dumps_typed(decision)
    restored = serde.loads_typed(typed)
    assert restored == decision

    typed_baseline = serde.dumps_typed(baseline)
    restored_baseline = serde.loads_typed(typed_baseline)
    assert restored_baseline == baseline


def test_turn_index_increments_by_one_per_routed_turn() -> None:
    first = TurnBaseline(turn_index=1)
    second = TurnBaseline(turn_index=first.turn_index + 1)
    assert second.turn_index == 2


# --- RoutingDecision invariants (data-model.md) -----------------------------


def test_undecided_kind_must_be_low_confidence_and_have_no_services() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(kind=None, confidence=0.9)
    with pytest.raises(ValidationError):
        RoutingDecision(kind=None, services=[Domain.DINING])
    # a valid undecided decision:
    RoutingDecision(kind=None, confidence=0.0, services=[])


def test_out_of_scope_and_smalltalk_cannot_carry_services() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(kind=RequestKind.OUT_OF_SCOPE, services=[Domain.DINING])
    with pytest.raises(ValidationError):
        RoutingDecision(kind=RequestKind.SMALLTALK, services=[Domain.WELLNESS])


def test_secondary_kind_only_valid_as_book_under_a_lookup_primary() -> None:
    RoutingDecision(kind=RequestKind.LOOKUP, secondary_kind=RequestKind.BOOK, secondary_services=[Domain.DINING])
    with pytest.raises(ValidationError):
        RoutingDecision(kind=RequestKind.BOOK, secondary_kind=RequestKind.BOOK)
    with pytest.raises(ValidationError):
        RoutingDecision(kind=RequestKind.LOOKUP, secondary_kind=RequestKind.MODIFY)


def test_tier2_requires_a_real_outcome() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(kind=RequestKind.BOOK, services=[Domain.DINING], tier=RoutingTier.LANGUAGE_MODEL, tier2_outcome=Tier2Outcome.NOT_CALLED)
    RoutingDecision(
        kind=RequestKind.BOOK, services=[Domain.DINING], tier=RoutingTier.LANGUAGE_MODEL, tier2_outcome=Tier2Outcome.DECIDED
    )


def test_gaming_never_coexists_with_modify() -> None:
    with pytest.raises(ValidationError):
        RoutingDecision(kind=RequestKind.MODIFY, services=[Domain.GAMING])


# --- ResortFlowState carries the two new keys, no reducer -------------------------


async def test_pre010_checkpoint_with_duplicated_confirmation_reports_once_and_never_as_this_turn() -> None:
    """Feature 010, T055: a pre-010 checkpoint (no `turn_baseline`) whose
    `confirmations` already contains today's duplication bug's output
    resumes and: (a) `booking_view` collapses the duplicate to one record
    (FR-033); (b) the reply composer's fallback path (no baseline) never
    claims anything as confirmed BY THIS TURN — it uses the lookup-style
    "here's where things stand" wording instead (FR-031)."""
    from graph.supervisor import _compose_respond_message

    proposal = Proposal(
        proposal_id="p1", domain=Domain.DINING, tenant_id="MGV", title="Silverline Chophouse",
        starts_at=datetime.now(UTC), party_size=4, hold_idempotency_key="k1",
    )
    hold = Hold(
        idempotency_key="k1", hold_id="h1", domain=Domain.DINING, tenant_id="MGV",
        saga_id="s1", status=HoldStatus.CONFIRMED,
    )
    duplicated_confirmation = Confirmation(
        confirmation_id="ABC123", idempotency_key="k1", domain=Domain.DINING, confirmed_at=datetime.now(UTC)
    )
    state = _fresh_pre010_state()
    state.update(
        proposals=[proposal],
        holds=[hold],
        # The exact shape of today's bug: the same booking appended twice.
        confirmations=[duplicated_confirmation, duplicated_confirmation],
        escalate=False,
        saga_log=[],
    )
    assert state.get("turn_baseline") is None  # pre-010: absent, not merely empty

    records = booking_view(state)
    assert len(records) == 1

    update = await _compose_respond_message(state)
    reply = update["messages"][0].content
    assert "here's where things stand" in reply.lower()
    assert "confirmed:" not in reply.lower()  # never framed as THIS turn's action
    assert reply.count("Silverline Chophouse") == 1


def test_resortflowstate_declares_routing_and_turn_baseline_with_no_reducer() -> None:
    hints = ResortFlowState.__annotations__
    assert "routing" in hints
    assert "turn_baseline" in hints
    # NotRequired[...] / plain type, never Annotated[..., reducer] — a
    # reducer would show up as a two-arg Annotated metadata tuple.
    assert not hasattr(hints["routing"], "__metadata__")
    assert not hasattr(hints["turn_baseline"], "__metadata__")
