"""T070 — `Respond`'s message must distinguish a fully-confirmed itinerary
from a partially-confirmed-plus-recovered-alternative one (FR-014, FR-021,
spec.md User Story 2 Acceptance Scenario 3). Unit-tested directly against
`make_respond_node` for determinism independent of the full saga path."""

from __future__ import annotations

import pytest

from graph.models import Confirmation, Domain, SagaEvent, TurnBaseline
from graph.supervisor import make_respond_node

_EMPTY_BASELINE = TurnBaseline(confirmation_keys=[], hold_statuses={})
"""Feature 010: `_compose_respond_message` now reports from THIS TURN's
outcomes (`turn_outcomes`, research R-12) rather than the cumulative
`confirmations` channel — a raw state dict with no `turn_baseline` at all
is treated as a pre-010 checkpoint's first post-upgrade turn (the
"here's where things stand" fallback, `contracts/turn-replies.md`), not
as "everything below is new". These tests build the confirmations/saga_log
shown below AS this turn's outcome, so they need an explicit empty
baseline to say so — exactly what a real turn's `INTENT_ROUTER` would have
already written before `RESPOND` ever runs."""


def _confirmation(domain: Domain) -> Confirmation:
    from datetime import UTC, datetime

    return Confirmation(
        confirmation_id=f"conf-{domain.value}", idempotency_key=f"key-{domain.value}",
        domain=domain, confirmed_at=datetime.now(UTC),
    )


def _saga_event(kind: str) -> SagaEvent:
    from datetime import UTC, datetime

    return SagaEvent(
        event_id=f"evt-{kind}", saga_id="saga-1", sequence=1, kind=kind, occurred_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_fully_confirmed_message_names_every_domain_with_no_caveat():
    state = {
        "escalate": False,
        "confirmations": [_confirmation(Domain.DINING), _confirmation(Domain.ENTERTAINMENT)],
        "failures": [],
        "saga_log": [],
        "turn_baseline": _EMPTY_BASELINE,
    }
    result = await make_respond_node()(state)
    content = result["messages"][-1].content.lower()
    assert "dining" in content and "entertainment" in content
    assert "alternative" not in content and "escalat" not in content


@pytest.mark.asyncio
async def test_partial_confirmation_with_alternative_message_flags_it():
    state = {
        "escalate": False,
        "confirmations": [_confirmation(Domain.ENTERTAINMENT)],
        "failures": [],
        "saga_log": [_saga_event("commit_failed"), _saga_event("compensated")],
        "turn_baseline": _EMPTY_BASELINE,
    }
    result = await make_respond_node()(state)
    content = result["messages"][-1].content.lower()
    assert "entertainment" in content
    assert "alternative" in content


@pytest.mark.asyncio
async def test_escalated_message_never_claims_success():
    state = {
        "escalate": True,
        "escalate_reason": "replan_bound",
        "confirmations": [],
        "failures": [],
        "saga_log": [],
    }
    result = await make_respond_node()(state)
    content = result["messages"][-1].content.lower()
    assert "team member" in content or "escalat" in content
    assert "confirmed" not in content
