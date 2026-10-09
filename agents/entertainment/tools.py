"""The entertainment subgraph's only door to vendor data: the MCP
entertainment gateway (`mcp/gateway/entertainment/server.py`). No node in
this package ever imports `httpx` or talks to `sims/entertainment` directly
— every call goes through `EntertainmentTools`, matching AGENTS.md's "All
tool access through MCP" commitment.

Mirrors `agents/dining/tools.py`'s already-retrofitted (ADR-021) shape:
`McpEntertainmentTools` delegates to `graph.mcp_client.McpClient`, the same
real streamable-HTTP wire-transport client every other domain's tools
module uses — there is no in-process predecessor to retrofit here, since
this is the first entertainment server built at all
(`entertainment-mcp-tool-contract.md`).

Identity: `tenant_id` is never accepted as a parameter here (AGENTS.md hard
rule — never derive `tenant_id` from model output). `McpEntertainmentTools`
is constructed with the tenant/guest identity once, at subgraph build time,
from `ResortFlowState` (itself set once at conversation entry from a verified
token claim); that identity becomes a bearer token minted once per instance
and sent with every wire call.
"""

from __future__ import annotations

from typing import Any, Protocol

from gateway.entertainment.models import (
    EventDetail,
    InventoryResponse,
    OrderStatus,
    PurchaseResponse,
    RefundResponse,
    SearchEventsResponse,
    SeatHold,
)
from pydantic import BaseModel

from graph.mcp_client import McpClient, McpToolError

ENTERTAINMENT_SCOPES: tuple[str, ...] = ("entertainment:read", "entertainment:hold", "entertainment:commit")
"""Fixed from a previously-invented `"entertainment:mutate"` (feature 008):
the gateway's `SCOPE_RE` (`mcp/gateway/_shared/identity.py`) only ever
accepts `read|hold|commit`, matching `mcp/gateway/entertainment/server.py`'s
own `entitlement_action` per tool (`hold_seats`/`release_seat_hold` are
`hold`; `purchase_tickets`/`request_refund` are `commit`) -- the same
`read`/`hold`/`commit` vocabulary every other domain's own `*_SCOPES` tuple
already uses. `"mutate"` failed `verify_token`'s scope-claim validation on
every real (non-`FakeEntertainmentTools`) call, a 401 invisible until this
domain's real MCP wire path was first exercised."""


class EmptyResponse(BaseModel):
    """`mcp/gateway/entertainment/server.py`'s `release_seat_hold` returns a
    plain `{"status": "released"}` dict rather than a dedicated response
    model (unlike dining's `gateway.dining.models.EmptyResponse`) — defined
    locally here rather than added to the frozen `mcp/gateway/entertainment/`
    package, which this feature does not modify."""

    model_config = {"extra": "forbid"}
    status: str = "released"


PLACEHOLDER_PAYMENT_TOKEN = "resortflow-internal-guest-payment-token"
"""No real payment integration exists in this vertical slice. The ticketing vendor
contract requires a processor-issued `payment_token` on `POST /orders` (raw card data
never enters this system); this stand-in satisfies the shape and is the same value the
stay domain's confirm node sends (`agents/stay/nodes/confirm.py`)."""

DEFAULT_DELIVERY = "mobile"
"""`delivery` is required by the vendor contract (`mobile | will_call | room_charge`);
the gateway defaults it to the same value."""


