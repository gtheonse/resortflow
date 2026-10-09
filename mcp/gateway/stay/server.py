"""Seven stay/PMS tools: room ops and checkout timing."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel

from gateway._shared.middleware import get_current_identity
from gateway._shared.registry import SubCallSpec, register_tool
from gateway._shared.upstream import UpstreamClient

from .models import (
    CheckoutResponse,
    EmptyResponse,
    PmsHold,
    RoomReservation,
    SearchRoomsResponse,
    ServiceRequest,
    Stay,
)

app = MCPServer(name="stay", instructions="Stay/PMS tools")
stay_client = UpstreamClient(domain="stay")


class SearchRoomsInput(BaseModel):
    model_config = {"extra": "forbid"}
    arrival: str
    departure: str
    adults: int
    children: int = 0
    room_class: str | None = None


class HoldRoomInput(BaseModel):
    model_config = {"extra": "forbid"}
    OFFER_ID: str
    ARRIVAL: str
    DEPARTURE: str
    TTL_SECONDS: int | None = None


class ReleaseRoomHoldInput(BaseModel):
    model_config = {"extra": "forbid"}
    hold_id: str


class ConfirmRoomReservationInput(BaseModel):
    model_config = {"extra": "forbid"}
    HOLD_ID: str
    GUEST_PROFILE_ID: str
    PAYMENT_TOKEN: str
    SPECIAL_REQUESTS: str | None = None


class GetStayDetailsInput(BaseModel):
    model_config = {"extra": "forbid"}
    stay_id: str


class RequestLateCheckoutInput(BaseModel):
    model_config = {"extra": "forbid"}
    stay_id: str
    REQUESTED_TIME: str | None = None


class CreateServiceRequestInput(BaseModel):
    model_config = {"extra": "forbid"}
    stay_id: str
    REQUEST_TYPE: str
    DETAIL: str | None = None
    REQUESTED_WINDOW: str | None = None
    DIETARY: list[dict] | None = None


@app.tool()
@register_tool(domain="stay", entitlement_action="read", is_mutating=False)
async def search_rooms(input: SearchRoomsInput) -> SearchRoomsResponse:
    """Search available rooms."""
    identity = get_current_identity()
    response = await stay_client.call(
        "GET",
        "/pms/v5/availability/rooms",
        tenant_id=identity.tenant_id,
        response_model=SearchRoomsResponse,
        params={
            "arrival": input.arrival,
            "departure": input.departure,
            "adults": input.adults,
            "children": input.children,
            "room_class": input.room_class,
        },
    )
    return response


@app.tool()
@register_tool(domain="stay", entitlement_action="hold", is_mutating=True)
async def hold_room(input: HoldRoomInput, idempotency_key: str | None = None) -> PmsHold:
    """Hold a room."""
    identity = get_current_identity()
    response = await stay_client.call(
        "POST",
        "/pms/v5/holds/rooms",
        tenant_id=identity.tenant_id,
        response_model=PmsHold,
        json_body=input.model_dump(),
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return response


@app.tool()
@register_tool(domain="stay", entitlement_action="hold", is_mutating=True)
async def release_room_hold(input: ReleaseRoomHoldInput, idempotency_key: str | None = None) -> EmptyResponse:
    """Release a room hold."""
    identity = get_current_identity()
    await stay_client.call(
        "DELETE",
        f"/pms/v5/holds/rooms/{input.hold_id}",
        tenant_id=identity.tenant_id,
        response_model=dict,
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return EmptyResponse(status="released")


@app.tool()
@register_tool(domain="stay", entitlement_action="commit", is_mutating=True)
async def confirm_room_reservation(
    input: ConfirmRoomReservationInput, idempotency_key: str | None = None
) -> RoomReservation:
    """Confirm (commit) a room reservation."""
    identity = get_current_identity()
    response = await stay_client.call(
        "POST",
        "/pms/v5/reservations",
        tenant_id=identity.tenant_id,
        response_model=RoomReservation,
        json_body=input.model_dump(),
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return response


@app.tool()
@register_tool(
    domain="stay",
    entitlement_action="read",
    is_mutating=False,
    sub_calls=[
        SubCallSpec("stay", "getStay", required_for_success=True),
        SubCallSpec("folio", "getFolio", required_for_success=False),
        SubCallSpec("upgrades", "getUpgradeOffers", required_for_success=False),
    ],
)
async def get_stay_details(input: GetStayDetailsInput) -> Stay:
    """Composite: stay + optional folio + upgrade offers."""
    identity = get_current_identity()
    response = await stay_client.call(
        "GET",
        f"/pms/v5/stays/{input.stay_id}",
        tenant_id=identity.tenant_id,
        response_model=Stay,
    )
    return response


@app.tool()
@register_tool(domain="stay", entitlement_action="hold", is_mutating=True)
async def request_late_checkout(
    input: RequestLateCheckoutInput, idempotency_key: str | None = None
) -> CheckoutResponse:
    """Request late checkout (reversible amendment to stay)."""
    identity = get_current_identity()
    response = await stay_client.call(
        "PATCH",
        f"/pms/v5/stays/{input.stay_id}/checkout-time",
        tenant_id=identity.tenant_id,
        response_model=CheckoutResponse,
        json_body={"REQUESTED_TIME": input.REQUESTED_TIME},
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return response


@app.tool()
@register_tool(domain="stay", entitlement_action="hold", is_mutating=True)
async def create_service_request(
    input: CreateServiceRequestInput, idempotency_key: str | None = None
) -> ServiceRequest:
    """Create a service request (room service, housekeeping, etc.)."""
    identity = get_current_identity()
    response = await stay_client.call(
        "POST",
        f"/pms/v5/stays/{input.stay_id}/service-requests",
        tenant_id=identity.tenant_id,
        response_model=ServiceRequest,
        json_body={k: v for k, v in input.model_dump().items() if v is not None and k != "stay_id"},
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return response
