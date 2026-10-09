"""T074 — `/speckit-converge`'s C2 finding: `CommitVerify` must not simply
trust `Commit`'s own recorded outcome (FR-012: "rather than assuming a
commitment succeeded because the request to make it was sent"). Entertainment
is the one domain where an independent vendor round-trip is directly
possible (`Confirmation.confirmation_id` is literally the vendor's
`order_id`) — this test forces `get_order_status` to report the order as
failed *after* `Commit` already recorded a `Confirmation` for it, and asserts
`CommitVerify` catches the mismatch and escalates rather than silently
reporting success."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.dining.tests.fakes import FakeDiningTools
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from graph.models import Domain
from graph.supervisor import compile_supervisor_graph, new_session_state


@pytest.mark.asyncio
async def test_commit_verify_accepts_a_normal_pending_entertainment_order():
    """The disclosed async-purchase simplification: `Order.status` stays
    `"pending"` (the vendor resolves it later via webhook, never modeled
    here) — CommitVerify must not treat that as a mismatch."""
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"dinner and a show on {FIXED_ISO_DATE} at 7pm, party of 2",
    )
    cfg = thread_config("verify-1")
    await app.ainvoke(state, config=cfg)
    result = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)

    assert result["escalate"] is False
    confirmed_domains = {c.domain for c in result["confirmations"]}
    assert confirmed_domains == {Domain.DINING, Domain.ENTERTAINMENT}
    assert not any(e.kind == "verification_mismatch" for e in result["saga_log"])


class _EntertainmentWithFailedOrderStatus(FakeEntertainmentTools):
    """Simulates the anomaly this test exists for: the vendor's own order
    record disagrees with what `Commit` believed happened (e.g. an
    asynchronous vendor-side failure that arrived after the purchase call
    was accepted). There is no fault-injection flag for a post-purchase
    async failure because the real vendor's webhook-driven resolution is
    deliberately unmodeled (see `confirm.py`'s docstring) — overriding
    `get_order_status` is the only way to inject it, since `purchase_tickets`
    itself must still succeed for `Commit` to record the `Confirmation` this
    test needs to already exist before `CommitVerify` re-checks it."""

    async def get_order_status(self, *, hold_id=None, order_id=None):
        if order_id is not None:
            self.calls.append("get_order_status")
            order = self.orders.get(order_id)
            if order is not None:
                order = order.model_copy(update={"status": "failed"})
            from gateway.entertainment.models import OrderStatus

            return OrderStatus(kind="order", order=order)
        return await super().get_order_status(hold_id=hold_id, order_id=order_id)


@pytest.mark.asyncio
async def test_commit_verify_escalates_when_the_vendor_disagrees_with_commit():
    dining = FakeDiningTools()
    entertainment = _EntertainmentWithFailedOrderStatus()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"dinner and a show on {FIXED_ISO_DATE} at 7pm, party of 2",
    )
    cfg = thread_config("verify-2")
    await app.ainvoke(state, config=cfg)

    result = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)

    assert result["escalate"] is True
    assert result["escalate_reason"] == "compensation_failed"
    mismatch_events = [e for e in result["saga_log"] if e.kind == "verification_mismatch"]
    assert mismatch_events and mismatch_events[0].domain == Domain.ENTERTAINMENT

    # The append-only `confirmations` channel can never retract the earlier,
    # now-disputed record (graph/state.py's frozen `append_only` reducer) —
    # CommitVerify must not attempt to falsify history, only flag and escalate.
    assert any(c.domain == Domain.ENTERTAINMENT for c in result["confirmations"])
