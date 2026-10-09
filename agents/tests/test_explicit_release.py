"""T066 — after `HumanApproval` is declined with two outstanding holds
(entertainment + wellness), both are marked `RELEASED` in `hold_ledger`
immediately, not left to expire (FR-022, ADR-026). Uses a `PostgresHoldLedger`
double recorded via a tiny in-memory `HoldLedger` fake so this asserts against
the ledger's own call sequence, not just the in-graph `Hold.status` field —
FR-022 is about the durable ledger row, not only conversational state."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from agents.wellness.tests.fakes import FakeWellnessTools
from graph.models import Domain, HoldStatus
from graph.supervisor import compile_supervisor_graph, new_session_state


class _RecordingLedger:
    """Minimal in-memory `HoldLedger` double that records every call — used
    here to assert `mark_released` is actually invoked for each outstanding
    hold, not just that `Hold.status` reads `RELEASED` in the returned
    conversation state."""

    def __init__(self) -> None:
        self.released: list[str] = []

    async def insert_pending(self, **kwargs):
        pass

    async def mark_held(self, **kwargs):
        pass

    async def mark_confirmed(self, **kwargs):
        pass

    async def mark_released(self, *, idempotency_key: str) -> None:
        self.released.append(idempotency_key)

    async def mark_expired(self, **kwargs):
        pass

    async def mark_orphan_released(self, **kwargs):
        pass


@pytest.mark.asyncio
async def test_decline_releases_every_outstanding_hold_immediately():
    ledger = _RecordingLedger()
    entertainment, wellness = FakeEntertainmentTools(), FakeWellnessTools()
    factory = {Domain.ENTERTAINMENT: lambda t, g: entertainment, Domain.WELLNESS: lambda t, g: wellness}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory, ledger=ledger)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"a show and a spa treatment on {FIXED_ISO_DATE} evening, party of 2",
    )
    cfg = thread_config("release-1")
    result = await app.ainvoke(state, config=cfg)
    assert result["awaiting_approval"] is True
    held_keys = {h.idempotency_key for h in result["holds"] if h.status == HoldStatus.HELD}
    assert len(held_keys) == 2

    result2 = await app.ainvoke(Command(resume={"action": "declined"}), config=cfg)

    assert set(ledger.released) == held_keys, "every outstanding hold's idempotency_key must be released"
    assert all(h.status == HoldStatus.RELEASED for h in result2["holds"])
    assert result2["confirmations"] == []
    # Proactive — not left to expire naturally.
    assert "release_seat_hold" in entertainment.calls
    assert "release_amenity_hold" in wellness.calls
