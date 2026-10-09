"""T023 — eight entertainment/ticketing tools, mirroring `mcp/gateway/stay/server.py`'s
pattern exactly (`contracts/entertainment-mcp-tool-contract.md`). The one wholly new
gateway server this feature adds — tenant-filter construction at the boundary (every
upstream call carries `tenant_id=identity.tenant_id`, taken from the verified
`CallerIdentity`, never from tool input — `_shared.tenancy.assert_no_tenant_field`
enforces the absence of a tenant field on every input model at registration time) is the
never-economy surface `agents/tests/test_tenant_isolation.py` (T067) proves against.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel

from gateway._shared.middleware import get_current_identity
from gateway._shared.registry import register_tool
from gateway._shared.upstream import UpstreamClient

from .models import (
    EmptyResponse,
    EventDetail,
    InventoryResponse,
    Order,
    OrderStatus,
    PurchaseResponse,
    RefundResponse,
    SearchEventsResponse,
    SeatHold,
)

app = MCPServer(name="entertainment", instructions="Entertainment/ticketing tools")
entertainment_client = UpstreamClient(domain="entertainment")
# Every call site below is prefixed with `/discovery/v2`, matching
# sims/ticketing/main.py's own `PREFIX` -- mirroring how every sibling
# domain's server already hardcodes its vendor's full versioned path
# (mcp/gateway/stay/server.py's `/pms/v5/...`, wellness's `/amenities/v3/...`,
# gaming's `/casino/v1/...`); `UpstreamClient`'s shared `VENDOR_BASE_URL`
# deliberately stays bare across every domain, so this is the one place the
# version segment belongs. Was missing entirely until now -- every real
# entertainment call 404'd, masked until feature 008 first fixed the
# separate auth-flow crash (vendor_auth.py) that failed even earlier.


class SearchEventsInput(BaseModel):
    model_config = {"extra": "forbid"}
    classification: str | None = None
    date: str | None = None
    """`YYYY-MM-DD`; expanded to a full-day `start_utc`/`end_utc` window server-side —
    the vendor's `search_events` requires an explicit window, the guest-facing tool
    doesn't need to know that."""
    tenant_venue: bool | None = None
    """`True` -> on-property only, `False` -> city-wide only, `None` -> both."""


class GetEventDetailInput(BaseModel):
    model_config = {"extra": "forbid"}
    event_id: str


class CheckInventoryInput(BaseModel):
    model_config = {"extra": "forbid"}
    event_id: str
    quantity: int = 1


class HoldSeatsInput(BaseModel):
    model_config = {"extra": "forbid"}
    event_id: str
    section_id: str
    quantity: int
    ttl_seconds: int | None = None


class ReleaseSeatHoldInput(BaseModel):
    model_config = {"extra": "forbid"}
    hold_id: str


class PurchaseTicketsInput(BaseModel):
    model_config = {"extra": "forbid"}
    hold_id: str
    payment_token: str
    delivery: str = "mobile"


class GetOrderStatusInput(BaseModel):
    model_config = {"extra": "forbid"}
    hold_id: str | None = None
    order_id: str | None = None


class RequestRefundInput(BaseModel):
    model_config = {"extra": "forbid"}
    order_id: str


def _day_window(date_str: str) -> tuple[str, str]:
    start = datetime.fromisoformat(date_str).replace(tzinfo=UTC)
    end = start + timedelta(days=1)
    return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")


@app.tool()
@register_tool(domain="entertainment", entitlement_action="read", is_mutating=False)
async def search_events(input: SearchEventsInput) -> SearchEventsResponse:
    """Search events by classification/date/on-property scope."""
    identity = get_current_identity()
    if input.date is not None:
        start_utc, end_utc = _day_window(input.date)
    else:
        now = datetime.now(UTC)
        start_utc = now.isoformat().replace("+00:00", "Z")
        end_utc = (now + timedelta(days=365)).isoformat().replace("+00:00", "Z")
    scope = "all"
    if input.tenant_venue is True:
        scope = "on_property"
    elif input.tenant_venue is False:
        scope = "city_wide"
    response = await entertainment_client.call(
        "GET",
        "/discovery/v2/events",
        tenant_id=identity.tenant_id,
        response_model=SearchEventsResponse,
        params={
            "start_utc": start_utc,
            "end_utc": end_utc,
            "scope": scope,
            "classification": input.classification,
        },
    )
    return response


