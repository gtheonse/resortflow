"""The stay subgraph's only door to vendor data: the MCP stay gateway
(`mcp/gateway/stay/server.py`, the PMS-sim). No node in this package ever
imports `httpx` or talks to `sims/pms` directly — every call goes through
`StayTools`, matching AGENTS.md's "All tool access through MCP" commitment.

Mirrors `agents.dining.tools`'s already-retrofitted shape (ADR-021,
feature 005): `McpStayTools` delegates to `graph.mcp_client.McpClient`, the
same real streamable-HTTP wire-transport client every domain's tools module
uses. `agents/stay/tests/fakes.py`'s `FakeStayTools` implements the
`StayTools` Protocol directly and is never routed through this class.

Identity: `tenant_id` is never accepted as a parameter here (AGENTS.md hard
rule — never derive `tenant_id` from model output). `McpStayTools` is
constructed with the tenant/guest identity once, at subgraph build time,
from `ResortFlowState` (itself set once at conversation entry from a verified
token claim); that identity becomes a bearer token minted once per instance
and sent with every wire call.

Field-name gotcha (see `mcp/gateway/stay/models.py` / `server.py`): the PMS
vendor's own contract uses SCREAMING_SNAKE_CASE field names
(`OFFER_ID`, `HOLD_ID`, `EXPIRES_AT`, `CONFIRMATION_NUMBER`, ...) for the
*mutating* tools' request/response bodies, matching the vendor's own
convention — unlike dining's snake_case. `search_rooms`'s own input model
(`SearchRoomsInput`) and the two `stay_id`/`hold_id`-keyed inputs
(`ReleaseRoomHoldInput`, `GetStayDetailsInput`, `RequestLateCheckoutInput`,
`CreateServiceRequestInput`) stay lowercase because that is what those
input models actually declare — the screaming case is not blanket-applied
to every tool, only to the ones whose fields mirror the vendor's own
`HoldRoomInput`/`ConfirmRoomReservationInput` shapes. `StayTools`'s own
Protocol methods stay snake_case/idiomatic Python throughout; `McpStayTools`
is what performs the translation, tool call by tool call, against each
tool's actual declared input model.
"""

from __future__ import annotations

from typing import Any, Protocol

from gateway.stay.models import (
    CheckoutResponse,
    PmsHold,
    RoomOffer,
    RoomReservation,
    ServiceRequest,
    Stay,
)

from graph.mcp_client import McpClient, McpToolError

STAY_SCOPES: tuple[str, ...] = ("stay:read", "stay:hold", "stay:commit")


