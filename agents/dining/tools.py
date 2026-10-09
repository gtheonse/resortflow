"""The dining subgraph's only door to vendor data: the MCP dining gateway
(`mcp/gateway/dining/server.py`). No node in this package ever imports
`httpx` or talks to `sims/dining` directly — every call goes through
`DiningTools`, matching AGENTS.md's "All tool access through MCP"
commitment.

Retrofit (ADR-021, feature 005): `McpDiningTools` previously invoked the
gateway's `@register_tool`-wrapped functions in-process, because nothing in
Phase 4 hosted a second process to put a real client/server pair between.
Feature 005's supervisor is that second process — `McpDiningTools` now
delegates to `graph.mcp_client.McpClient`, the same real streamable-HTTP
wire-transport client every other domain's tools module uses, closing
the open question of whether a live MCP wire-transport integration test should
exist before feature 005 assumes it works (see ADR-021). `agents/dining/tests/fakes.py`'s `FakeDiningTools` implements the
`DiningTools` Protocol directly and was never routed through this class, so
the eval loop's 25 cases are unaffected by this retrofit (T013).

Identity: `tenant_id` is never accepted as a parameter here (AGENTS.md hard
rule — never derive `tenant_id` from model output). `McpDiningTools` is
constructed with the tenant/guest identity once, at subgraph build time,
from `ResortFlowState` (itself set once at conversation entry from a verified
token claim); that identity becomes a bearer token minted once per
instance and sent with every wire call — the wire-transport equivalent of
what `AuthenticationMiddleware` verifies for every real HTTP request.
"""

from __future__ import annotations

from typing import Literal, Protocol

from gateway.dining.models import (
    AvailabilityResponse,
    DiningStatus,
    EmptyResponse,
    Reservation,
    VenuesResponse,
    WaitlistResponse,
)
from gateway.dining.models import (
    Hold as VendorHold,
)

from graph.mcp_client import McpClient, McpToolError

DINING_SCOPES: tuple[str, ...] = ("dining:read", "dining:hold", "dining:commit")


class DiningToolError(Exception):
    """Raised by `DiningTools` on any vendor/gateway failure. Carries exactly
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


class DiningTools(Protocol):
    """The nine MCP dining tools this subgraph is allowed to call. Node code
    depends on this Protocol, never on a concrete client — the allergen
    checker's `query_venue_menu` stub follows the identical spirit for RAG."""

    async def list_venues(
        self,
        *,
        cuisine: str | None = None,
        price_band: str | None = None,
        supports_dietary: str | None = None,
    ) -> VenuesResponse: ...

    async def check_availability(
        self,
        *,
        venue_id: str,
        date: str,
        party_size: int,
        earliest: str | None = None,
        latest: str | None = None,
    ) -> AvailabilityResponse: ...

    async def hold_table(
        self,
        *,
        venue_id: str,
        date: str,
        time: str,
        party_size: int,
        idempotency_key: str,
        ttl_seconds: int | None = None,
    ) -> VendorHold: ...

    async def release_hold(self, *, hold_id: str, idempotency_key: str) -> EmptyResponse: ...

    async def confirm_reservation(
        self,
        *,
        hold_id: str,
        guest: dict,
        idempotency_key: str,
        dietary: list[str] | None = None,
        occasion: str | None = None,
    ) -> Reservation: ...

    async def check_dining_status(
        self,
        *,
        kind: Literal["hold", "reservation"],
        hold_id: str | None = None,
        reservation_id: str | None = None,
    ) -> DiningStatus: ...

    async def cancel_reservation(
        self, *, reservation_id: str, idempotency_key: str
    ) -> EmptyResponse: ...

    async def join_waitlist(
        self, *, venue_id: str, party_size: int, idempotency_key: str
    ) -> WaitlistResponse: ...


class McpDiningTools:
    """Real implementation — a thin adapter over `graph.mcp_client.McpClient`
    (ADR-021), talking to `mcp/gateway/dining/server.py` over the wire
    rather than in-process. One `McpClient` per instance, scoped to one
    guest's identity for the conversation's lifetime."""

    def __init__(self, *, tenant_id: str, guest_id: str, extra_headers: dict[str, str] | None = None) -> None:
        self._client = McpClient(
            domain="dining", tenant_id=tenant_id, guest_id=guest_id, scopes=list(DINING_SCOPES),
            extra_headers=extra_headers,
        )

    async def _call(self, tool_name: str, **kwargs) -> dict:
        try:
            return await self._client.call_tool(tool_name, **kwargs)
        except McpToolError as exc:
            raise DiningToolError(
                exc.detail,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc

    async def list_venues(
        self, *, cuisine=None, price_band=None, supports_dietary=None
    ) -> VenuesResponse:
        payload = await self._call(
            "list_venues", cuisine=cuisine, price_band=price_band, supports_dietary=supports_dietary
        )
        return VenuesResponse.model_validate(payload)

    async def check_availability(
        self, *, venue_id, date, party_size, earliest=None, latest=None
    ) -> AvailabilityResponse:
        payload = await self._call(
            "check_availability", venue_id=venue_id, date=date, party_size=party_size,
            earliest=earliest, latest=latest,
        )
        return AvailabilityResponse.model_validate(payload)

    async def hold_table(
        self, *, venue_id, date, time, party_size, idempotency_key, ttl_seconds=None
    ) -> VendorHold:
        payload = await self._call(
            "hold_table", venue_id=venue_id, date=date, time=time, party_size=party_size,
            ttl_seconds=ttl_seconds, idempotency_key=idempotency_key,
        )
        return VendorHold.model_validate(payload)

    async def release_hold(self, *, hold_id, idempotency_key) -> EmptyResponse:
        payload = await self._call("release_hold", hold_id=hold_id, idempotency_key=idempotency_key)
        return EmptyResponse.model_validate(payload)

    async def confirm_reservation(
        self, *, hold_id, guest, idempotency_key, dietary=None, occasion=None
    ) -> Reservation:
        payload = await self._call(
            "confirm_reservation", hold_id=hold_id, guest=guest, idempotency_key=idempotency_key,
            dietary=dietary, occasion=occasion,
        )
        return Reservation.model_validate(payload)

    async def check_dining_status(
        self, *, kind, hold_id=None, reservation_id=None
    ) -> DiningStatus:
        payload = await self._call(
            "check_dining_status", kind=kind, hold_id=hold_id, reservation_id=reservation_id,
        )
        return DiningStatus.model_validate(payload)

    async def cancel_reservation(self, *, reservation_id, idempotency_key) -> EmptyResponse:
        payload = await self._call(
            "cancel_reservation", reservation_id=reservation_id, idempotency_key=idempotency_key
        )
        return EmptyResponse.model_validate(payload)

    async def join_waitlist(self, *, venue_id, party_size, idempotency_key) -> WaitlistResponse:
        payload = await self._call(
            "join_waitlist", venue_id=venue_id, party_size=party_size, idempotency_key=idempotency_key
        )
        return WaitlistResponse.model_validate(payload)
