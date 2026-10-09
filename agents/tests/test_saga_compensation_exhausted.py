"""eval/saga_suite.py's dining/compensate and stay/compensate cells (T032
matrix audit). `test_partial_failure_recovery.py`/`test_partial_failure_recovery_nondining.py`
already prove `Recovery` finding a real alternative after a commit failure
(the `confirm` cell); this file closes the sibling cell where `Recovery`
itself is exhausted — no alternative slot exists either — which is the
`compensation_failed` dead-letter path `make_recovery_node`
(`graph/supervisor.py`) takes when `_ALTERNATIVE_SEARCHERS` finds nothing,
mirroring `agents/tests/test_commit_verification.py`'s entertainment case of
the same `escalate_reason` reached via a different code path (a post-commit
vendor disagreement rather than an exhausted alternative search)."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.dining.tests.fakes import FakeDiningTools
from agents.dining.tools import DiningToolError
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.stay.tests.fakes import FakeStayTools
from agents.stay.tools import StayToolError
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from graph.models import Domain
from graph.supervisor import compile_supervisor_graph, new_session_state


class _DiningNoAlternativeAfterFirstCheck(FakeDiningTools):
    """The original availability check (before the hold) must still
    succeed — only `Recovery`'s own retry, the second call, comes back
    empty, proving the alternative search itself (not the original
    booking) is what's exhausted."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._availability_calls = 0

    async def check_availability(self, **kwargs):
        self._availability_calls += 1
        if self._availability_calls > 1:
            from gateway.dining.models import AvailabilityResponse

            return AvailabilityResponse(
                venue_id=kwargs["venue_id"], date=kwargs["date"], slots=[]
            )
        return await super().check_availability(**kwargs)


class _StayNoAlternativeAfterFirstSearch(FakeStayTools):
    """Mirrors `_DiningNoAlternativeAfterFirstCheck`: the original
    `search_rooms` call (room discovery) succeeds; `Recovery`'s own retry
    call comes back with no offers."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._search_calls = 0

    async def search_rooms(self, **kwargs):
        self._search_calls += 1
        if self._search_calls > 1:
            return []
        return await super().search_rooms(**kwargs)


@pytest.mark.asyncio
async def test_dining_compensation_exhausted_escalates_without_falsifying_confirmed_domains():
    dining = _DiningNoAlternativeAfterFirstCheck(
        fail_confirm=DiningToolError("hold expired", vendor_code="HOLD_EXPIRED", http_status=410)
    )
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"dinner and a show on {FIXED_ISO_DATE} at 7pm, party of 2",
    )
    cfg = thread_config("compensation-exhausted-dining-1")
    await app.ainvoke(state, config=cfg)
    result2 = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)

    assert result2["escalate"] is True
    assert result2["escalate_reason"] == "compensation_failed"
    kinds = [e.kind for e in result2["saga_log"]]
    assert "compensation_failed" in kinds

    # The domain that already confirmed must never be falsified as a side
    # effect of dining's exhausted recovery search.
    confirmed_domains = {c.domain for c in result2["confirmations"]}
    assert Domain.ENTERTAINMENT in confirmed_domains
    assert Domain.DINING not in confirmed_domains


@pytest.mark.asyncio
async def test_stay_compensation_exhausted_escalates_without_falsifying_confirmed_domains():
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    stay = _StayNoAlternativeAfterFirstSearch(
        fail_confirm=StayToolError("hold expired", vendor_code="HOLD_EXPIRED", http_status=410)
    )
    factory = {
        Domain.DINING: lambda t, g: dining,
        Domain.ENTERTAINMENT: lambda t, g: entertainment,
        Domain.STAY: lambda t, g: stay,
    }
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"dinner, a show, and a room on {FIXED_ISO_DATE} evening, party of 2",
    )
    cfg = thread_config("compensation-exhausted-stay-1")
    await app.ainvoke(state, config=cfg)
    result2 = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)

    assert result2["escalate"] is True
    assert result2["escalate_reason"] == "compensation_failed"
    kinds = [e.kind for e in result2["saga_log"]]
    assert "compensation_failed" in kinds

    confirmed_domains = {c.domain for c in result2["confirmations"]}
    assert Domain.DINING in confirmed_domains
    assert Domain.ENTERTAINMENT in confirmed_domains
    assert Domain.STAY not in confirmed_domains
