"""The wellness subgraph's only door to vendor data: the MCP wellness
gateway (`mcp/gateway/wellness/server.py`). No node in this package ever
imports `httpx` or talks to `sims/wellness` directly — every call goes
through `WellnessTools`, matching AGENTS.md's "All tool access through MCP"
commitment.

Mirrors `agents.dining.tools`'s already-retrofitted shape (ADR-021,
feature 005): `McpWellnessTools` delegates to `graph.mcp_client.McpClient`,
the same real streamable-HTTP wire-transport client every domain's tools
module uses — built directly against that pattern rather than an
in-process precursor, since no such precursor for wellness ever existed.
`agents/wellness/tests/fakes.py`'s `FakeWellnessTools` implements the
`WellnessTools` Protocol directly and is never routed through this class.

Identity: `tenant_id` is never accepted as a parameter here (AGENTS.md hard
rule — never derive `tenant_id` from model output). `McpWellnessTools` is
constructed with the tenant/guest identity once, at subgraph build time,
from `ResortFlowState` (itself set once at conversation entry from a verified
token claim); that identity becomes a bearer token minted once per instance
and sent with every wire call.
"""

from __future__ import annotations

from typing import Any, Protocol

from gateway.wellness.models import (
    AmenityBooking,
    AmenityHold,
    AvailabilityResponse,
    ListServicesResponse,
)

from graph.mcp_client import McpClient, McpToolError

WELLNESS_SCOPES: tuple[str, ...] = ("wellness:read", "wellness:hold", "wellness:commit")


class WellnessToolError(Exception):
    """Raised by `WellnessTools` on any vendor/gateway failure. Carries
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
    computed once by the caller and forwarded verbatim."""
    return f"resortflow:{session_id}:{proposal_id}:{attempt}"


class WellnessTools(Protocol):
    """The six MCP wellness tools this subgraph is allowed to call. Node
    code depends on this Protocol, never on a concrete client."""

    async def list_services(self, *, category: str | None = None) -> ListServicesResponse: ...

    async def check_service_availability(
        self,
        *,
        service_id: str,
        date: str,
        practitioner_gender_preference: str | None = None,
        accessibility_required: bool | None = None,
    ) -> AvailabilityResponse: ...

    async def hold_amenity_slot(
        self,
        *,
        service_id: str,
        date: str,
        time: str,
        practitioner_id: str,
        idempotency_key: str,
        ttl_seconds: int | None = None,
    ) -> AmenityHold: ...

    async def release_amenity_hold(self, *, hold_id: str, idempotency_key: str) -> dict[str, Any]: ...

    async def confirm_amenity_booking(
        self,
        *,
        hold_id: str,
        guest_id: str,
        idempotency_key: str,
        health_declarations: list[str] | None = None,
        accessibility_needs: str | None = None,
    ) -> AmenityBooking: ...

    async def cancel_amenity_booking(self, *, booking_id: str, idempotency_key: str) -> dict[str, Any]: ...


class McpWellnessTools:
    """Real implementation — a thin adapter over `graph.mcp_client.McpClient`
    (ADR-021), talking to `mcp/gateway/wellness/server.py` over the wire.
    One `McpClient` per instance, scoped to one guest's identity for the
    conversation's lifetime."""

    def __init__(self, *, tenant_id: str, guest_id: str, extra_headers: dict[str, str] | None = None) -> None:
        self._client = McpClient(
            domain="wellness", tenant_id=tenant_id, guest_id=guest_id, scopes=list(WELLNESS_SCOPES),
            extra_headers=extra_headers,
        )

    async def _call(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return await self._client.call_tool(tool_name, **kwargs)
        except McpToolError as exc:
            raise WellnessToolError(
                exc.detail,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc

    async def list_services(self, *, category: str | None = None) -> ListServicesResponse:
        payload = await self._call("list_services", category=category)
        return ListServicesResponse.model_validate(payload)

    async def check_service_availability(
        self, *, service_id: str, date: str, practitioner_gender_preference: str | None = None, accessibility_required: bool | None = None
    ) -> AvailabilityResponse:
        payload = await self._call(
            "check_service_availability", service_id=service_id, date=date,
            practitioner_gender_preference=practitioner_gender_preference,
            accessibility_required=accessibility_required,
        )
        return AvailabilityResponse.model_validate(payload)

    async def hold_amenity_slot(
        self, *, service_id: str, date: str, time: str, practitioner_id: str, idempotency_key: str, ttl_seconds: int | None = None
    ) -> AmenityHold:
        payload = await self._call(
            "hold_amenity_slot", service_id=service_id, date=date, time=time,
            practitioner_id=practitioner_id, ttl_seconds=ttl_seconds,
            idempotency_key=idempotency_key,
        )
        return AmenityHold.model_validate(payload)

    async def release_amenity_hold(self, *, hold_id: str, idempotency_key: str) -> dict[str, Any]:
        return await self._call(
            "release_amenity_hold", hold_id=hold_id, idempotency_key=idempotency_key
        )

    async def confirm_amenity_booking(
        self, *, hold_id: str, guest_id: str, idempotency_key: str, health_declarations: list[str] | None = None, accessibility_needs: str | None = None
    ) -> AmenityBooking:
        payload = await self._call(
            "confirm_amenity_booking", hold_id=hold_id, guest_id=guest_id,
            idempotency_key=idempotency_key, health_declarations=health_declarations,
            accessibility_needs=accessibility_needs,
        )
        return AmenityBooking.model_validate(payload)

    async def cancel_amenity_booking(self, *, booking_id: str, idempotency_key: str) -> dict[str, Any]:
        return await self._call(
            "cancel_amenity_booking", booking_id=booking_id, idempotency_key=idempotency_key
        )
