"""T035 [CRIT] — a dinner time and show time requiring infeasible travel per
`graph/travel_time.py`'s matrix must be caught by `ConflictCheck` before
`HumanApproval` (spec.md Acceptance Scenario 2). Never economy: the
conflict checker is deterministic by construction (Constitution: DETERMINISM
FIRST) — these tests exercise `make_conflict_check_node` directly against
hand-built state, which is more robust than depending on which slot a fake
tool happens to pick, and is exactly the "no LLM in the loop" property this
node exists to guarantee."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from graph.models import Domain, Hold, HoldStatus, Proposal
from graph.supervisor import make_conflict_check_node
from graph.travel_time import travel_minutes


def _held_proposal(*, proposal_id, domain, title, zone, starts_at, ends_at) -> tuple[Proposal, Hold]:
    key = f"key-{proposal_id}"
    proposal = Proposal(
        proposal_id=proposal_id, domain=domain, tenant_id="MGV", title=title,
        location_zone=zone, starts_at=starts_at, ends_at=ends_at, hold_idempotency_key=key,
    )
    hold = Hold(
        idempotency_key=key, hold_id=f"hold-{proposal_id}", domain=domain, tenant_id="MGV",
        saga_id="saga-1", status=HoldStatus.HELD, expires_at=starts_at + timedelta(hours=1),
    )
    return proposal, hold


@pytest.mark.asyncio
async def test_infeasible_travel_between_domains_is_flagged():
    dinner_start = datetime(2026, 9, 12, 19, 0, tzinfo=UTC)
    dinner_end = dinner_start + timedelta(minutes=90)  # ends 20:30
    dining_p, dining_h = _held_proposal(
        proposal_id="p-dining", domain=Domain.DINING, title="Dinner",
        zone="poolside", starts_at=dinner_start, ends_at=dinner_end,
    )
    # arena_zone is far from poolside (see graph/travel_time.py's MGV matrix);
    # a curtain 4 minutes after the dinner ends leaves no realistic buffer.
    needed = travel_minutes("MGV", "poolside", "arena_zone")
    show_start = dinner_end + timedelta(minutes=max(1, needed - 2))
    entertainment_p, entertainment_h = _held_proposal(
        proposal_id="p-show", domain=Domain.ENTERTAINMENT, title="Show",
        zone="arena_zone", starts_at=show_start, ends_at=show_start + timedelta(hours=2),
    )

    state = {
        "proposals": [dining_p, entertainment_p],
        "holds": [dining_h, entertainment_h],
        "saga_id": "saga-1",
        "saga_log": [],
    }
    result = await make_conflict_check_node()(state)

    kinds = [e.kind for e in result.get("saga_log", [])]
    assert kinds == ["conflict_detected"]


@pytest.mark.asyncio
async def test_ample_travel_buffer_is_not_flagged():
    dinner_start = datetime(2026, 9, 12, 18, 0, tzinfo=UTC)
    dinner_end = dinner_start + timedelta(minutes=90)
    dining_p, dining_h = _held_proposal(
        proposal_id="p-dining", domain=Domain.DINING, title="Dinner",
        zone="tower_a", starts_at=dinner_start, ends_at=dinner_end,
    )
    # Same zone, hours later — trivially feasible.
    show_start = dinner_end + timedelta(hours=2)
    entertainment_p, entertainment_h = _held_proposal(
        proposal_id="p-show", domain=Domain.ENTERTAINMENT, title="Show",
        zone="tower_a", starts_at=show_start, ends_at=show_start + timedelta(hours=1),
    )

    state = {
        "proposals": [dining_p, entertainment_p],
        "holds": [dining_h, entertainment_h],
        "saga_id": "saga-1",
        "saga_log": [],
    }
    result = await make_conflict_check_node()(state)

    assert result == {}


@pytest.mark.asyncio
async def test_overlapping_schedules_same_domain_pair_is_flagged():
    start = datetime(2026, 9, 12, 19, 0, tzinfo=UTC)
    a, ha = _held_proposal(
        proposal_id="p-a", domain=Domain.DINING, title="A", zone="tower_a",
        starts_at=start, ends_at=start + timedelta(hours=2),
    )
    b, hb = _held_proposal(
        proposal_id="p-b", domain=Domain.WELLNESS, title="B", zone="tower_a",
        starts_at=start + timedelta(minutes=30), ends_at=start + timedelta(hours=1, minutes=30),
    )
    state = {"proposals": [a, b], "holds": [ha, hb], "saga_id": "saga-1", "saga_log": []}
    result = await make_conflict_check_node()(state)

    kinds = [e.kind for e in result.get("saga_log", [])]
    assert "conflict_detected" in kinds
