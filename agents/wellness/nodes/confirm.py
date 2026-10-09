"""T030 — Confirm: MCP `confirm_amenity_booking`, promotes a `Hold` to a
`graph.models.Confirmation` appended to `state["confirmations"]`.

AGENTS.md hard rule: never trust `state["holds"]` on resume — revalidate
against the vendor. Wellness has no separate status-check tool the way
dining has `check_dining_status`, so this node revalidates by calling
`check_service_availability` again for the held slot's `service_id`/`date`
and confirming the exact held `time` is still listed among the returned
slots — the closest wellness equivalent to dining's "hold still active"
check available from the frozen tool surface (`mcp/gateway/wellness/
models.py`'s `AvailabilityResponse` carries no separate hold-status lookup).
Revalidation runs unconditionally (not only when resumed — there is no
reliable in-node signal distinguishing "just placed this superstep" from
"resumed after a checkpoint", and revalidating always is strictly safer).
A slot no longer listed classifies as `HOLD_EXPIRED` -> `REQUIRES_HUMAN`
(`graph.errors.classify`) and escalates rather than silently retrying — a
fresh hold requires going back through `hold_placement`, a human/
orchestrator decision.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from agents.wellness.nodes.service_discovery import service_id_from_proposal_id
from agents.wellness.state import WellnessState
from agents.wellness.tools import WellnessToolError, WellnessTools
from graph.errors import classify
from graph.ledger import HoldLedger
from graph.models import Confirmation, Domain, Hold, HoldStatus, Proposal, ToolFailure


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


def make_confirm_node(tools: WellnessTools, ledger: HoldLedger) -> Any:
    async def confirm(state: WellnessState) -> dict[str, Any]:
        proposal = _find_proposal(state)
        if proposal is None or proposal.hold_idempotency_key is None:
            return {}
        hold = _find_hold(state, proposal.hold_idempotency_key)
        if hold is None or hold.hold_id is None or hold.status != HoldStatus.HELD:
            return {}
        if proposal.starts_at is None:
            # No schedule to revalidate against at all — cannot safely
            # confirm a hold whose committed time we can no longer name.
            return await _expire(
                ledger, state, hold, "HOLD_EXPIRED", 410, "proposal lost its scheduled time before confirm"
            )

        # --- Revalidate against the vendor before ever confirming ----------
        service_id = service_id_from_proposal_id(proposal.proposal_id)
        date_str = proposal.starts_at.date().isoformat()
        time_str = proposal.starts_at.strftime("%H:%M")
        try:
            availability = await tools.check_service_availability(service_id=service_id, date=date_str)
        except WellnessToolError as exc:
            return await _expire(ledger, state, hold, exc.vendor_code, exc.http_status, exc.detail)

        still_listed = any(slot.time == time_str for slot in availability.slots)
        if not still_listed:
            return await _expire(
                ledger, state, hold, "HOLD_EXPIRED", 410, "hold no longer active at vendor"
            )

        # --- Confirm -----------------------------------------------------
        try:
            booking = await tools.confirm_amenity_booking(
                hold_id=hold.hold_id,
                guest_id=state["guest_id"],
                idempotency_key=hold.idempotency_key,
            )
        except WellnessToolError as exc:
            return await _expire(ledger, state, hold, exc.vendor_code, exc.http_status, exc.detail)

        await ledger.mark_confirmed(idempotency_key=hold.idempotency_key)
        confirmed_hold = hold.model_copy(update={"status": HoldStatus.CONFIRMED})
        confirmation = Confirmation(
            confirmation_id=booking.booking_id,
            idempotency_key=hold.idempotency_key,
            domain=Domain.WELLNESS,
            confirmed_at=datetime.now(UTC),
            cancellation_deadline=datetime.fromisoformat(booking.cancellation_deadline),
        )
        return {"holds": [confirmed_hold], "confirmations": [confirmation]}

    return confirm


async def _expire(
    ledger: HoldLedger, state: WellnessState, hold: Hold,
    vendor_code: str | None, http_status: int | None, detail: str,
) -> dict[str, Any]:
    error_class = classify(vendor_code, http_status, None)
    failure = ToolFailure(
        domain=Domain.WELLNESS,
        tool="confirm_amenity_booking",
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
