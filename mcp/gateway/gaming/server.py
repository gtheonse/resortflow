"""Three gaming/casino tools — read-only by construction (T034, T055)."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel

from gateway._shared.errors import UpstreamFailure
from gateway._shared.middleware import get_current_identity
from gateway._shared.registry import SubCallSpec, register_tool
from gateway._shared.upstream import UpstreamClient

from .models import (
    FloorGamesResponse,
    LoyaltyOffersResponse,
    LoyaltyProfile,
    ResponsibleGamingStatus,
)

# NOTE: This module deliberately does NOT import _shared.idempotency. Gaming tools
# are read-only: no mutating tool exists, so no idempotency key is ever needed.
# The absence of this import is verified by T055 as a structural test of the
# read-only guarantee (SC-011).

app = MCPServer(name="gaming", instructions="Gaming/casino tools (read-only)")
gaming_client = UpstreamClient(domain="gaming")


class CheckRgStatusInput(BaseModel):
    model_config = {"extra": "forbid"}
    guest_id: str


class GetLoyaltyStatusInput(BaseModel):
    model_config = {"extra": "forbid"}
    guest_id: str


class ListCasinoFloorInput(BaseModel):
    model_config = {"extra": "forbid"}
    game_type: str | None = None


@app.tool()
@register_tool(domain="gaming", entitlement_action="read", is_mutating=False)
async def check_rg_status(input: CheckRgStatusInput) -> ResponsibleGamingStatus:
    """Check responsible gaming status (policy-critical, must pass before gaming content)."""
    identity = get_current_identity()
    response = await gaming_client.call(
        "GET",
        "/casino/v1/responsible-gaming/status",
        tenant_id=identity.tenant_id,
        response_model=ResponsibleGamingStatus,
        params={"guest_id": input.guest_id},
    )
    return response


@app.tool()
@register_tool(
    domain="gaming",
    entitlement_action="read",
    is_mutating=False,
    sub_calls=[
        SubCallSpec("profile", "getLoyaltyProfile", required_for_success=True),
        SubCallSpec("offers", "getLoyaltyOffers", required_for_success=False),
    ],
)
async def get_loyalty_status(input: GetLoyaltyStatusInput) -> LoyaltyProfile:
    """Composite: loyalty profile + optional offers (non-gaming comp only, respects
    403->POLICY_BLOCKED).

    The offers sub-call (`getLoyaltyOffers`) is `required_for_success=False` for
    ordinary vendor trouble (a transient offers-service hiccup should not fail the
    whole loyalty lookup) -- but a `POLICY_BLOCKED` outcome from it (casino-sim's
    403 when responsible-gaming has suppressed offers for a self-excluded guest,
    `errors.classify_status`'s gaming+403 case) is never swallowed regardless of
    that flag: responsible-gaming suppression is compliance-critical (constitution
    Principle IV) and must surface to the caller, not be silently dropped as an
    "optional enrichment failed" case. `LoyaltyProfile` carries no offers field, so
    a blocked lookup surfaces as the tool call itself failing POLICY_BLOCKED with
    no offers payload at all, rather than a profile response with an empty list.
    """
    identity = get_current_identity()
    response = await gaming_client.call(
        "GET",
        f"/casino/v1/loyalty/{input.guest_id}",
        tenant_id=identity.tenant_id,
        response_model=LoyaltyProfile,
    )
    try:
        await gaming_client.call(
            "GET",
            f"/casino/v1/loyalty/{input.guest_id}/offers",
            tenant_id=identity.tenant_id,
            response_model=LoyaltyOffersResponse,
        )
    except UpstreamFailure as exc:
        if exc.error_class == "POLICY_BLOCKED":
            raise
        # Any other offers-side failure is a non-fatal enrichment miss --
        # `required_for_success=False` per this tool's `SubCallSpec`.
    return response


@app.tool()
@register_tool(
    domain="gaming",
    entitlement_action="read",
    is_mutating=False,
    sub_calls=[
        SubCallSpec("games", "listFloorGames", required_for_success=True),
        SubCallSpec("tournaments", "listTournaments", required_for_success=False),
    ],
)
async def list_casino_floor(input: ListCasinoFloorInput) -> FloorGamesResponse:
    """Composite: floor games + optional tournaments (informational only)."""
    identity = get_current_identity()
    response = await gaming_client.call(
        "GET",
        "/casino/v1/floor/games",
        tenant_id=identity.tenant_id,
        response_model=FloorGamesResponse,
        params={"game_type": input.game_type} if input.game_type else {},
    )
    return response
