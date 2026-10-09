"""T030 — Hold placement: MCP `hold_amenity_slot`, constructs
`graph.models.Hold` (status=`HoldStatus.HELD`, `expires_at` required) and
merges it into `state["holds"]` via `merge_holds`. Mirrors
`agents.dining.nodes.hold_placement` exactly, including the write-ahead
ledger sequencing (`graph.ledger.HoldLedger`, ADR-019): `insert_pending` is
called *before* the vendor call, so a crash between here and the vendor
responding leaves a `PENDING` row a reaper can reconcile.

Wellness-specific wrinkle: `hold_amenity_slot` requires a `practitioner_id`
that `check_service_availability`'s slot already carries but the `Proposal`
model has no field for (frozen model — data-model.md). Rather than smuggle
a new scratch field onto `WellnessState` for it, this node re-selects the
same slot (by the proposal's already-resolved `starts_at` time) right
before the hold call — cheap, deterministic, and avoids widening the state
contract for a value that is only ever needed transiently, right here.

Every wellness booking requires human sign-off before commit (health
declarations/contraindications are domain-specific to spa/fitness/cabana/
golf) — unlike dining, where approval is conditional on a flagged allergen
conflict, wellness's `awaiting_approval` is set unconditionally on a
successful hold; `route_after_hold_placement` in `edges/conditions.py`
always routes a held hold to `HUMAN_APPROVAL`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agents.wellness.nodes.availability import select_slot
from agents.wellness.nodes.service_discovery import service_id_from_proposal_id
from agents.wellness.state import WellnessState
from agents.wellness.tools import WellnessToolError, WellnessTools, idempotency_key
from graph.errors import classify
from graph.ledger import HoldLedger
from graph.models import Domain, Hold, HoldStatus, Proposal, ToolFailure


def make_hold_placement_node(tools: WellnessTools, ledger: HoldLedger) -> Any:
    async def hold_placement(state: WellnessState) -> dict[str, Any]:
        selected_id = state.get("selected_proposal_id")
        proposal: Proposal | None = None
        for candidate in state.get("proposals", []):
            if candidate.proposal_id == selected_id:
                proposal = candidate
                break
        if proposal is None or proposal.starts_at is None:
            # No confirmed slot (e.g. no availability) — nothing to hold.
            return {}

        attempt = state.get("hold_attempt", 0) + 1
        key = idempotency_key(state["session_id"], proposal.proposal_id, attempt)
        service_id = service_id_from_proposal_id(proposal.proposal_id)
        date_str = proposal.starts_at.date().isoformat()
        time_str = proposal.starts_at.strftime("%H:%M")

        await ledger.insert_pending(
            saga_id=state["saga_id"],
            session_id=state["session_id"],
            tenant_id=state["tenant_id"],
            domain=Domain.WELLNESS,
            idempotency_key=key,
        )

        try:
            # Re-select the slot to recover the vendor-assigned
            # `practitioner_id` (see module docstring).
            intent = state.get("intent") or {}
            availability = await tools.check_service_availability(
                service_id=service_id,
                date=date_str,
                practitioner_gender_preference=intent.get("practitioner_gender_preference"),
            )
            slot = select_slot(availability.slots, time_str)
            if slot is None:
                raise WellnessToolError(
                    "slot no longer available", vendor_code="SLOT_UNAVAILABLE", http_status=409
                )

            vendor_hold = await tools.hold_amenity_slot(
                service_id=service_id,
                date=date_str,
                time=time_str,
                practitioner_id=slot.practitioner_id,
                idempotency_key=key,
            )
        except WellnessToolError as exc:
            error_class = classify(exc.vendor_code, exc.http_status, None)
            failure = ToolFailure(
                domain=Domain.WELLNESS,
                tool="hold_amenity_slot",
                error_class=error_class,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
                detail=exc.detail,
            )
            hold = Hold(
                idempotency_key=key,
                domain=Domain.WELLNESS,
                tenant_id=state["tenant_id"],
                saga_id=state["saga_id"],
                status=HoldStatus.RELEASED,
                proposal_id=proposal.proposal_id,
            )
            await ledger.mark_released(idempotency_key=key)
            return {"failures": [failure], "holds": [hold], "hold_attempt": attempt}

        expires_at = datetime.fromisoformat(vendor_hold.expires_at)
        await ledger.mark_held(idempotency_key=key, hold_id=vendor_hold.hold_id, expires_at=expires_at)
        hold = Hold(
            idempotency_key=key,
            hold_id=vendor_hold.hold_id,
            domain=Domain.WELLNESS,
            tenant_id=state["tenant_id"],
            saga_id=state["saga_id"],
            status=HoldStatus.HELD,
            expires_at=expires_at,
            proposal_id=proposal.proposal_id,
        )
        updated_proposal = proposal.model_copy(update={"hold_idempotency_key": key})
        return {
            "holds": [hold],
            "proposals": [updated_proposal],
            "hold_attempt": attempt,
            "awaiting_approval": True,
            "approval_scope": f"wellness_booking:{proposal.proposal_id}",
        }

    return hold_placement
