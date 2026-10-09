"""T051 (stay) — Approval interrupt (HITL) and the stay subgraph's
conditional edges.

Node order: intent_parser -> room_discovery -> hold_placement ->
human_approval -> confirm -> END. Placing the hold *before* asking for
approval, not after, is deliberate — same reasoning as
`agents.dining.edges.conditions`/`agents.wellness.edges.conditions`: the
hold is the reversible half of the two-phase commit (AGENTS.md commitment 3)
and costs nothing to hold while a human decides; `confirm` is the
irreversible half and is exactly what the approval gate stands in front of.

Every successfully-held stay booking routes through `human_approval`
unconditionally — same pattern as wellness (`hold_placement` always sets
`awaiting_approval = True` on success; no dedicated conflict-detection node
exists for stay the way `allergen_check` does for dining).

Rejection releases the hold (MCP `release_room_hold`) and routes to END
without ever reaching `confirm` — AGENTS.md's "every external mutation is
two-phase hold-then-confirm" is only meaningful if reject really means
reject.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.graph import END
from langgraph.types import interrupt

from agents.stay.state import StayState
from agents.stay.tools import StayToolError, StayTools
from graph.ledger import HoldLedger
from graph.models import Domain, Hold, HoldStatus, Proposal, SagaEvent
from graph.replies import stay_assumed_arrival_note

# --- Node names (internal to this subgraph; stable once chosen) -------------

INTENT_PARSER = "intent_parser"
ROOM_DISCOVERY = "room_discovery"
HOLD_PLACEMENT = "hold_placement"
HUMAN_APPROVAL = "human_approval"
CONFIRM = "confirm"


def _find_proposal(state: StayState) -> Proposal | None:
    selected_id = state.get("selected_proposal_id")
    for candidate in state.get("proposals", []):
        if candidate.proposal_id == selected_id:
            return candidate
    return None


def _find_hold(state: StayState, idempotency_key: str) -> Hold | None:
    for hold in state.get("holds", []):
        if hold.idempotency_key == idempotency_key:
            return hold
    return None


# --- Conditional edges --------------------------------------------------------


def route_after_room_discovery(state: StayState) -> str:
    return HOLD_PLACEMENT if state.get("selected_proposal_id") else END


def route_after_hold_placement(state: StayState) -> str:
    proposal = _find_proposal(state)
    if proposal is None or proposal.hold_idempotency_key is None:
        return END
    hold = _find_hold(state, proposal.hold_idempotency_key)
    if hold is None or hold.status != HoldStatus.HELD:
        return END
    if state.get("awaiting_approval"):
        return HUMAN_APPROVAL
    return CONFIRM


def route_after_human_approval(state: StayState) -> str:
    return END if state.get("approval_rejected") else CONFIRM


# --- human_approval node ------------------------------------------------------


def make_human_approval_node(tools: StayTools, ledger: HoldLedger) -> Any:
    """`interrupt()` pauses the graph; the resumed value is expected to be
    `{"approved": bool}` (or a bare bool) — the shape a human-in-the-loop
    UI/CLI supplies via `Command(resume=...)`."""

    async def human_approval(state: StayState) -> dict[str, Any]:
        payload: dict[str, Any] = {"approval_scope": state.get("approval_scope")}
        intent = state.get("intent") or {}
        assumed_date = intent.get("date_str")
        if intent.get("arrival_assumed") and assumed_date:
            # Feature 011 (FR-018): the guest gave no arrival date and tomorrow was used — say so where they can see it.
            arrival = date.fromisoformat(assumed_date)
            nights = intent.get("nights") or 1
            payload["note"] = stay_assumed_arrival_note(arrival, arrival + timedelta(days=nights), nights)
        decision = interrupt(payload)
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)

        update: dict[str, Any] = {
            "awaiting_approval": False,
            "approval_scope": None,
            "approval_rejected": not approved,
        }
        if approved:
            return update

        proposal = _find_proposal(state)
        if proposal is not None and proposal.hold_idempotency_key is not None:
            hold = _find_hold(state, proposal.hold_idempotency_key)
            if hold is not None and hold.hold_id is not None and hold.status == HoldStatus.HELD:
                try:
                    await tools.release_room_hold(
                        hold_id=hold.hold_id,
                        idempotency_key=f"{hold.idempotency_key}:release",
                    )
                except StayToolError as exc:
                    # Compensation itself failed. Constitution Principle
                    # III: this MUST NOT be a silent pass — write the
                    # failure to the saga log (this feature's dead-letter
                    # path; no dedicated dead-letter store exists until a
                    # later feature) and escalate to a human. The hold is
                    # still marked released *locally* so `confirm` can
                    # never fire for it regardless — the vendor's own TTL
                    # is the backstop for the vendor-side state, but a
                    # human still needs to know the release call itself
                    # did not succeed.
                    existing_log = state.get("saga_log") or []
                    update["saga_log"] = [
                        SagaEvent(
                            event_id=uuid.uuid4().hex,
                            saga_id=state["saga_id"],
                            sequence=len(existing_log) + 1,
                            kind="compensation_failed",
                            domain=Domain.STAY,
                            occurred_at=datetime.now(UTC),
                            detail={
                                "hold_idempotency_key": hold.idempotency_key,
                                "vendor_code": str(exc.vendor_code),
                                "detail": exc.detail,
                            },
                        )
                    ]
                    update["escalate"] = True
                else:
                    await ledger.mark_released(idempotency_key=hold.idempotency_key)
                update["holds"] = [hold.model_copy(update={"status": HoldStatus.RELEASED})]
        update["messages"] = [
            AIMessage(content="Booking not confirmed — approval was declined. Hold released.")
        ]
        return update

    return human_approval