class EntertainmentToolError(Exception):
    """Raised by `EntertainmentTools` on any vendor/gateway failure. Carries
    exactly the fields `graph.errors.classify()` needs — nodes never
    re-derive them from a raw exception or an HTTP response."""

    def __init__(
        self,
        detail: str,
        *,
        vendor_code: str | None = None,
        http_status: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.vendor_code = vendor_code
        self.http_status = http_status
        self.retry_after_seconds = retry_after_seconds


def idempotency_key(session_id: str, proposal_id: str, attempt: int) -> str:
    """The frozen top-level formula (data-model.md / `_shared/idempotency.py`
    docstring): `resortflow:{session_id}:{hold_id_or_proposal_id}:{attempt}`,
    computed once by the caller and forwarded verbatim. Per
    `entertainment-mcp-tool-contract.md`, this is instantiated per-domain
    (not shared cross-domain) even though it is byte-for-byte identical to
    `agents.dining.tools.idempotency_key`."""
    return f"resortflow:{session_id}:{proposal_id}:{attempt}"


class EntertainmentTools(Protocol):
    """The eight MCP entertainment tools this subgraph is allowed to call.
    Node code depends on this Protocol, never on a concrete client."""

    async def search_events(
        self,
        *,
        classification: str | None = None,
        date: str | None = None,
        tenant_venue: bool | None = None,
    ) -> SearchEventsResponse: ...

    async def get_event_detail(self, *, event_id: str) -> EventDetail: ...

    async def check_inventory(self, *, event_id: str) -> InventoryResponse: ...

    async def hold_seats(
        self,
        *,
        event_id: str,
        section_id: str,
        quantity: int,
        idempotency_key: str,
        ttl_seconds: int | None = None,
    ) -> SeatHold: ...

    async def release_seat_hold(self, *, hold_id: str, idempotency_key: str) -> EmptyResponse: ...

    async def purchase_tickets(
        self, *, hold_id: str, guest: dict[str, Any], idempotency_key: str
    ) -> PurchaseResponse: ...

    async def get_order_status(
        self, *, hold_id: str | None = None, order_id: str | None = None
    ) -> OrderStatus: ...

    async def request_refund(self, *, order_id: str, idempotency_key: str) -> RefundResponse: ...


class McpEntertainmentTools:
    """Real implementation — a thin adapter over `graph.mcp_client.McpClient`
    (ADR-021), talking to `mcp/gateway/entertainment/server.py` over the
    wire. One `McpClient` per instance, scoped to one guest's identity for
    the conversation's lifetime."""

    def __init__(self, *, tenant_id: str, guest_id: str, extra_headers: dict[str, str] | None = None) -> None:
        self._client = McpClient(
            domain="entertainment",
            tenant_id=tenant_id,
            guest_id=guest_id,
            scopes=list(ENTERTAINMENT_SCOPES),
            extra_headers=extra_headers,
        )

    async def _call(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return await self._client.call_tool(tool_name, **kwargs)
        except McpToolError as exc:
            raise EntertainmentToolError(
                exc.detail,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc

    async def search_events(
        self, *, classification: str | None = None, date: str | None = None, tenant_venue: bool | None = None
    ) -> SearchEventsResponse:
        payload = await self._call(
            "search_events", classification=classification, date=date, tenant_venue=tenant_venue
        )
        return SearchEventsResponse.model_validate(payload)

    async def get_event_detail(self, *, event_id: str) -> EventDetail:
        payload = await self._call("get_event_detail", event_id=event_id)
        return EventDetail.model_validate(payload)

    async def check_inventory(self, *, event_id: str) -> InventoryResponse:
        payload = await self._call("check_inventory", event_id=event_id)
        return InventoryResponse.model_validate(payload)

    async def hold_seats(
        self, *, event_id: str, section_id: str, quantity: int, idempotency_key: str, ttl_seconds: int | None = None
    ) -> SeatHold:
        payload = await self._call(
            "hold_seats", event_id=event_id, section_id=section_id, quantity=quantity,
            ttl_seconds=ttl_seconds, idempotency_key=idempotency_key,
        )
        return SeatHold.model_validate(payload)

    async def release_seat_hold(self, *, hold_id: str, idempotency_key: str) -> EmptyResponse:
        payload = await self._call(
            "release_seat_hold", hold_id=hold_id, idempotency_key=idempotency_key
        )
        return EmptyResponse.model_validate(payload)

    async def purchase_tickets(self, *, hold_id: str, guest: dict[str, Any], idempotency_key: str) -> PurchaseResponse:
        """Translate the agent-side call to the gateway's `PurchaseTicketsInput`
        (`hold_id`, `payment_token`, `delivery`; extra fields are forbidden).

        `guest` stays in the signature so the agent-side interface (spec 005) is
        unchanged, but it is deliberately not forwarded: the vendor contract has no
        guest field on `POST /orders` (only an optional `guest_ref` the gateway does
        not expose), and the guest's identity already travels in the verified token
        identity this client authenticates with. Forwarding it made the gateway
        reject every purchase."""
        payload = await self._call(
            "purchase_tickets",
            hold_id=hold_id,
            payment_token=PLACEHOLDER_PAYMENT_TOKEN,
            delivery=DEFAULT_DELIVERY,
            idempotency_key=idempotency_key,
        )
        return PurchaseResponse.model_validate(payload)

    async def get_order_status(self, *, hold_id: str | None = None, order_id: str | None = None) -> OrderStatus:
        payload = await self._call("get_order_status", hold_id=hold_id, order_id=order_id)
        return OrderStatus.model_validate(payload)

    async def request_refund(self, *, order_id: str, idempotency_key: str) -> RefundResponse:
        payload = await self._call(
            "request_refund", order_id=order_id, idempotency_key=idempotency_key
        )
        return RefundResponse.model_validate(payload)
