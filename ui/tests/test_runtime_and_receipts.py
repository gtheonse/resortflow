"""Feature 008: unit coverage for the pieces of ui/ that don't need a live
MCP gateway/sims/Postgres stack to verify -- the fault registry's
next-call-only semantics, the receipt store's state machine, and the
inspector's data shaping. A real end-to-end turn (intent parsing -> tool
calls -> hold placement) needs the live stack (`make up && make seed`),
exactly like `graph/demo.py` already discloses in its own module docstring
-- not re-verified here for the same reason.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from graph.errors import ErrorClass
from graph.models import (
    Confirmation,
    Domain,
    Hold,
    HoldStatus,
    PolicyFlag,
    Proposal,
    SagaEvent,
    ToolFailure,
)
from ui.inspector import _classify_interrupt, summarize_turn
from ui.receipts import ReceiptStore
from ui.runtime import REAL_FAULT_VALUES, _NextCallFaultRegistry


def test_fault_registry_is_next_call_only():
    reg = _NextCallFaultRegistry()
    reg.set("thread-1", "dining", "hold_expired")
    assert reg.peek("thread-1") == {"dining": "hold_expired"}

    assert reg.pop_for("thread-1", Domain.DINING) == "hold_expired"
    # Popped -- gone for the next call.
    assert reg.pop_for("thread-1", Domain.DINING) is None
    assert reg.peek("thread-1") == {}


def test_fault_registry_scoped_per_thread_and_domain():
    reg = _NextCallFaultRegistry()
    reg.set("thread-1", "dining", "error_500")
    reg.set("thread-2", "dining", "rate_limit")
    assert reg.pop_for("thread-1", Domain.DINING) == "error_500"
    assert reg.pop_for("thread-2", Domain.DINING) == "rate_limit"
    # Unset domain on a thread with other faults set -> None, not a KeyError.
    reg.set("thread-1", "stay", "webhook_delay")
    assert reg.pop_for("thread-1", Domain.ENTERTAINMENT) is None


def test_fault_registry_clear():
    reg = _NextCallFaultRegistry()
    reg.set("thread-1", "dining", "timeout")
    reg.clear("thread-1", "dining")
    assert reg.peek("thread-1") == {}


def test_all_real_fault_values_match_sims_shared_faults():
    from sims._shared.faults import ALL_FAULT_VALUES

    assert set(REAL_FAULT_VALUES) == ALL_FAULT_VALUES


def test_receipt_store_lifecycle():
    store = ReceiptStore()
    receipt = store.create()
    assert store.get(receipt.token).status == "confirming"

    store.mark_succeeded(receipt.token, {"reply": "ok"})
    assert store.get(receipt.token).status == "succeeded"
    assert store.get(receipt.token).result == {"reply": "ok"}

    other = store.create()
    store.mark_failed(other.token, "boom")
    assert store.get(other.token).status == "failed"
    assert store.get(other.token).error == "boom"


def test_receipt_store_unknown_token():
    store = ReceiptStore()
    assert store.get("nope") is None


def test_summarize_turn_shapes_holds_tool_calls_and_guardrails():
    now = datetime.now(UTC)
    result = {
        "messages": [],
        "saga_log": [
            SagaEvent(
                event_id="e1", saga_id="s1", sequence=1, kind="hold_placed", domain=Domain.DINING,
                occurred_at=now,
            ),
        ],
        "failures": [
            ToolFailure(
                domain=Domain.DINING, tool="confirm_reservation", error_class=ErrorClass.RETRYABLE,
                detail="vendor 500",
            ),
        ],
        "holds": [
            Hold(
                idempotency_key="idem-1", hold_id="hold-1", domain=Domain.DINING, tenant_id="MGV",
                saga_id="s1", status=HoldStatus.HELD, expires_at=now + timedelta(seconds=90),
            ),
        ],
        "policy_flags": [
            PolicyFlag(flag_id="f1", rule="distress_signal", disposition="escalated", jurisdiction="NV", raised_at=now),
        ],
        "retrieved": [],
        "escalate": True,
        "escalate_reason": "distress_signal",
    }

    summary = summarize_turn(result, thread_id="thread-1", node_order=[["intent_parser"], ["hold_placement", "policy_preflight"]])

    assert summary["node_order"] == [["intent_parser"], ["hold_placement", "policy_preflight"]]
    # 1 synthesized from `holds` + 1 from `saga_log` + 1 from `failures`.
    assert len(summary["tool_calls"]) == 3
    assert any(c["kind"] == "hold_placed" and c["ok"] for c in summary["tool_calls"])
    assert any(c["kind"] == "hold:held" and c["ok"] for c in summary["tool_calls"])
    assert any(not c["ok"] and c["error_class"] for c in summary["tool_calls"])
    assert summary["holds"][0]["hold_id"] == "hold-1"
    assert summary["holds"][0]["expires_at"] is not None
    assert summary["guardrail_verdicts"][0]["rule"] == "distress_signal"
    assert summary["groundedness"]["status"] == "not_applicable"
    assert summary["cost"]["status"] == "not_instrumented"
    assert summary["escalate"] is True


def test_tool_calls_synthesized_from_holds_and_confirmations_alone():
    """The actual bug this covers: a plain single-domain booking success
    (e.g. dining/wellness's own hold_placement.py/confirm.py) never writes a
    SagaEvent -- confirmed via grep, zero hits. Before this fix, the Tool
    Calls panel went empty for the single most common real turn -- found
    live via the console, not a hypothetical."""
    now = datetime.now(UTC)
    result = {
        "messages": [],
        "saga_log": [],
        "failures": [],
        "proposals": [
            Proposal(
                proposal_id="wellness:t1:svc-1", domain=Domain.WELLNESS, tenant_id="MGV",
                title="Desert Hot Stone Massage",
            ),
        ],
        "holds": [
            Hold(
                idempotency_key="idem-1", hold_id="hold-1", domain=Domain.WELLNESS, tenant_id="MGV",
                saga_id="s1", status=HoldStatus.CONFIRMED, expires_at=now + timedelta(seconds=90),
            ),
        ],
        "confirmations": [
            Confirmation(
                confirmation_id="conf-1", idempotency_key="idem-1", domain=Domain.WELLNESS,
                confirmed_at=now,
            ),
        ],
    }

    summary = summarize_turn(result, thread_id="thread-1", node_order=[])

    kinds = {c["kind"] for c in summary["tool_calls"]}
    assert "proposal" in kinds
    assert "hold:confirmed" in kinds
    assert "confirmed" in kinds
    assert all(c["ok"] for c in summary["tool_calls"])


def test_summarize_turn_with_no_activity_is_empty_but_shaped():
    summary = summarize_turn({"messages": []}, thread_id="thread-1", node_order=[])
    assert summary["tool_calls"] == []
    assert summary["holds"] == []
    assert summary["guardrail_verdicts"] == []
    assert summary["interrupt"]["pending"] is False


def test_classify_interrupt_no_interrupt():
    assert _classify_interrupt(None) == {"pending": False, "kind": None}
    assert _classify_interrupt(()) == {"pending": False, "kind": None}


def test_classify_interrupt_approval_shape():
    from langgraph.types import Interrupt

    payload = (Interrupt(value={"approval_scope": "Dinner (dining); Show (entertainment)"}),)
    result = _classify_interrupt(payload)
    assert result == {
        "pending": True,
        "kind": "approval",
        "approval_scope": "Dinner (dining); Show (entertainment)",
    }


def test_classify_interrupt_clarify_shape():
    """The bug this fixes: graph/supervisor.py's make_clarify_node raises
    interrupt({"question": ...}) -- a free-text question, not an approval.
    Before this fix, the console rendered an "Approve all"/"Decline all"
    card for this shape regardless."""
    from langgraph.types import Interrupt

    payload = (Interrupt(value={"question": "Which resort experience would you like, and for when?"}),)
    result = _classify_interrupt(payload)
    assert result == {
        "pending": True,
        "kind": "clarify",
        "question": "Which resort experience would you like, and for when?",
    }


def test_classify_interrupt_unknown_shape_does_not_crash():
    from langgraph.types import Interrupt

    payload = (Interrupt(value={"forward": True}),)
    result = _classify_interrupt(payload)
    assert result["pending"] is True
    assert result["kind"] == "unknown"
