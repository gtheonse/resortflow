"""Eight dining tools: venue discovery through hold/confirm."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from gateway._shared.middleware import get_current_identity
from gateway._shared.registry import SubCallSpec, register_tool
from gateway._shared.upstream import UpstreamClient

from .models import (
    AvailabilityResponse,
    DiningStatus,
    EmptyResponse,
    Hold,
    Reservation,
    VenuesResponse,
    WaitlistResponse,
)

app = MCPServer(name="dining")
dining_client = UpstreamClient(domain="dining")


# --- Input models ----


class ListVenuesInput(BaseModel):
    model_config = {"extra": "forbid"}
    cuisine: str | None = None
    price_band: str | None = None
    supports_dietary: str | None = None


class CheckAvailabilityInput(BaseModel):
    model_config = {"extra": "forbid"}
    venue_id: str
    date: str
    party_size: int
    earliest: str | None = None
    latest: str | None = None


class HoldTableInput(BaseModel):
    model_config = {"extra": "forbid"}
    venue_id: str
    date: str
    time: str
    party_size: int
    ttl_seconds: int | None = None


class ReleaseHoldInput(BaseModel):
    model_config = {"extra": "forbid"}
    hold_id: str


class ConfirmReservationInput(BaseModel):
    model_config = {"extra": "forbid"}
    hold_id: str
    guest: dict
    dietary: list[str] | None = None
    occasion: str | None = None


class CheckDiningStatusInput(BaseModel):
    model_config = {"extra": "forbid"}
    kind: str = Field(
        description="'hold' or 'reservation'",
        pattern="^(hold|reservation)$",
    )
    hold_id: str | None = None
    reservation_id: str | None = None


class CancelReservationInput(BaseModel):
    model_config = {"extra": "forbid"}
    reservation_id: str


class JoinWaitlistInput(BaseModel):
    model_config = {"extra": "forbid"}
    venue_id: str
    party_size: int


# --- Tools ----


@app.tool()
@register_tool(
    domain="dining",
    entitlement_action="read",
    is_mutating=False,
    sub_calls=[
        SubCallSpec("venues", "listVenues", required_for_success=True),
        SubCallSpec("events", "listVenueEvents", required_for_success=False),
    ],
)
async def list_venues(input: ListVenuesInput) -> VenuesResponse:
    """List all venues (composite: venues + optional venue-events enrichment),
    optionally filtered by cuisine, price band, or dietary support (`contracts/
    tool-registry.md`: `list_venues` itself is the composite — there is no
    separate `list_venues_with_events` tool in the frozen 8-tool roster)."""
    identity = get_current_identity()
    response = await dining_client.call(
        "GET",
        "/v1/venues",
        tenant_id=identity.tenant_id,
        response_model=VenuesResponse,
        params={
            k: v for k, v in {
                "cuisine": input.cuisine,
                "price_band": input.price_band,
                "supports_dietary": input.supports_dietary,
            }.items()
            if v is not None
        },
    )
    return response


@app.tool()
@register_tool(domain="dining", entitlement_action="read", is_mutating=False)
async def check_availability(input: CheckAvailabilityInput) -> AvailabilityResponse:
    """Check availability for a venue on a specific date."""
    response = await dining_client.call(
        "GET",
        f"/v1/venues/{input.venue_id}/availability",
        tenant_id=get_current_identity().tenant_id,
        response_model=AvailabilityResponse,
        params={
            k: v for k, v in {
                "date": input.date,
                "party_size": input.party_size,
                "earliest": input.earliest,
                "latest": input.latest,
            }.items()
            if v is not None
        },
    )
    return response


@app.tool()
@register_tool(domain="dining", entitlement_action="hold", is_mutating=True)
async def hold_table(input: HoldTableInput, idempotency_key: str | None = None) -> Hold:
    """Place a table hold."""
    response = await dining_client.call(
        "POST",
        "/v1/holds",
        tenant_id=get_current_identity().tenant_id,
        response_model=Hold,
        json_body={
            "venue_id": input.venue_id,
            "date": input.date,
            "time": input.time,
            "party_size": input.party_size,
            "ttl_seconds": input.ttl_seconds,
        },
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return response


@app.tool()
@register_tool(domain="dining", entitlement_action="hold", is_mutating=True)
async def release_hold(input: ReleaseHoldInput, idempotency_key: str | None = None) -> EmptyResponse:
    """Release a hold."""
    await dining_client.call(
        "DELETE",
        f"/v1/holds/{input.hold_id}",
        tenant_id=get_current_identity().tenant_id,
        response_model=EmptyResponse,
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return EmptyResponse()


@app.tool()
@register_tool(domain="dining", entitlement_action="commit", is_mutating=True)
async def confirm_reservation(
    input: ConfirmReservationInput, idempotency_key: str | None = None
) -> Reservation:
    """Confirm (commit) a held reservation."""
    response = await dining_client.call(
        "POST",
        "/v1/reservations",
        tenant_id=get_current_identity().tenant_id,
        response_model=Reservation,
        json_body={
            "hold_id": input.hold_id,
            "guest": input.guest,
            "dietary": input.dietary or [],
            "occasion": input.occasion,
        },
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return response


@app.tool()
@register_tool(domain="dining", entitlement_action="read", is_mutating=False)
async def check_dining_status(input: CheckDiningStatusInput) -> DiningStatus:
    """Check status of a hold or reservation using explicit `kind` discriminator (F7)."""
    if input.kind == "hold" and input.hold_id:
        response = await dining_client.call(
            "GET",
            f"/v1/holds/{input.hold_id}",
            tenant_id=get_current_identity().tenant_id,
            response_model=Hold,
        )
        return DiningStatus(kind="hold", hold=response)
    elif input.kind == "reservation" and input.reservation_id:
        response = await dining_client.call(
            "GET",
            f"/v1/reservations/{input.reservation_id}",
            tenant_id=get_current_identity().tenant_id,
            response_model=Reservation,
        )
        return DiningStatus(kind="reservation", reservation=response)
    raise ValueError("kind must be 'hold' or 'reservation' with corresponding id")


@app.tool()
@register_tool(domain="dining", entitlement_action="commit", is_mutating=True)
async def cancel_reservation(
    input: CancelReservationInput, idempotency_key: str | None = None
) -> EmptyResponse:
    """Cancel a reservation."""
    await dining_client.call(
        "DELETE",
        f"/v1/reservations/{input.reservation_id}",
        tenant_id=get_current_identity().tenant_id,
        response_model=EmptyResponse,
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return EmptyResponse()


@app.tool()
@register_tool(domain="dining", entitlement_action="hold", is_mutating=True)
async def join_waitlist(input: JoinWaitlistInput, idempotency_key: str | None = None) -> WaitlistResponse:
    """Join a venue waitlist."""
    response = await dining_client.call(
        "POST",
        "/v1/waitlist",
        tenant_id=get_current_identity().tenant_id,
        response_model=WaitlistResponse,
        json_body={"venue_id": input.venue_id, "party_size": input.party_size},
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return response
