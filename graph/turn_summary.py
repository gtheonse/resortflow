"""Turns one supervisor-graph turn's raw result into the JSON-serializable
per-turn outcome shape both `graph/api.py` (production) and `ui/inspector.py`
(the verification console) return.

Moved out of `ui/inspector.py` (feature 008.5 gap closure, ADR-050): the
tool-call/hold/guardrail/interrupt shaping here has no dependency on `ui/` or
`eval/` and is exactly what a production turn response needs, so it lives in
`graph/` where `graph/api.py` can import it without pulling `ui/` into the
production Docker image (the root `Dockerfile` does not `COPY ui/` at all).

Deliberately excludes `ui/inspector.py`'s `groundedness` (LLM-judge
faithfulness scoring via `eval.judge`/`eval.retrieval_scorer`) and `cost`
(a `not_instrumented` placeholder) fields -- those are dev-console
enrichments layered on top by `ui/inspector.py` itself, not core turn
outcome. Running an LLM-judge call synchronously on every production turn
response would also be a real latency/cost concern this module has no
business introducing on its own. `ui/inspector.summarize_turn` wraps this
module's `summarize_turn` and adds those two fields back for the console.
"""
from __future__ import annotations

from typing import Any


def _tool_calls_from_state(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Reconstructs a tool-call timeline from `ResortFlowState` fields that are
    always populated on a real turn, not just `saga_log`/`failures` (which
    only get written on the multi-domain FanOut path today — no domain's
    single-domain `hold_placement.py`/`confirm.py` writes a `SagaEvent` on
    its own success path, confirmed by grep). That made this panel go empty
    for the most common case: a plain single-domain booking that succeeds.

    Ordering is discovery -> hold -> confirm -> saga_log events -> failures,
    the real lifecycle order even without a shared call-sequence counter."""
    calls: list[dict[str, Any]] = []
    for proposal in result.get("proposals") or []:
        calls.append(
            {
                "kind": "proposal",
                "domain": proposal.domain.value,
                "ok": True,
                "title": proposal.title,
            }
        )
    for hold in result.get("holds") or []:
        calls.append(
            {
                "kind": f"hold:{hold.status.value}",
                "domain": hold.domain.value,
                "ok": hold.status.value not in ("expired", "orphan_released"),
                "hold_id": hold.hold_id or hold.idempotency_key,
            }
        )
    for confirmation in result.get("confirmations") or []:
        calls.append(
            {
                "kind": "confirmed",
                "domain": confirmation.domain.value,
                "ok": True,
                "confirmation_id": confirmation.confirmation_id,
            }
        )
    for event in result.get("saga_log") or []:
        calls.append(
            {
                "kind": event.kind,
                "domain": event.domain.value if event.domain else None,
                "sequence": event.sequence,
                "ok": True,
            }
        )
    for failure in result.get("failures") or []:
        calls.append(
            {
                "kind": f"tool_call:{failure.tool}",
                "domain": failure.domain.value,
                "ok": False,
                "error_class": failure.error_class.value
                if hasattr(failure.error_class, "value")
                else str(failure.error_class),
                "detail": failure.detail,
            }
        )
    return calls


def _hold_cards(result: dict[str, Any]) -> list[dict[str, Any]]:
    cards = []
    for hold in result.get("holds") or []:
        cards.append(
            {
                "hold_id": hold.hold_id,
                "idempotency_key": hold.idempotency_key,
                "domain": hold.domain.value,
                "status": hold.status.value,
                "expires_at": hold.expires_at.isoformat() if hold.expires_at else None,
                "proposal_id": hold.proposal_id,
            }
        )
    return cards


def _guardrail_verdicts(result: dict[str, Any]) -> list[dict[str, Any]]:
    flags = []
    for flag in result.get("policy_flags") or []:
        flags.append(
            {
                "rule": flag.rule,
                "disposition": flag.disposition,
                "jurisdiction": flag.jurisdiction,
                "raised_at": flag.raised_at.isoformat() if flag.raised_at else None,
            }
        )
    return flags


def _classify_interrupt(interrupt_payload: Any) -> dict[str, Any]:
    """LangGraph's `chunk["__interrupt__"]`/`StateSnapshot.values["__interrupt__"]`
    is a tuple of `Interrupt` objects. This codebase has at least two
    distinct interrupt payload shapes that need different UI treatment and
    different resume payloads — `human_approval`'s
    `interrupt({"approval_scope": ...})` (resume with
    `Command(resume={"action": ...})`, `graph/supervisor.py`'s
    `make_human_approval_node`) and `clarify`'s
    `interrupt({"question": ...})` (resume with a raw string,
    `make_clarify_node`)."""
    if not interrupt_payload:
        return {"pending": False, "kind": None}
    first = interrupt_payload[0] if isinstance(interrupt_payload, (list, tuple)) else interrupt_payload
    value = getattr(first, "value", first)
    if isinstance(value, dict) and "question" in value:
        return {"pending": True, "kind": "clarify", "question": value["question"]}
    if isinstance(value, dict) and "approval_scope" in value:
        info: dict[str, Any] = {"pending": True, "kind": "approval", "approval_scope": value.get("approval_scope")}
        if value.get("note"):
            info["note"] = value["note"]  # Feature 011: e.g. the arrival date a stay request assumed
        return info
    return {"pending": True, "kind": "unknown", "raw": str(value)}


def _booking_outcome(result: dict[str, Any], interrupt_info: dict[str, Any]) -> dict[str, Any]:
    """Feature 010 (FR-035): what actually happened to a booking THIS
    turn — the console badge's source of truth, so it can never again
    say "Confirmed" just because a resume finished without raising (the
    approve/decline/expired-hold badge bug analyze finding G1 found).
    Precedence: handed_off > awaiting_input > held > confirmed > released
    > nothing_booked."""
    from graph.bookings import turn_outcomes

    if result.get("escalate"):
        return {"state": "handed_off", "items": []}
    if interrupt_info.get("pending"):
        return {"state": "awaiting_input", "items": []}

    outcomes = turn_outcomes(result)
    if not outcomes:
        recent_kinds = {e.kind for e in (result.get("saga_log") or [])[-10:]}
        state = "released" if "released" in recent_kinds else "nothing_booked"
        return {"state": state, "items": []}

    items = [
        {"key": o.key, "service": o.service.value, "venue": o.venue, "status": o.status}
        for o in outcomes
    ]
    statuses = {o.status for o in outcomes}
    if statuses & {"held", "pending"}:
        state = "held"
    elif "confirmed" in statuses:
        state = "confirmed"
    else:
        state = "released"
    return {"state": state, "items": items}


def summarize_turn(
    result: dict[str, Any], *, thread_id: str, node_order: list[list[str]]
) -> dict[str, Any]:
    messages = result.get("messages") or []
    last_message = messages[-1] if messages else None
    reply = getattr(last_message, "content", "") if last_message else ""

    interrupt_info = _classify_interrupt(result.get("__interrupt__"))

    # Found live (demo report): a turn that pauses for approval/clarification
    # before any node ever appends an `AIMessage` left `messages[-1]` as the
    # guest's own just-sent `HumanMessage` — the console then displayed the
    # guest's own text back to them as if it were the assistant's reply.
    # Compose an actual reply describing what's pending instead of echoing.
    if interrupt_info["pending"] and getattr(last_message, "type", None) == "human":
        if interrupt_info["kind"] == "approval":
            reply = f"Please review and approve: {interrupt_info.get('approval_scope') or 'this request'}."
            if interrupt_info.get("note"):
                reply = f"{interrupt_info['note']} {reply}"
        elif interrupt_info["kind"] == "clarify":
            reply = interrupt_info.get("question") or "Could you clarify your request?"
        else:
            reply = "Waiting for your input to continue."

    return {
        "reply": reply,
        "node_order": node_order,
        "tool_calls": _tool_calls_from_state(result),
        "holds": _hold_cards(result),
        "guardrail_verdicts": _guardrail_verdicts(result),
        "escalate": bool(result.get("escalate")),
        "escalate_reason": result.get("escalate_reason"),
        "interrupt": interrupt_info,
        "booking_outcome": _booking_outcome(result, interrupt_info),
    }
