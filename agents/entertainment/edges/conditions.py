"""T027 — Approval interrupt (HITL) and the entertainment subgraph's
conditional edges. Mirrors `agents/dining/edges/conditions.py`.

Node order: intent_parser -> event_discovery -> inventory_check ->
hold_placement -> human_approval -> purchase -> END. Unlike dining (whose
`human_approval` gate is conditional on `allergen_check` flagging a
blocking dietary conflict), entertainment has no analogous pre-hold
compliance check, so `human_approval` sits unconditionally between
`hold_placement` and `purchase`: ticket purchase is an irreversible spend
of the guest's money, and a human/guest confirmation step before that
commitment is the domain's own safety margin, standing in for the
conflict-gate role allergen_check plays for dining. Placing the hold
*before* asking for approval, not after, is deliberate for the same reason
as dining: the hold is the reversible half of the two-phase commit
(AGENTS.md commitment 3) and costs nothing to hold while a human decides;
`purchase` is the irreversible half and is exactly what the approval gate
stands in front of.

Rejection releases the hold (MCP `release_seat_hold`) and routes to END
without ever reaching `purchase` — AGENTS.md's "every external mutation is
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

from agents.entertainment.state import EntertainmentState
from agents.entertainment.tools import EntertainmentToolError, EntertainmentTools
from graph.ledger import HoldLedger
from graph.models import Domain, Hold, HoldStatus, Proposal, SagaEvent

# --- Node names (internal to this subgraph; stable once chosen) -------------

INTENT_PARSER = "intent_parser"
EVENT_DISCOVERY = "event_discovery"
INVENTORY_CHECK = "inventory_check"
HOLD_PLACEMENT = "hold_placement"
HUMAN_APPROVAL = "human_approval"
PURCHASE = "purchase"


def _find_proposal(state: EntertainmentState) -> Proposal | None:
    selected_id = state.get("selected_proposal_id")
    for candidate in state.get("proposals", []):
        if candidate.proposal_id == selected_id:
            return candidate
    return None


def _find_hold(state: EntertainmentState, idempotency_key: str) -> Hold | None:
    for hold in state.get("holds", []):
        if hold.idempotency_key == idempotency_key:
            return hold
    return None


# --- Conditional edges --------------------------------------------------------


def route_after_event_discovery(state: EntertainmentState) -> str:
    return INVENTORY_CHECK if state.get("selected_proposal_id") else END


def route_after_inventory_check(state: EntertainmentState) -> str:
    proposal = _find_proposal(state)
    if proposal is None or proposal.starts_at is None or not state.get("selected_section_id"):
        return END
    return HOLD_PLACEMENT


def route_after_hold_placement(state: EntertainmentState) -> str:
    proposal = _find_proposal(state)
    if proposal is None or proposal.hold_idempotency_key is None:
        return END
    hold = _find_hold(state, proposal.hold_idempotency_key)
    if hold is None or hold.status != HoldStatus.HELD:
        return END
    return HUMAN_APPROVAL


def route_after_human_approval(state: EntertainmentState) -> str:
    return END if state.get("approval_rejected") else PURCHASE


# --- human_approval node ------------------------------------------------------


def make_human_approval_node(tools: EntertainmentTools, ledger: HoldLedger) -> Any:
    """`interrupt()` pauses the graph; the resumed value is expected to be
    `{"approved": bool}` (or a bare bool) — the shape a human-in-the-loop
    UI/CLI supplies via `Command(resume=...)`."""

    async def human_approval(state: EntertainmentState) -> dict[str, Any]:
        decision = interrupt(
            {
                "approval_scope": state.get("approval_scope"),
                "proposal_id": state.get("selected_proposal_id"),
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
                    await tools.release_seat_hold(
                        hold_id=hold.hold_id,
                        idempotency_key=f"{hold.idempotency_key}:release",
                    )
                except EntertainmentToolError as exc:
                    # Compensation itself failed. Constitution Principle
                    # III: this MUST NOT be a silent pass — write the
                    # failure to the saga log (this feature's dead-letter
                    # path; no dedicated dead-letter store exists until a
                    # later feature) and escalate to a human. The hold is
                    # still marked released *locally* so `purchase` can
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
                            domain=Domain.ENTERTAINMENT,
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
            AIMessage(content="Purchase not approved — hold released.")
        ]
        return update

    return human_approval
