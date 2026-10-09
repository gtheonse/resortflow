"""T033 [CRIT] — proves `ResortFlow-HITL-Durability-State.md` §2's exact guarantee:
"the fan-out superstep runs Dining, Entertainment and Wellness in parallel...
the successful nodes' writes are already durable and don't re-run on
resume." FR-007, SC-003.

Forces dining's `hold_table` call to raise mid-superstep (simulating a crash
partway through the parallel fan-out); asserts entertainment's hold is
already durable in the checkpoint (queried via `aget_state`, without
resuming) and, after resume, that entertainment's `hold_seats` was called
exactly once — never re-executed — while dining's `hold_table` was retried.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.dining.tests.fakes import FakeDiningTools
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from graph.models import Domain, HoldStatus
from graph.supervisor import compile_supervisor_graph


class _FlakyDiningTools(FakeDiningTools):
    """Raises on the first `hold_table` call, succeeds thereafter — the
    node-level equivalent of "the process crashed right after the vendor
    call started"."""

    def __init__(self) -> None:
        super().__init__()
        self.hold_attempts = 0

    async def hold_table(self, **kwargs):
        self.hold_attempts += 1
        if self.hold_attempts == 1:
            raise RuntimeError("simulated crash mid-hold")
        return await super().hold_table(**kwargs)


@pytest.mark.asyncio
async def test_other_domains_hold_survives_and_is_not_reexecuted_on_resume():
    flaky_dining = _FlakyDiningTools()
    entertainment = FakeEntertainmentTools()
    factory = {
        Domain.DINING: lambda t, g: flaky_dining,
        Domain.ENTERTAINMENT: lambda t, g: entertainment,
    }
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)
    from graph.supervisor import new_session_state

    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"dinner and a show on {FIXED_ISO_DATE} at 7pm, party of 2",
    )
    cfg = thread_config("fanout-crash-1")

    with pytest.raises(RuntimeError, match="simulated crash mid-hold"):
        await app.ainvoke(state, config=cfg)

    # Entertainment's branch completed and is durable even though dining's
    # branch raised in the SAME superstep — the pending-writes guarantee.
    assert entertainment.calls.count("hold_seats") == 1
    snapshot = await app.aget_state(cfg)
    holds_before_resume = snapshot.values.get("holds", [])
    entertainment_holds = [h for h in holds_before_resume if h.domain == Domain.ENTERTAINMENT]
    assert len(entertainment_holds) == 1
    assert entertainment_holds[0].status == HoldStatus.HELD

    # Resume: only dining's failed node re-runs.
    result = await app.ainvoke(None, config=cfg)

    assert flaky_dining.hold_attempts == 2
    assert entertainment.calls.count("hold_seats") == 1, "entertainment's already-durable hold must not re-execute"
    domains_held = {h.domain: h.status for h in result.get("holds", [])}
    assert domains_held[Domain.DINING] == HoldStatus.HELD
    assert domains_held[Domain.ENTERTAINMENT] == HoldStatus.HELD
    # Exactly one hold row per domain — never duplicated across the retry.
    assert len([h for h in result["holds"] if h.domain == Domain.ENTERTAINMENT]) == 1
    assert len([h for h in result["holds"] if h.domain == Domain.DINING]) == 1
