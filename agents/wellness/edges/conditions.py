"""T030/T031 — Approval interrupt (HITL) and the wellness subgraph's
conditional edges.

Node order: intent_parser -> service_discovery -> availability ->
hold_placement -> human_approval -> confirm -> END. Placing the hold
*before* asking for approval, not after, is deliberate — same reasoning as
`agents.dining.edges.conditions`: the hold is the reversible half of the
two-phase commit (AGENTS.md commitment 3) and costs nothing to hold while a
human decides; `confirm` is the irreversible half and is exactly what the
approval gate stands in front of.

Unlike dining (where approval is conditional on a flagged allergen
conflict), every successfully-held wellness booking routes through
`human_approval` unconditionally — `hold_placement` always sets
`awaiting_approval = True` on success (spa/fitness/cabana/golf bookings
carry health declarations/contraindications that warrant a human check
before an irreversible commit, and no dedicated conflict-detection node
exists for wellness the way `allergen_check` does for dining).

Rejection releases the hold (MCP `release_amenity_hold`) and routes to END
without ever reaching `confirm` — AGENTS.md's "every external mutation is
two-phase hold-then-confirm" is only meaningful if reject really means
reject.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage
from langgraph.graph import END
from langgraph.types import interrupt

from agents.wellness.state import WellnessState
from agents.wellness.tools import WellnessToolError, WellnessTools
from graph.ledger import HoldLedger
from graph.models import Domain, Hold, HoldStatus, Proposal, SagaEvent

# --- Node names (internal to this subgraph; stable once chosen) -------------

INTENT_PARSER = "intent_parser"
SERVICE_DISCOVERY = "service_discovery"
AVAILABILITY = "availability"
HOLD_PLACEMENT = "hold_placement"
HUMAN_APPROVAL = "human_approval"
CONFIRM = "confirm"


def _find_proposal(state: WellnessState) -> Proposal | None:
    selected_id = state.get("selected_proposal_id")
    for candidate in state.get("proposals", []):
        if candidate.proposal_id == selected_id:
            return candidate
    return None


def _find_hold(state: WellnessState, idempotency_key: str) -> Hold | None:
    for hold in state.get("holds", []):
        if hold.idempotency_key == idempotency_key:
            return hold
    return None


# --- Conditional edges --------------------------------------------------------


def route_after_service_discovery(state: WellnessState) -> str:
    return AVAILABILITY if state.get("selected_proposal_id") else END


def route_after_availability(state: WellnessState) -> str:
    proposal = _find_proposal(state)
    if proposal is None or proposal.starts_at is None:
        return END
    return HOLD_PLACEMENT


def route_after_hold_placement(state: WellnessState) -> str:
    proposal = _find_proposal(state)
    if proposal is None or proposal.hold_idempotency_key is None:
        return END
    hold = _find_hold(state, proposal.hold_idempotency_key)
    if hold is None or hold.status != HoldStatus.HELD:
        return END
    if state.get("awaiting_approval"):
        return HUMAN_APPROVAL
    return CONFIRM


def route_after_human_approval(state: WellnessState) -> str:
    return END if state.get("approval_rejected") else CONFIRM


# --- human_approval node ------------------------------------------------------


def make_human_approval_node(tools: WellnessTools, ledger: HoldLedger) -> Any:
    """`interrupt()` pauses the graph; the resumed value is expected to be
    `{"approved": bool}` (or a bare bool) — the shape a human-in-the-loop
    UI/CLI supplies via `Command(resume=...)`."""

    async def human_approval(state: WellnessState) -> dict[str, Any]:
        decision = interrupt(
            {
                "approval_scope": state.get("approval_scope"),
            }
        )
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
                    await tools.release_amenity_hold(
                        hold_id=hold.hold_id,
                        idempotency_key=f"{hold.idempotency_key}:release",
                    )
                except WellnessToolError as exc:
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
                            domain=Domain.WELLNESS,
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
