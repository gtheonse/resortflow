"""T030 — Availability check: MCP `check_service_availability`, refines the
selected `Proposal`'s `starts_at`/`ends_at`. Unlike dining, wellness has no
waitlist equivalent (no `join_waitlist` tool exists in
`mcp/gateway/wellness/server.py`) — no slots found simply reports no
availability in a message and the conditional edge routes straight to END.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from gateway.wellness.models import AvailabilitySlot
from langchain_core.messages import AIMessage

from agents.wellness.nodes.service_discovery import service_id_from_proposal_id
from agents.wellness.state import WellnessState
from agents.wellness.tools import WellnessToolError, WellnessTools
from graph.errors import classify
from graph.models import Domain, Proposal, ToolFailure

DEFAULT_DURATION_MINUTES = 60
"""Fallback only — the real duration always comes from the matching
`Service.duration_minutes` looked up below; used only if that lookup
somehow misses (e.g. a fake/test double that doesn't echo the service)."""


def select_slot(slots: list[AvailabilitySlot], preferred_time: str | None) -> AvailabilitySlot | None:
    """Shared with `hold_placement` (re-selects the same slot there to learn
    the vendor-assigned `practitioner_id` right before the hold call, rather
    than smuggling it through a new `ResortFlowState`/`WellnessState` field)."""
    if not slots:
        return None
    if preferred_time is None:
        return slots[0]
    return min(slots, key=lambda s: abs(_minutes(s.time) - _minutes(preferred_time)))


def _minutes(hhmm: str) -> int:
    hour, minute = hhmm.split(":")
    return int(hour) * 60 + int(minute)


def make_availability_node(tools: WellnessTools, *, today: date | None = None) -> Any:
    def _find_proposal(state: WellnessState) -> Proposal | None:
        selected_id = state.get("selected_proposal_id")
        if not selected_id:
            return None
        for proposal in state.get("proposals", []):
            if proposal.proposal_id == selected_id:
                return proposal
        return None

    async def availability(state: WellnessState) -> dict[str, Any]:
        proposal = _find_proposal(state)
        if proposal is None:
            return {}

        intent = state.get("intent") or {}
        resolved_today = today or date.today()  # noqa: DTZ011 — local wall-clock date is correct here
        date_str = intent.get("date_str") or (resolved_today + timedelta(days=1)).isoformat()
        service_id = service_id_from_proposal_id(proposal.proposal_id)

        try:
            response = await tools.check_service_availability(
                service_id=service_id,
                date=date_str,
                practitioner_gender_preference=intent.get("practitioner_gender_preference"),
            )
        except WellnessToolError as exc:
            error_class = classify(exc.vendor_code, exc.http_status, None)
            failure = ToolFailure(
                domain=Domain.WELLNESS,
                tool="check_service_availability",
                error_class=error_class,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
                detail=exc.detail,
            )
            return {
                "failures": [failure],
                "messages": [AIMessage(content="No availability could be checked for that service.")],
            }

        slot = select_slot(response.slots, intent.get("time_str"))
        if slot is None:
            return {
                "messages": [
                    AIMessage(content="No availability for that service on that date.")
                ],
            }

        # Duration lives on the `Service` model, not the availability slot —
        # look the service back up by id to refine `ends_at` accurately.
        services_response = await tools.list_services()
        duration = next(
            (s.duration_minutes for s in services_response.services if s.service_id == service_id),
            DEFAULT_DURATION_MINUTES,
        )

        starts_at = datetime.fromisoformat(f"{date_str}T{slot.time}:00")
        updated = proposal.model_copy(
            update={
                "starts_at": starts_at,
                "ends_at": starts_at + timedelta(minutes=duration),
            }
        )
        return {"proposals": [updated]}

    return availability
