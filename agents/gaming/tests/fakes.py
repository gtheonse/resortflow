"""A deterministic in-memory `GamingTools` fake — implements the exact
`agents.gaming.tools.GamingTools` Protocol so node/graph tests never need a
live `sims/gaming` + MCP gateway process. Mirrors
`agents.dining.tests.fakes.FakeDiningTools`'s shape."""

from __future__ import annotations

from gateway.gaming.models import (
    FloorGamesResponse,
    GameFloor,
    LoyaltyProfile,
    ResponsibleGamingStatus,
)

from agents.gaming.tools import GamingToolError

FAKE_RG_STATUSES: dict[str, ResponsibleGamingStatus] = {
    "guest-good-standing": ResponsibleGamingStatus(
        guest_id="guest-good-standing",
        jurisdiction="NV",
        self_excluded=False,
        exclusion_scope=None,
        limit_flags=[],
        offers_permitted=True,
        gaming_content_permitted=True,
        mandated_message=None,
        support_resources=[],
    ),
    "guest-self-excluded": ResponsibleGamingStatus(
        guest_id="guest-self-excluded",
        jurisdiction="NV",
        self_excluded=True,
        exclusion_scope="all_gaming",
        limit_flags=["self_exclusion_active"],
        offers_permitted=False,
        gaming_content_permitted=False,
        mandated_message=(
            "You have an active self-exclusion on file. If you would like support, "
            "please contact the Nevada Council on Problem Gambling at 1-800-522-4700."
        ),
        support_resources=[
            {"name": "Nevada Council on Problem Gambling", "phone": "1-800-522-4700"}
        ],
    ),
}

FAKE_LOYALTY_PROFILES: dict[str, LoyaltyProfile] = {
    "guest-good-standing": LoyaltyProfile(
        guest_id="guest-good-standing",
        tier="gold",
        points_balance=12500,
        comp_dollars=340.50,
        tier_expires="2027-01-01",
        entitlements=["priority_checkin", "late_checkout"],
    ),
    "guest-self-excluded": LoyaltyProfile(
        guest_id="guest-self-excluded",
        tier="silver",
        points_balance=800,
        comp_dollars=0.0,
        tier_expires=None,
        entitlements=[],
    ),
}

FAKE_FLOOR_GAMES: list[GameFloor] = [
    GameFloor(game_type="blackjack", location_zone="main_floor", table_minimum=25.0, hours="24/7"),
    GameFloor(game_type="poker", location_zone="poker_room", table_minimum=50.0, hours="10:00-04:00"),
    GameFloor(game_type="slots", location_zone="main_floor", table_minimum=None, hours="24/7"),
]


class FakeGamingTools:
    """Implements `agents.gaming.tools.GamingTools`. Configurable failure
    injection via constructor flags. No mutating method exists — matching
    the Protocol it fakes."""

    def __init__(
        self,
        *,
        rg_statuses: dict[str, ResponsibleGamingStatus] | None = None,
        loyalty_profiles: dict[str, LoyaltyProfile] | None = None,
        floor_games: list[GameFloor] | None = None,
        floor_mandated_message: str | None = None,
        fail_rg_status: GamingToolError | None = None,
        fail_loyalty: GamingToolError | None = None,
        fail_floor: GamingToolError | None = None,
    ) -> None:
        # Copied, not referenced: a test mutating `self.rg_statuses`/
        # `self.loyalty_profiles` in place (feature 006's freshness tests do
        # this deliberately, simulating a mid-conversation status change)
        # must never corrupt the shared module-level fixture for every
        # other test in the session.
        self.rg_statuses = dict(rg_statuses if rg_statuses is not None else FAKE_RG_STATUSES)
        self.loyalty_profiles = dict(
            loyalty_profiles if loyalty_profiles is not None else FAKE_LOYALTY_PROFILES
        )
        self.floor_games = floor_games if floor_games is not None else FAKE_FLOOR_GAMES
        self.floor_mandated_message = floor_mandated_message
        self.fail_rg_status = fail_rg_status
        self.fail_loyalty = fail_loyalty
        self.fail_floor = fail_floor
        self.calls: list[str] = []

    async def check_rg_status(self, *, guest_id: str) -> ResponsibleGamingStatus:
        self.calls.append("check_rg_status")
        if self.fail_rg_status:
            raise self.fail_rg_status
        status = self.rg_statuses.get(guest_id)
        if status is None:
            status = ResponsibleGamingStatus(
                guest_id=guest_id,
                jurisdiction="NV",
                self_excluded=False,
                exclusion_scope=None,
                limit_flags=[],
                offers_permitted=True,
                gaming_content_permitted=True,
                mandated_message=None,
                support_resources=[],
            )
        return status

    async def get_loyalty_status(self, *, guest_id: str) -> LoyaltyProfile:
        self.calls.append("get_loyalty_status")
        if self.fail_loyalty:
            raise self.fail_loyalty
        profile = self.loyalty_profiles.get(guest_id)
        if profile is None:
            profile = LoyaltyProfile(
                guest_id=guest_id, tier="none", points_balance=0, comp_dollars=0.0,
                tier_expires=None, entitlements=[],
            )
        return profile

    async def list_casino_floor(self, *, game_type: str | None = None) -> FloorGamesResponse:
        self.calls.append("list_casino_floor")
        if self.fail_floor:
            raise self.fail_floor
        games = [g for g in self.floor_games if game_type is None or g.game_type == game_type]
        return FloorGamesResponse(games=games, mandated_message=self.floor_mandated_message)
