"""T034 — quickstart.md Scenario 1: a three-domain itinerary (dining, show,
spa) succeeds end-to-end in one conversation (User Story 1, SC-001)."""

from __future__ import annotations

import pytest
from langgraph.types import Command

from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from graph.models import Domain, HoldStatus
from graph.supervisor import new_session_state


@pytest.mark.asyncio
async def test_three_domain_itinerary_confirms_all_three(app_and_fakes):
    app, _fakes = app_and_fakes
    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"dinner, a show, and a spa treatment on {FIXED_ISO_DATE} evening, party of 2",
    )
    cfg = thread_config("mvp-1")

    result = await app.ainvoke(state, config=cfg)
    assert result["awaiting_approval"] is True
    proposal_domains = {p.domain for p in result["proposals"]}
    assert proposal_domains == {Domain.DINING, Domain.ENTERTAINMENT, Domain.WELLNESS}
    assert all(
        h.status == HoldStatus.HELD for h in result["holds"] if h.domain in proposal_domains
    )

    result2 = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)

    confirmed_domains = {c.domain for c in result2["confirmations"]}
    assert confirmed_domains == {Domain.DINING, Domain.ENTERTAINMENT, Domain.WELLNESS}
    assert all(h.status == HoldStatus.CONFIRMED for h in result2["holds"])
    assert not result2["failures"]
    assert result2["escalate"] is False


@pytest.mark.asyncio
async def test_single_domain_request_never_enters_the_saga_path(app_and_fakes):
    """A single-domain request routes through DETERMINISTIC — never through
    PLANNER/FanOut/HumanApproval's cross-domain saga machinery."""
    app, _fakes = app_and_fakes
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text=f"table for 2 on {FIXED_ISO_DATE} at 7pm, steakhouse"
    )
    cfg = thread_config("standalone-1")

    result = await app.ainvoke(state, config=cfg)
    assert result["plan"] is None  # PLANNER never ran
    assert len(result["confirmations"]) == 1
    assert result["confirmations"][0].domain == Domain.DINING
