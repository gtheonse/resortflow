"""T073 — `/speckit-converge`'s C1 finding: `Recovery`'s alternative search
must not be dining-only (T072 generalized it). This test forces the STAY
commit to fail after entertainment, dining, and wellness are already
confirmed — stay commits last in `_SAGA_COMMIT_ORDER`, so this is the
realistic "failure after other pieces already committed" case, not merely a
hypothetical one — and asserts `Recovery` finds a real alternative room and
reports it as a lead, mirroring `test_partial_failure_recovery.py`'s dining
case.

Updated (demo report, Option B): `Recovery` always escalates now — see that
file's own updated docstring for the full reasoning."""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.dining.tests.fakes import FakeDiningTools
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.stay.tests.fakes import FakeStayTools
from agents.stay.tools import StayToolError
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from agents.wellness.tests.fakes import FakeWellnessTools
from graph.models import Domain, HoldStatus
from graph.supervisor import compile_supervisor_graph, new_session_state


@pytest.mark.asyncio
async def test_stay_commit_failure_preserves_other_confirmations_and_proposes_alternative():
    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    wellness = FakeWellnessTools()
    stay = FakeStayTools(
        fail_confirm=StayToolError("hold expired", vendor_code="HOLD_EXPIRED", http_status=410)
    )
    factory = {
        Domain.DINING: lambda t, g: dining,
        Domain.ENTERTAINMENT: lambda t, g: entertainment,
        Domain.WELLNESS: lambda t, g: wellness,
        Domain.STAY: lambda t, g: stay,
    }
    app = compile_supervisor_graph(checkpointer=MemorySaver(), tools_factory=factory)

    state = new_session_state(
        tenant_id="MGV", guest_id="g1",
        text=f"dinner, a show, a spa treatment, and a room on {FIXED_ISO_DATE} evening, party of 2",
    )
    cfg = thread_config("recovery-nondining-1")
    result = await app.ainvoke(state, config=cfg)
    assert result["awaiting_approval"] is True

    result2 = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)

    confirmed_domains = {c.domain for c in result2["confirmations"]}
    assert confirmed_domains == {Domain.DINING, Domain.ENTERTAINMENT, Domain.WELLNESS}, (
        "the three domains that succeeded must stay confirmed"
    )
    assert Domain.STAY not in confirmed_domains, "the domain that failed must not be falsely confirmed"
    # Option B: an unverified alternative still escalates — recovery has no
    # way to actually hold/commit it (no edge back to `COMMIT`).
    assert result2["escalate"] is True, "an unverified alternative must still hand off to a human"
    assert result2["escalate_reason"] == "compensation_failed"

    stay_proposals = [p for p in result2["proposals"] if p.domain == Domain.STAY]
    assert any(p.proposal_id.endswith(":alt") for p in stay_proposals), (
        "a new stay alternative proposal must exist, distinct from the failed original"
    )

    for domain in (Domain.DINING, Domain.ENTERTAINMENT, Domain.WELLNESS):
        holds = [h for h in result2["holds"] if h.domain == domain]
        assert holds[0].status == HoldStatus.CONFIRMED, f"{domain} must remain confirmed, untouched by stay's failure"

    kinds = [e.kind for e in result2["saga_log"]]
    assert "commit_failed" in kinds
    failed_index = kinds.index("commit_failed")
    assert "compensation_lead_found" in kinds[failed_index:], (
        "a recovery event must follow the failure (reported as a lead, not a resolution)"
    )

    all_content = " ".join(m.content for m in result2["messages"]).lower()
    assert "alternative" in all_content
    assert "team member" in all_content