class StayToolError(Exception):
    """Raised by `StayTools` on any vendor/gateway failure. Carries exactly
    the fields `graph.errors.classify()` needs — nodes never re-derive them
    from a raw exception or an HTTP response."""

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
    computed once by the caller and forwarded verbatim."""
    return f"resortflow:{session_id}:{proposal_id}:{attempt}"


class StayTools(Protocol):
    """The MCP stay/PMS tools this subgraph is allowed to call. Node code
    depends on this Protocol, never on a concrete client."""

    async def search_rooms(
        self,
        *,
        arrival: str,
        departure: str,
        adults: int,
        children: int = 0,
        room_class: str | None = None,
    ) -> list[RoomOffer]: ...

    async def hold_room(
        self,
        *,
        offer_id: str,
        arrival: str,
        departure: str,
        idempotency_key: str,
        ttl_seconds: int | None = None,
    ) -> PmsHold: ...

    async def release_room_hold(self, *, hold_id: str, idempotency_key: str) -> dict[str, Any]: ...

    async def confirm_room_reservation(
        self,
        *,
        hold_id: str,
        guest_profile_id: str,
        payment_token: str,
        idempotency_key: str,
        special_requests: str | None = None,
    ) -> RoomReservation: ...

    async def get_stay_details(self, *, stay_id: str) -> Stay: ...

    async def request_late_checkout(
        self, *, stay_id: str, idempotency_key: str, requested_time: str | None = None
    ) -> CheckoutResponse: ...

    async def create_service_request(
        self,
        *,
        stay_id: str,
        request_type: str,
        idempotency_key: str,
        detail: str | None = None,
        requested_window: str | None = None,
        dietary: list[dict[str, Any]] | None = None,
    ) -> ServiceRequest: ...


class McpStayTools:
    """Real implementation — a thin adapter over `graph.mcp_client.McpClient`
    (ADR-021), talking to `mcp/gateway/stay/server.py` over the wire. One
    `McpClient` per instance, scoped to one guest's identity for the
    conversation's lifetime."""

    def __init__(self, *, tenant_id: str, guest_id: str, extra_headers: dict[str, str] | None = None) -> None:
        self._client = McpClient(
            domain="stay", tenant_id=tenant_id, guest_id=guest_id, scopes=list(STAY_SCOPES),
            extra_headers=extra_headers,
        )

    async def _call(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return await self._client.call_tool(tool_name, **kwargs)
        except McpToolError as exc:
            raise StayToolError(
                exc.detail,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc

    async def search_rooms(
        self, *, arrival: str, departure: str, adults: int, children: int = 0, room_class: str | None = None
    ) -> list[RoomOffer]:
        # SearchRoomsInput's own fields are lowercase (not vendor-mirrored
        # screaming case — see module docstring). The response, however, is
        # `{"RESPONSE": {"STATUS": ..., "DATA": {"ROOM_OFFERS": [...]}}}`
        # (pms-sim's own envelope, transcribed verbatim in
        # `SearchRoomsResponse.RESPONSE: dict`) — unwrapped here so node code
        # never has to know about the vendor's envelope shape.
        payload = await self._call(
            "search_rooms",
            arrival=arrival,
            departure=departure,
            adults=adults,
            children=children,
            room_class=room_class,
        )
        offers = payload.get("RESPONSE", {}).get("DATA", {}).get("ROOM_OFFERS", [])
        return [RoomOffer.model_validate(o) for o in offers]

    async def hold_room(
        self, *, offer_id: str, arrival: str, departure: str, idempotency_key: str, ttl_seconds: int | None = None
    ) -> PmsHold:
        # HoldRoomInput mirrors the vendor's own PMS field naming —
        # OFFER_ID/ARRIVAL/DEPARTURE/TTL_SECONDS, screaming case.
        payload = await self._call(
            "hold_room",
            OFFER_ID=offer_id,
            ARRIVAL=arrival,
            DEPARTURE=departure,
            TTL_SECONDS=ttl_seconds,
            idempotency_key=idempotency_key,
        )
        return PmsHold.model_validate(payload)

    async def release_room_hold(self, *, hold_id: str, idempotency_key: str) -> dict[str, Any]:
        # ReleaseRoomHoldInput stays lowercase (`hold_id`) — only the
        # hold/confirm *mutation payloads* mirror the vendor's screaming case.
        return await self._call("release_room_hold", hold_id=hold_id, idempotency_key=idempotency_key)

    async def confirm_room_reservation(
        self, *, hold_id: str, guest_profile_id: str, payment_token: str, idempotency_key: str, special_requests: str | None = None
    ) -> RoomReservation:
        # ConfirmRoomReservationInput: HOLD_ID/GUEST_PROFILE_ID/PAYMENT_TOKEN/
        # SPECIAL_REQUESTS, screaming case.
        payload = await self._call(
            "confirm_room_reservation",
            HOLD_ID=hold_id,
            GUEST_PROFILE_ID=guest_profile_id,
            PAYMENT_TOKEN=payment_token,
            SPECIAL_REQUESTS=special_requests,
            idempotency_key=idempotency_key,
        )
        return RoomReservation.model_validate(payload)

    async def get_stay_details(self, *, stay_id: str) -> Stay:
        payload = await self._call("get_stay_details", stay_id=stay_id)
        return Stay.model_validate(payload)

    async def request_late_checkout(self, *, stay_id: str, idempotency_key: str, requested_time: str | None = None) -> CheckoutResponse:
        payload = await self._call(
            "request_late_checkout",
            stay_id=stay_id,
            REQUESTED_TIME=requested_time,
            idempotency_key=idempotency_key,
        )
        return CheckoutResponse.model_validate(payload)

    async def create_service_request(
        self, *, stay_id: str, request_type: str, idempotency_key: str, detail: str | None = None, requested_window: str | None = None, dietary: list[dict[str, Any]] | None = None
    ) -> ServiceRequest:
        payload = await self._call(
            "create_service_request",
            stay_id=stay_id,
            REQUEST_TYPE=request_type,
            DETAIL=detail,
            REQUESTED_WINDOW=requested_window,
            DIETARY=dietary,
            idempotency_key=idempotency_key,
        )
        return ServiceRequest.model_validate(payload)
