"""Six wellness/amenities tools: spa, fitness, cabana, golf."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel

from gateway._shared.middleware import get_current_identity
from gateway._shared.registry import register_tool
from gateway._shared.upstream import UpstreamClient

from .models import (
    AmenityBooking,
    AmenityHold,
    AvailabilityResponse,
    EmptyResponse,
    ListServicesResponse,
)

app = MCPServer(name="wellness", instructions="Wellness/amenities tools")
wellness_client = UpstreamClient(domain="wellness")


class ListServicesInput(BaseModel):
    model_config = {"extra": "forbid"}
    category: str | None = None


class CheckServiceAvailabilityInput(BaseModel):
    model_config = {"extra": "forbid"}
    service_id: str
    date: str
    practitioner_gender_preference: str | None = None
    accessibility_required: bool | None = None


class HoldAmenitySlotInput(BaseModel):
    model_config = {"extra": "forbid"}
    service_id: str
    date: str
    time: str
    practitioner_id: str
    ttl_seconds: int | None = None


class ReleaseAmenityHoldInput(BaseModel):
    model_config = {"extra": "forbid"}
    hold_id: str


class ConfirmAmenityBookingInput(BaseModel):
    model_config = {"extra": "forbid"}
    hold_id: str
    guest_id: str
    health_declarations: list[str] | None = None
    accessibility_needs: str | None = None


class CancelAmenityBookingInput(BaseModel):
    model_config = {"extra": "forbid"}
    booking_id: str


@app.tool()
@register_tool(domain="wellness", entitlement_action="read", is_mutating=False)
async def list_services(input: ListServicesInput) -> ListServicesResponse:
    """List available wellness services."""
    identity = get_current_identity()
    response = await wellness_client.call(
        "GET",
        "/amenities/v3/services",
        tenant_id=identity.tenant_id,
        response_model=ListServicesResponse,
        params={"category": input.category} if input.category else {},
    )
    return response


@app.tool()
@register_tool(domain="wellness", entitlement_action="read", is_mutating=False)
async def check_service_availability(input: CheckServiceAvailabilityInput) -> AvailabilityResponse:
    """Check availability for a wellness service."""
    identity = get_current_identity()
    response = await wellness_client.call(
        "GET",
        f"/amenities/v3/services/{input.service_id}/availability",
        tenant_id=identity.tenant_id,
        response_model=AvailabilityResponse,
        params={
            k: v for k, v in {
                "date": input.date,
                "practitioner_gender_preference": input.practitioner_gender_preference,
                "accessibility_required": input.accessibility_required,
            }.items()
            if v is not None
        },
    )
    return response


@app.tool()
@register_tool(domain="wellness", entitlement_action="hold", is_mutating=True)
async def hold_amenity_slot(input: HoldAmenitySlotInput, idempotency_key: str | None = None) -> AmenityHold:
    """Hold an amenity slot."""
    identity = get_current_identity()
    response = await wellness_client.call(
        "POST",
        "/amenities/v3/holds",
        tenant_id=identity.tenant_id,
        response_model=AmenityHold,
        json_body=input.model_dump(exclude_none=True),
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return response


@app.tool()
@register_tool(domain="wellness", entitlement_action="hold", is_mutating=True)
async def release_amenity_hold(input: ReleaseAmenityHoldInput, idempotency_key: str | None = None) -> EmptyResponse:
    """Release an amenity hold."""
    identity = get_current_identity()
    await wellness_client.call(
        "DELETE",
        f"/amenities/v3/holds/{input.hold_id}",
        tenant_id=identity.tenant_id,
        response_model=dict,
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return EmptyResponse(status="released")


@app.tool()
@register_tool(domain="wellness", entitlement_action="commit", is_mutating=True)
async def confirm_amenity_booking(
    input: ConfirmAmenityBookingInput, idempotency_key: str | None = None
) -> AmenityBooking:
    """Confirm (commit) an amenity booking."""
    identity = get_current_identity()
    response = await wellness_client.call(
        "POST",
        "/amenities/v3/bookings",
        tenant_id=identity.tenant_id,
        response_model=AmenityBooking,
        json_body=input.model_dump(exclude_none=True),
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return response


@app.tool()
@register_tool(domain="wellness", entitlement_action="commit", is_mutating=True)
async def cancel_amenity_booking(input: CancelAmenityBookingInput, idempotency_key: str | None = None) -> EmptyResponse:
    """Cancel an amenity booking."""
    identity = get_current_identity()
    await wellness_client.call(
        "DELETE",
        f"/amenities/v3/bookings/{input.booking_id}",
        tenant_id=identity.tenant_id,
        response_model=dict,
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return EmptyResponse(status="cancelled")
