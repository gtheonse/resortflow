"""T051 — Approval interrupt (HITL) and the dining subgraph's conditional
edges.

Node order (per specs/004-dining-vertical-slice/plan.md's architecture
diagram): ... allergen_check -> hold_placement -> human_approval (only when
`allergen_check` flagged a blocking conflict) -> confirm -> END. Placing the
hold *before* asking for approval, not after, is deliberate: the hold is
the reversible half of the two-phase commit (AGENTS.md commitment 3) and
costs nothing to hold while a human decides; `confirm` is the irreversible
half and is exactly what the approval gate stands in front of.

Rejection releases the hold (MCP `release_hold`) and routes to END without
ever reaching `confirm` — AGENTS.md's "every external mutation is
two-phase hold-then-confirm" is only meaningful if reject really means
reject.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from langchain_core.messages import AIMessage
from langgraph.graph import END
from langgraph.types import interrupt

from agents.dining.state import DiningState
from agents.dining.tools import DiningToolError, DiningTools
from graph.ledger import HoldLedger
from graph.models import Domain, Hold, HoldStatus, Proposal, SagaEvent

# --- Node names (internal to this subgraph; stable once chosen) -------------

INTENT_PARSER = "intent_parser"
VENUE_DISCOVERY = "venue_discovery"
AVAILABILITY = "availability"
ALLERGEN_CHECK = "allergen_check"
HOLD_PLACEMENT = "hold_placement"
HUMAN_APPROVAL = "human_approval"
CONFIRM = "confirm"


def _find_proposal(state: DiningState) -> Proposal | None:
    selected_id = state.get("selected_proposal_id")
    for candidate in state.get("proposals", []):
        if candidate.proposal_id == selected_id:
            return candidate
    return None


def _find_hold(state: DiningState, idempotency_key: str) -> Hold | None:
    for hold in state.get("holds", []):
        if hold.idempotency_key == idempotency_key:
            return hold
    return None


# --- Conditional edges --------------------------------------------------------


def route_after_venue_discovery(state: DiningState) -> str:
    return AVAILABILITY if state.get("selected_proposal_id") else END


def route_after_availability(state: DiningState) -> str:
    if state.get("waitlisted"):
        return END
    proposal = _find_proposal(state)
    if proposal is None or proposal.starts_at is None:
        return END
    return ALLERGEN_CHECK


def route_after_allergen_check(state: DiningState) -> str:
    """Feature 006 (ADR-029) as amended by ADR-061: a non-overridable allergen
    block ends the flow *before* any vendor hold is placed — identical to the
    cross-domain fan-out leg, so no inventory is tied up for a guest who can
    never confirm. `allergen_check` already produced the guest-facing message
    and the PolicyFlag audit trail."""
    return END if state.get("policy_blocked") else HOLD_PLACEMENT


def route_after_hold_placement(state: DiningState) -> str:
    proposal = _find_proposal(state)
    if proposal is None or proposal.hold_idempotency_key is None:
        return END
    hold = _find_hold(state, proposal.hold_idempotency_key)
    if hold is None or hold.status != HoldStatus.HELD:
        return END
    if state.get("policy_blocked"):
        # Defence in depth (Constitution Principle IV): `route_after_allergen_check`
        # already ends the flow before a hold exists, but a blocked state must
        # never reach approval or confirm even if that edge is ever rewired.
        return END
    if state.get("awaiting_approval"):
        return HUMAN_APPROVAL
    return CONFIRM


def route_after_human_approval(state: DiningState) -> str:
    return END if state.get("approval_rejected") else CONFIRM


# --- human_approval node ------------------------------------------------------


def make_human_approval_node(tools: DiningTools, ledger: HoldLedger):
    """`interrupt()` pauses the graph; the resumed value is expected to be
    `{"approved": bool}` (or a bare bool) — the shape a human-in-the-loop
    UI/CLI supplies via `Command(resume=...)`."""

    async def human_approval(state: DiningState) -> dict:
        decision = interrupt(
            {
                "approval_scope": state.get("approval_scope"),
                "dietary_conflicts": state.get("dietary_conflicts", []),
            }
        )
        approved = bool(decision.get("approved")) if isinstance(decision, dict) else bool(decision)

        update: dict = {
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
                    await tools.release_hold(
                        hold_id=hold.hold_id,
                        idempotency_key=f"{hold.idempotency_key}:release",
                    )
                except DiningToolError as exc:
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
                            domain=Domain.DINING,
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
            AIMessage(
                content=(
                    "Booking not confirmed — the dietary conflict was not approved. "
                    "Hold released."
                )
            )
        ]
        return update

    return human_approval
