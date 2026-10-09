"""T028 — structural test: `BLOCKED`-equivalent (dining's own subgraph ends
at END directly, never `human_approval`) is the only reachable outcome for
`policy_blocked=True`, for every combination that can set it (ADR-029)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents.dining.edges.conditions import (
    CONFIRM,
    END,
    HUMAN_APPROVAL,
    route_after_hold_placement,
)
from agents.dining.state import DiningState
from graph.models import Domain, Hold, HoldStatus, Proposal


def _base_state(*, policy_blocked: bool, awaiting_approval: bool) -> DiningState:
    proposal = Proposal(
        proposal_id="p1", domain=Domain.DINING, tenant_id="MGV", title="Venue",
        hold_idempotency_key="k1",
    )
    hold = Hold(
        idempotency_key="k1", hold_id="vendor-1", domain=Domain.DINING, tenant_id="MGV",
        saga_id="s1", status=HoldStatus.HELD, expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    return {  # type: ignore[typeddict-item] — minimal state, only fields this routing function reads
        "proposals": [proposal],
        "holds": [hold],
        "selected_proposal_id": "p1",
        "policy_blocked": policy_blocked,
        "awaiting_approval": awaiting_approval,
    }


def test_policy_blocked_true_always_routes_to_end_never_human_approval() -> None:
    """Regardless of `awaiting_approval`'s value, `policy_blocked=True`
    takes precedence — there is no combination that reaches `HUMAN_APPROVAL`
    while a non-overridable block is set."""
    for awaiting in (True, False):
        state = _base_state(policy_blocked=True, awaiting_approval=awaiting)
        route = route_after_hold_placement(state)
        assert route == END
        assert route != HUMAN_APPROVAL


def test_policy_blocked_false_and_awaiting_approval_true_still_reaches_human_approval() -> None:
    """The routing function itself still supports a legitimately-overridable
    case (`policy_blocked=False`, `awaiting_approval=True`) — this feature
    narrows what *allergen_check* sets, not what the routing function itself
    is capable of, preserving `human_approval` for a future overridable
    block type."""
    state = _base_state(policy_blocked=False, awaiting_approval=True)
    assert route_after_hold_placement(state) == HUMAN_APPROVAL


def test_neither_blocked_nor_awaiting_routes_to_confirm() -> None:
    state = _base_state(policy_blocked=False, awaiting_approval=False)
    assert route_after_hold_placement(state) == CONFIRM


def test_allergen_check_routes_blocked_to_end_before_any_hold() -> None:
    """ADR-061: a blocked verdict ends the flow ahead of `hold_placement`."""
    from agents.dining.edges.conditions import HOLD_PLACEMENT, route_after_allergen_check

    assert route_after_allergen_check(_base_state(policy_blocked=True, awaiting_approval=False)) == END
    assert route_after_allergen_check(_base_state(policy_blocked=False, awaiting_approval=False)) == HOLD_PLACEMENT
