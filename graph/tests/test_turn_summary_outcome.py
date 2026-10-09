"""Feature 010, T048 — `summarize_turn`'s additive `booking_outcome` field
(FR-035): the field the console badge (T054) and `graph/api.py` both read,
so "Confirmed" can never again mean merely "the resume didn't raise"
(analyze finding G1)."""

from __future__ import annotations

from datetime import UTC, datetime

from graph.models import Confirmation, Domain, Hold, HoldStatus, Proposal, TurnBaseline
from graph.turn_summary import summarize_turn


def _base_state(**overrides) -> dict:
    state = {
        "messages": [],
        "proposals": [],
        "holds": [],
        "confirmations": [],
        "failures": [],
        "policy_flags": [],
        "saga_log": [],
        "escalate": False,
        "turn_baseline": TurnBaseline(confirmation_keys=[], hold_statuses={}, turn_index=1),
    }
    state.update(overrides)
    return state


def test_existing_keys_are_unchanged() -> None:
    result = summarize_turn(_base_state(), thread_id="t1", node_order=[])
    for key in ("reply", "node_order", "tool_calls", "holds", "guardrail_verdicts", "escalate", "escalate_reason", "interrupt"):
        assert key in result


def test_escalated_turn_is_handed_off() -> None:
    result = summarize_turn(_base_state(escalate=True), thread_id="t1", node_order=[])
    assert result["booking_outcome"]["state"] == "handed_off"


def test_pending_interrupt_is_awaiting_input() -> None:
    interrupt_payload = [type("I", (), {"value": {"question": "which service?"}})()]
    result = summarize_turn(_base_state(__interrupt__=interrupt_payload), thread_id="t1", node_order=[])
    assert result["booking_outcome"]["state"] == "awaiting_input"


def test_nothing_booked_when_no_outcomes() -> None:
    result = summarize_turn(_base_state(), thread_id="t1", node_order=[])
    assert result["booking_outcome"] == {"state": "nothing_booked", "items": []}


def test_confirmed_this_turn() -> None:
    proposal = Proposal(proposal_id="p1", domain=Domain.DINING, tenant_id="MGV", title="X",
                         starts_at=datetime(2026, 9, 30, 20, 0, tzinfo=UTC), party_size=4,
                         hold_idempotency_key="k1")
    hold = Hold(idempotency_key="k1", hold_id="h1", domain=Domain.DINING, tenant_id="MGV",
                saga_id="s1", status=HoldStatus.CONFIRMED, proposal_id="p1")
    confirmation = Confirmation(confirmation_id="C1", idempotency_key="k1", domain=Domain.DINING,
                                 confirmed_at=datetime.now(UTC))
    state = _base_state(proposals=[proposal], holds=[hold], confirmations=[confirmation])
    result = summarize_turn(state, thread_id="t1", node_order=[])
    assert result["booking_outcome"]["state"] == "confirmed"
    assert result["booking_outcome"]["items"][0]["status"] == "confirmed"


def test_held_this_turn_never_reported_confirmed() -> None:
    proposal = Proposal(proposal_id="p1", domain=Domain.DINING, tenant_id="MGV", title="X",
                         starts_at=datetime(2026, 9, 30, 20, 0, tzinfo=UTC), party_size=4,
                         hold_idempotency_key="k1")
    hold = Hold(idempotency_key="k1", hold_id="h1", domain=Domain.DINING, tenant_id="MGV",
                saga_id="s1", status=HoldStatus.HELD, proposal_id="p1",
                expires_at=datetime(2026, 9, 30, 19, 45, tzinfo=UTC))
    state = _base_state(proposals=[proposal], holds=[hold], confirmations=[])
    result = summarize_turn(state, thread_id="t1", node_order=[])
    assert result["booking_outcome"]["state"] == "held"
