"""T052 — Confirm: MCP `confirm_reservation`, promotes a `Hold` to a
`graph.models.Confirmation` appended to `state["confirmations"]`.

AGENTS.md hard rule: never trust `state["holds"]` on resume — revalidate
against the vendor. This node revalidates unconditionally (not only when
resumed — there is no reliable in-node signal distinguishing "just placed
this superstep" from "resumed after a checkpoint", and revalidating always
is strictly safer) via `check_dining_status` before ever calling
`confirm_reservation`. `HOLD_EXPIRED` — whether discovered at revalidation
or at confirm itself — classifies to `REQUIRES_HUMAN`
(`graph.errors.classify`) and escalates rather than silently retrying:
a fresh hold requires going back through `hold_placement`, which is a
human/orchestrator decision in Phase 4 (no supervisor-driven replan exists
until feature 005).
"""

from __future__ import annotations

from datetime import UTC, datetime

from agents.dining.state import DiningState
from agents.dining.tools import DiningToolError, DiningTools
from graph.errors import classify
from graph.ledger import HoldLedger
from graph.models import Confirmation, Domain, Hold, HoldStatus, Proposal, ToolFailure


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


def make_confirm_node(tools: DiningTools, ledger: HoldLedger):
    async def confirm(state: DiningState) -> dict:
        proposal = _find_proposal(state)
        if proposal is None or proposal.hold_idempotency_key is None:
            return {}
        hold = _find_hold(state, proposal.hold_idempotency_key)
        if hold is None or hold.hold_id is None or hold.status != HoldStatus.HELD:
            return {}

        # --- Revalidate against the vendor before ever confirming ----------
        try:
            status = await tools.check_dining_status(kind="hold", hold_id=hold.hold_id)
        except DiningToolError as exc:
            return await _expire(ledger, state, hold, exc.vendor_code, exc.http_status, exc.detail)

        if status.hold is None or status.hold.status != "active":
            return await _expire(
                ledger, state, hold, "HOLD_EXPIRED", 410, "hold no longer active at vendor"
            )

        # --- Confirm -----------------------------------------------------
        try:
            reservation = await tools.confirm_reservation(
                hold_id=hold.hold_id,
                guest={"guest_id": state["guest_id"]},
                idempotency_key=hold.idempotency_key,
                dietary=[d.restriction.value for d in proposal.dietary],
            )
        except DiningToolError as exc:
            return await _expire(ledger, state, hold, exc.vendor_code, exc.http_status, exc.detail)

        await ledger.mark_confirmed(idempotency_key=hold.idempotency_key)
        confirmed_hold = hold.model_copy(update={"status": HoldStatus.CONFIRMED})
        confirmation = Confirmation(
            confirmation_id=reservation.confirmation_code,
            idempotency_key=hold.idempotency_key,
            domain=Domain.DINING,
            confirmed_at=datetime.now(UTC),
            cancellation_deadline=datetime.fromisoformat(reservation.cancellation_deadline),
        )
        return {"holds": [confirmed_hold], "confirmations": [confirmation]}

    return confirm


async def _expire(
    ledger: HoldLedger, state: DiningState, hold: Hold, vendor_code, http_status, detail
) -> dict:
    error_class = classify(vendor_code, http_status, None)
    failure = ToolFailure(
        domain=Domain.DINING,
        tool="confirm_reservation",
        error_class=error_class,
        vendor_code=vendor_code if isinstance(vendor_code, str) else None,
        http_status=http_status,
        detail=detail,
    )
    await ledger.mark_expired(idempotency_key=hold.idempotency_key)
    expired_hold = hold.model_copy(update={"status": HoldStatus.EXPIRED})
    return {
        "holds": [expired_hold],
        "failures": [failure],
        "awaiting_approval": True,
        "approval_scope": f"hold_expired:{hold.proposal_id}",
    }
