"""The gaming subgraph's only door to vendor data: the MCP gaming gateway
(`mcp/gateway/gaming/server.py`). No node in this package ever imports
`httpx` or talks to `sims/gaming` directly — every call goes through
`GamingTools`, matching AGENTS.md's "All tool access through MCP" commitment.

Mirrors `agents.dining.tools`/`agents.wellness.tools`'s already-retrofitted
shape (ADR-021, feature 005): `McpGamingTools` delegates to
`graph.mcp_client.McpClient`, the same real streamable-HTTP wire-transport
client every domain's tools module uses.

READ-ONLY BY CONSTRUCTION (AGENTS.md hard rule: "Never let an agent place a
wager or execute a gaming transaction. The gaming domain is read-only."):
`GamingTools` below has exactly three methods, all read-only, matching
`mcp/gateway/gaming/server.py`'s three `@app.tool()`-registered functions
one-for-one. There is no `hold_*`/`confirm_*`/`place_*`/`cancel_*` method on
this Protocol, and none may ever be added — mirroring that gateway module's
own "This module deliberately does NOT import _shared.idempotency" comment:
the guarantee here is enforced by never writing a mutating method, not by
any runtime check. `agents/gaming/tests/test_graph.py` asserts this
structurally (no method name containing a mutation verb), the same spirit
as the gateway's own T055 structural test.

Identity: `tenant_id` is never accepted as a parameter here (AGENTS.md hard
rule — never derive `tenant_id` from model output). `McpGamingTools` is
constructed with the tenant/guest identity once, at subgraph build time,
from `ResortFlowState` (itself set once at conversation entry from a verified
token claim); that identity becomes a bearer token minted once per instance
and sent with every wire call.
"""

from __future__ import annotations

from typing import Any, Protocol

from gateway.gaming.models import FloorGamesResponse, LoyaltyProfile, ResponsibleGamingStatus

from graph.mcp_client import McpClient, McpToolError

GAMING_SCOPES: tuple[str, ...] = ("gaming:read",)
"""No `gaming:hold`/`gaming:commit` scope exists — there is nothing in this
domain that a scope like that could ever authorize."""


class GamingToolError(Exception):
    """Raised by `GamingTools` on any vendor/gateway failure. Carries
    exactly the fields `graph.errors.classify()` needs — nodes never
    re-derive them from a raw exception or an HTTP response. Same shape as
    `agents.dining.tools.DiningToolError` / `agents.wellness.tools.
    WellnessToolError`."""

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


class GamingTools(Protocol):
    """The three MCP gaming tools this subgraph is allowed to call — all
    read-only. Node code depends on this Protocol, never on a concrete
    client.

    Do not add a fourth method. Any mutating capability (placing a wager,
    redeeming a loyalty offer, opting a guest in or out of self-exclusion)
    belongs to a vendor system this project never gives an agent write
    access to — see this module's docstring and AGENTS.md's gaming hard
    rule.
    """

    async def check_rg_status(self, *, guest_id: str) -> ResponsibleGamingStatus: ...

    async def get_loyalty_status(self, *, guest_id: str) -> LoyaltyProfile: ...

    async def list_casino_floor(self, *, game_type: str | None = None) -> FloorGamesResponse: ...


class McpGamingTools:
    """Real implementation — a thin adapter over `graph.mcp_client.McpClient`
    (ADR-021), talking to `mcp/gateway/gaming/server.py` over the wire. One
    `McpClient` per instance, scoped to one guest's identity for the
    conversation's lifetime."""

    def __init__(self, *, tenant_id: str, guest_id: str, extra_headers: dict[str, str] | None = None) -> None:
        self._client = McpClient(
            domain="gaming", tenant_id=tenant_id, guest_id=guest_id, scopes=list(GAMING_SCOPES),
            extra_headers=extra_headers,
        )

    async def _call(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return await self._client.call_tool(tool_name, **kwargs)
        except McpToolError as exc:
            raise GamingToolError(
                exc.detail,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc

    async def check_rg_status(self, *, guest_id: str) -> ResponsibleGamingStatus:
        payload = await self._call("check_rg_status", guest_id=guest_id)
        return ResponsibleGamingStatus.model_validate(payload)

    async def get_loyalty_status(self, *, guest_id: str) -> LoyaltyProfile:
        payload = await self._call("get_loyalty_status", guest_id=guest_id)
        return LoyaltyProfile.model_validate(payload)

    async def list_casino_floor(self, *, game_type: str | None = None) -> FloorGamesResponse:
        payload = await self._call("list_casino_floor", game_type=game_type)
        return FloorGamesResponse.model_validate(payload)
