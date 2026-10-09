"""T039/T040 — quickstart.md Scenario 2: an injected dining commit failure,
with entertainment/wellness already committed, produces a real alternative
*lead* rather than cancelling the itinerary (User Story 2, SC-002). T040's
saga-log assertion: the log contains a failure event followed by a recovery
event, and never a `released` event for the domains that already confirmed.

Updated (demo report, Option B): `recovery()` no longer auto-resolves and
clears `escalate` when it finds an alternative — it has no way to actually
verify or commit that alternative (no edge back to `COMMIT`; see
`graph/supervisor/saga.py`'s own `recovery()` docstring and
`specs/010-tiered-intent-routing/research.md`), so treating "found a
candidate" as resolved used to silently orphan it with nobody — guest or
human — ever told whether it worked. Every recovery pass now escalates
unconditionally; a found alternative is reported as a lead for the human
who picks this up, logged as `compensation_lead_found` (not `compensated`,
which used to imply resolution)."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.dining.tests.fakes import FakeDiningTools
from agents.dining.tools import DiningToolError
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from graph.models import Domain, HoldStatus
from graph.supervisor import compile_supervisor_graph, new_session_state


@pytest.mark.asyncio
async def test_dining_commit_failure_preserves_other_confirmations_and_proposes_alternative():
    dining = FakeDiningTools(
        fail_confirm=DiningToolError("hold expired", vendor_code="HOLD_EXPIRED", http_status=410)
    )
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"dinner and a show on {FIXED_ISO_DATE} at 7pm, party of 2",
    )
    cfg = thread_config("recovery-1")
    result = await app.ainvoke(state, config=cfg)
    assert result["awaiting_approval"] is True

    result2 = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)

    confirmed_domains = {c.domain for c in result2["confirmations"]}
    assert Domain.ENTERTAINMENT in confirmed_domains, "the domain that succeeded must stay confirmed"
    assert Domain.DINING not in confirmed_domains, "the domain that failed must not be falsely confirmed"
    # Option B: a found-but-unverified alternative still escalates — the
    # human handling the hand-off has a real lead (below), not a promise.
    assert result2["escalate"] is True, "an unverified alternative must still hand off to a human"
    assert result2["escalate_reason"] == "compensation_failed"

    # The alternative is reported as a *lead* in the saga log, not silently
    # dropped — the human picking this up can see what was found.
    lead_events = [e for e in result2["saga_log"] if e.kind == "compensation_lead_found" and e.domain == Domain.DINING]
    assert lead_events, "the found alternative must be logged for the human hand-off"

    # The entertainment hold that already confirmed is untouched — never
    # released as a side effect of dining's failure.
    entertainment_holds = [h for h in result2["holds"] if h.domain == Domain.ENTERTAINMENT]
    assert entertainment_holds[0].status == HoldStatus.CONFIRMED

    # Recovery's own message (mentioning the alternative) plus
    # HUMAN_HANDOFF's standard hand-off line both land in the transcript —
    # same two-messages-in-one-turn shape as the modify hand-off (US4).
    all_content = " ".join(m.content for m in result2["messages"]).lower()
    assert "alternative" in all_content
    assert "team member" in all_content


@pytest.mark.asyncio
async def test_saga_log_never_releases_the_domains_that_already_confirmed():
    dining = FakeDiningTools(
        fail_confirm=DiningToolError("hold expired", vendor_code="HOLD_EXPIRED", http_status=410)
    )
    entertainment = FakeEntertainmentTools()
    factory = {Domain.DINING: lambda t, g: dining, Domain.ENTERTAINMENT: lambda t, g: entertainment}
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"dinner and a show on {FIXED_ISO_DATE} at 7pm, party of 2",
    )
    cfg = thread_config("recovery-2")
    await app.ainvoke(state, config=cfg)
    result2 = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)

    kinds = [e.kind for e in result2["saga_log"]]
    assert "commit_failed" in kinds
    failed_index = kinds.index("commit_failed")
    assert "compensation_lead_found" in kinds[failed_index:], (
        "a recovery event must follow the failure (Option B: reported as a lead, not a resolution)"
    )

    released_events = [e for e in result2["saga_log"] if e.kind == "released"]
    assert not any(e.domain == Domain.ENTERTAINMENT for e in released_events), (
        "the domain that already confirmed must never be released as a side effect"
    )