@app.tool()
@register_tool(domain="entertainment", entitlement_action="read", is_mutating=False)
async def get_event_detail(input: GetEventDetailInput) -> EventDetail:
    """Full detail for one event."""
    identity = get_current_identity()
    response = await entertainment_client.call(
        "GET",
        f"/discovery/v2/events/{input.event_id}",
        tenant_id=identity.tenant_id,
        response_model=EventDetail,
    )
    return response


@app.tool()
@register_tool(domain="entertainment", entitlement_action="read", is_mutating=False)
async def check_inventory(input: CheckInventoryInput) -> InventoryResponse:
    """Section-level seat availability for one event."""
    identity = get_current_identity()
    response = await entertainment_client.call(
        "GET",
        f"/discovery/v2/events/{input.event_id}/inventory",
        tenant_id=identity.tenant_id,
        response_model=InventoryResponse,
        params={"quantity": input.quantity, "contiguous_only": True},
    )
    return response


@app.tool()
@register_tool(domain="entertainment", entitlement_action="hold", is_mutating=True)
async def hold_seats(input: HoldSeatsInput, idempotency_key: str | None = None) -> SeatHold:
    """Place a reversible seat hold."""
    identity = get_current_identity()
    response = await entertainment_client.call(
        "POST",
        "/discovery/v2/reservations",
        tenant_id=identity.tenant_id,
        response_model=SeatHold,
        json_body={
            "event_id": input.event_id,
            "section_id": input.section_id,
            "quantity": input.quantity,
            "ttl_seconds": input.ttl_seconds,
        },
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return response


@app.tool()
@register_tool(domain="entertainment", entitlement_action="hold", is_mutating=True)
async def release_seat_hold(input: ReleaseSeatHoldInput, idempotency_key: str | None = None) -> EmptyResponse:
    """Release a seat hold."""
    identity = get_current_identity()
    await entertainment_client.call(
        "DELETE",
        f"/discovery/v2/reservations/{input.hold_id}",
        tenant_id=identity.tenant_id,
        response_model=dict,
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return EmptyResponse(status="released")


@app.tool()
@register_tool(domain="entertainment", entitlement_action="commit", is_mutating=True)
async def purchase_tickets(input: PurchaseTicketsInput, idempotency_key: str | None = None) -> PurchaseResponse:
    """Commit a seat hold into a purchase (async — vendor confirms via webhook)."""
    identity = get_current_identity()
    response = await entertainment_client.call(
        "POST",
        "/discovery/v2/orders",
        tenant_id=identity.tenant_id,
        response_model=PurchaseResponse,
        json_body={
            "hold_id": input.hold_id,
            "payment_token": input.payment_token,
            "delivery": input.delivery,
        },
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return response


@app.tool()
@register_tool(domain="entertainment", entitlement_action="read", is_mutating=False)
async def get_order_status(input: GetOrderStatusInput) -> OrderStatus:
    """Normalized status across the hold-vs-order lifecycle (F7's `kind` discriminator)."""
    identity = get_current_identity()
    if input.order_id is not None:
        order = await entertainment_client.call(
            "GET",
            f"/discovery/v2/orders/{input.order_id}",
            tenant_id=identity.tenant_id,
            response_model=Order,
        )
        return OrderStatus(kind="order", order=order)
    if input.hold_id is not None:
        hold = await entertainment_client.call(
            "GET",
            f"/discovery/v2/reservations/{input.hold_id}",
            tenant_id=identity.tenant_id,
            response_model=SeatHold,
        )
        return OrderStatus(kind="hold", hold=hold)
    raise ValueError("get_order_status requires either hold_id or order_id")


@app.tool()
@register_tool(domain="entertainment", entitlement_action="commit", is_mutating=True)
async def request_refund(input: RequestRefundInput, idempotency_key: str | None = None) -> RefundResponse:
    """Request a refund for a purchased order (REQUIRES_HUMAN if non-refundable)."""
    identity = get_current_identity()
    response = await entertainment_client.call(
        "POST",
        f"/discovery/v2/orders/{input.order_id}/refund",
        tenant_id=identity.tenant_id,
        response_model=RefundResponse,
        idempotency_key=idempotency_key,
        is_mutating=True,
    )
    return response
