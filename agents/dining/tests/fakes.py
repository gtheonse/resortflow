"""A deterministic in-memory `DiningTools` fake — implements the exact
`agents.dining.tools.DiningTools` Protocol so node/graph tests never need a
live `sims/dining` + MCP gateway process. Used by unit tests and the eval
runner; `agents/dining/tests/test_tools_live.py` (skipped unless the real
stack is reachable) exercises `McpDiningTools` against the genuine gateway.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from gateway.dining.models import (
    AvailabilityResponse,
    AvailabilitySlot,
    DiningStatus,
    EmptyResponse,
    Reservation,
    Venue,
    VenuesResponse,
    WaitlistResponse,
)
from gateway.dining.models import (
    Hold as VendorHold,
)

from agents.dining.tools import DiningToolError

FAKE_VENUES: list[Venue] = [
    Venue(
        venue_id="ven-mgv-01", tenant_id="MGV", name="Meridian Steakhouse", cuisine="steakhouse",
        price_band="$$$$", dress_code="smart casual", min_party=2, max_party=10,
        location_zone="tower_a", supported_dietary=["dairy"], service_windows=[],
    ),
    Venue(
        venue_id="ven-mgv-02", tenant_id="MGV", name="Sakura Sushi Bar", cuisine="japanese",
        price_band="$$$", dress_code="casual", min_party=1, max_party=8,
        location_zone="tower_b", supported_dietary=["shellfish", "peanut"], service_windows=[],
    ),
    Venue(
        venue_id="ven-mgv-03", tenant_id="MGV", name="Cactus Flower Cantina", cuisine="mexican",
        price_band="$$", dress_code="casual", min_party=1, max_party=12,
        location_zone="poolside", supported_dietary=["vegan"], service_windows=[],
    ),
    Venue(
        venue_id="ven-mgv-04", tenant_id="MGV", name="The Green Table", cuisine="vegetarian",
        price_band="$$$", dress_code="casual", min_party=1, max_party=6,
        location_zone="tower_a", supported_dietary=["vegetarian", "vegan", "kosher"], service_windows=[],
    ),
    Venue(
        venue_id="ven-mgv-05", tenant_id="MGV", name="Neon Noodle House", cuisine="asian_fusion",
        price_band="$$", dress_code="casual", min_party=1, max_party=10,
        location_zone="casino_floor", supported_dietary=["halal"], service_windows=[],
    ),
    Venue(
        venue_id="ven-mhb-01", tenant_id="MHB", name="Harbor Chophouse", cuisine="steakhouse",
        price_band="$$$$", dress_code="smart casual", min_party=2, max_party=10,
        location_zone="waterfront", supported_dietary=["gluten"], service_windows=[],
    ),
    Venue(
        venue_id="ven-mhb-02", tenant_id="MHB", name="North End Trattoria", cuisine="italian",
        price_band="$$$", dress_code="casual", min_party=1, max_party=8,
        location_zone="lobby_level", supported_dietary=["low_sodium"], service_windows=[],
    ),
    Venue(
        venue_id="ven-mhb-03", tenant_id="MHB", name="Bayside Raw Bar", cuisine="seafood",
        price_band="$$", dress_code="casual", min_party=1, max_party=10,
        location_zone="waterfront", supported_dietary=["shellfish"], service_windows=[],
    ),
]


class FakeDiningTools:
    """Implements `agents.dining.tools.DiningTools`. Configurable failure
    injection via constructor flags keeps each eval case a one-liner."""

    def __init__(
        self,
        *,
        tenant_id: str = "MGV",
        venues: list[Venue] | None = None,
        available_times: list[str] | None = None,
        fail_availability: DiningToolError | None = None,
        fail_hold: DiningToolError | None = None,
        fail_confirm: DiningToolError | None = None,
        fail_waitlist: DiningToolError | None = None,
        hold_expired_on_revalidate: bool = False,
    ) -> None:
        self.tenant_id = tenant_id
        self.venues = venues if venues is not None else FAKE_VENUES
        self.available_times = available_times if available_times is not None else ["19:00", "19:30"]
        self.fail_availability = fail_availability
        self.fail_hold = fail_hold
        self.fail_confirm = fail_confirm
        self.fail_waitlist = fail_waitlist
        self.hold_expired_on_revalidate = hold_expired_on_revalidate
        self.holds: dict[str, VendorHold] = {}
        self.reservations: dict[str, Reservation] = {}
        self.released: list[str] = []
        self.waitlisted: list[tuple[str, int]] = []
        self.calls: list[str] = []

    async def list_venues(self, *, cuisine=None, price_band=None, supports_dietary=None):
        self.calls.append("list_venues")
        venues = [
            v for v in self.venues
            if v.tenant_id == self.tenant_id and (cuisine is None or v.cuisine == cuisine)
        ]
        return VenuesResponse(venues=venues)

    async def check_availability(self, *, venue_id, date, party_size, earliest=None, latest=None):
        self.calls.append("check_availability")
        if self.fail_availability:
            raise self.fail_availability
        slots = [
            AvailabilitySlot(time=t, available_covers=40, seating_types=["standard"])
            for t in self.available_times
        ]
        return AvailabilityResponse(venue_id=venue_id, date=date, slots=slots)

    async def hold_table(self, *, venue_id, date, time, party_size, idempotency_key, ttl_seconds=None):
        self.calls.append("hold_table")
        if self.fail_hold:
            raise self.fail_hold
        hold_id = f"hold-{idempotency_key}"
        expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_seconds or 600)).isoformat()
        hold = VendorHold(
            hold_id=hold_id, venue_id=venue_id, date=date, time=time, party_size=party_size,
            status="active", expires_at=expires_at,
        )
        self.holds[hold_id] = hold
        return hold

    async def release_hold(self, *, hold_id, idempotency_key):
        self.calls.append("release_hold")
        self.released.append(hold_id)
        self.holds.pop(hold_id, None)
        return EmptyResponse()

    async def confirm_reservation(self, *, hold_id, guest, idempotency_key, dietary=None, occasion=None):
        self.calls.append("confirm_reservation")
        if self.fail_confirm:
            raise self.fail_confirm
        hold = self.holds.get(hold_id)
        if hold is None:
            raise DiningToolError("hold not found", vendor_code="HOLD_EXPIRED", http_status=410)
        reservation_id = f"res-{hold_id}"
        reservation = Reservation(
            reservation_id=reservation_id, tenant_id=self.tenant_id, venue_id=hold.venue_id,
            venue_name="Fake Venue", date=hold.date, time=hold.time, party_size=hold.party_size,
            status="confirmed", dietary=dietary or [], occasion=occasion,
            confirmation_code=reservation_id[:8].upper(),
            cancellation_deadline=(datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        )
        self.reservations[reservation_id] = reservation
        return reservation

    async def check_dining_status(self, *, kind: Literal["hold", "reservation"], hold_id=None, reservation_id=None):
        self.calls.append("check_dining_status")
        if kind == "hold":
            hold = self.holds.get(hold_id)
            if hold is None or self.hold_expired_on_revalidate:
                return DiningStatus(kind="hold", hold=None)
            return DiningStatus(kind="hold", hold=hold)
        reservation = self.reservations.get(reservation_id)
        return DiningStatus(kind="reservation", reservation=reservation)

    async def cancel_reservation(self, *, reservation_id, idempotency_key):
        self.calls.append("cancel_reservation")
        self.reservations.pop(reservation_id, None)
        return EmptyResponse()

    async def join_waitlist(self, *, venue_id, party_size, idempotency_key):
        self.calls.append("join_waitlist")
        if self.fail_waitlist:
            raise self.fail_waitlist
        self.waitlisted.append((venue_id, party_size))
        return WaitlistResponse(
            waitlist_id=f"wl-{venue_id}", position=len(self.waitlisted), quoted_wait_minutes=15
        )
