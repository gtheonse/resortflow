"""A deterministic in-memory `StayTools` fake — implements the exact
`agents.stay.tools.StayTools` Protocol so node/graph tests never need a live
`sims/pms` + MCP gateway process. Mirrors `agents/dining/tests/fakes.py`'s
shape; response models use the vendor's own SCREAMING_SNAKE_CASE field names
(`mcp/gateway/stay/models.py`), which this fake reproduces exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gateway.stay.models import (
    CheckoutResponse,
    NightlyRate,
    PmsHold,
    RoomOffer,
    RoomReservation,
    ServiceRequest,
    Stay,
)

from agents.stay.tools import StayToolError

FAKE_OFFERS: list[RoomOffer] = [
    RoomOffer(
        OFFER_ID="offer-mgv-standard-01", ROOM_CLASS="standard", ROOM_DESCRIPTION="Standard Room",
        MAX_OCCUPANCY=2, NIGHTLY_RATES=[NightlyRate(DATE="2026-09-12", AMOUNT=249.0)],
        RESORT_FEE_PER_NIGHT=45.0, CANCELLATION_POLICY="flexible", AVAILABLE_UNITS=10,
    ),
    RoomOffer(
        OFFER_ID="offer-mgv-deluxe-01", ROOM_CLASS="deluxe", ROOM_DESCRIPTION="Deluxe King",
        MAX_OCCUPANCY=3, NIGHTLY_RATES=[NightlyRate(DATE="2026-09-12", AMOUNT=349.0)],
        RESORT_FEE_PER_NIGHT=45.0, CANCELLATION_POLICY="flexible", AVAILABLE_UNITS=6,
    ),
    RoomOffer(
        OFFER_ID="offer-mhb-suite-01", ROOM_CLASS="suite", ROOM_DESCRIPTION="Harbor Suite",
        MAX_OCCUPANCY=4, NIGHTLY_RATES=[NightlyRate(DATE="2026-09-12", AMOUNT=549.0)],
        RESORT_FEE_PER_NIGHT=55.0, CANCELLATION_POLICY="strict", AVAILABLE_UNITS=3,
    ),
]


class FakeStayTools:
    """Implements `agents.stay.tools.StayTools`. Configurable failure
    injection via constructor flags keeps each test/eval case a one-liner."""

    def __init__(
        self,
        *,
        offers: list[RoomOffer] | None = None,
        fail_search: StayToolError | None = None,
        fail_hold: StayToolError | None = None,
        fail_confirm: StayToolError | None = None,
    ) -> None:
        self.offers = offers if offers is not None else FAKE_OFFERS
        self.fail_search = fail_search
        self.fail_hold = fail_hold
        self.fail_confirm = fail_confirm
        self.holds: dict[str, PmsHold] = {}
        self.hold_offer: dict[str, str] = {}
        self.reservations: dict[str, RoomReservation] = {}
        self.released: list[str] = []
        self.calls: list[str] = []

    async def search_rooms(self, *, arrival, departure, adults, children=0, room_class=None):
        self.calls.append("search_rooms")
        if self.fail_search:
            raise self.fail_search
        return [
            o for o in self.offers
            if o.MAX_OCCUPANCY >= adults and (room_class is None or o.ROOM_CLASS == room_class)
        ]

    async def hold_room(self, *, offer_id, arrival, departure, idempotency_key, ttl_seconds=None):
        self.calls.append("hold_room")
        if self.fail_hold:
            raise self.fail_hold
        hold_id = f"hold-{idempotency_key}"
        expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_seconds or 600)).isoformat()
        hold = PmsHold(HOLD_ID=hold_id, OFFER_ID=offer_id, STATUS="active", EXPIRES_AT=expires_at)
        self.holds[hold_id] = hold
        self.hold_offer[hold_id] = offer_id
        return hold

    async def release_room_hold(self, *, hold_id, idempotency_key):
        self.calls.append("release_room_hold")
        self.released.append(hold_id)
        self.holds.pop(hold_id, None)
        return {"status": "released"}

    async def confirm_room_reservation(
        self, *, hold_id, guest_profile_id, payment_token, idempotency_key, special_requests=None
    ):
        self.calls.append("confirm_room_reservation")
        if self.fail_confirm:
            raise self.fail_confirm
        hold = self.holds.get(hold_id)
        if hold is None:
            raise StayToolError("hold not found", vendor_code="HOLD_EXPIRED", http_status=410)
        offer = next((o for o in self.offers if o.OFFER_ID == hold.OFFER_ID), None)
        confirmation_number = f"CONF-{hold_id[:8].upper()}"
        reservation = RoomReservation(
            CONFIRMATION_NUMBER=confirmation_number, STAY_ID=f"stay-{hold_id}",
            ROOM_CLASS=offer.ROOM_CLASS if offer else "standard",
            ARRIVAL="2026-09-12", DEPARTURE="2026-09-13",
            TOTAL_AMOUNT=offer.NIGHTLY_RATES[0].AMOUNT if offer and offer.NIGHTLY_RATES else 0.0,
            STATUS="confirmed",
        )
        self.reservations[confirmation_number] = reservation
        return reservation

    async def get_stay_details(self, *, stay_id):
        self.calls.append("get_stay_details")
        return Stay(
            STAY_ID=stay_id, TENANT_ID="MGV", GUEST_PROFILE_ID="g1", ROOM_NUMBER="101",
            ROOM_CLASS="standard", ARRIVAL="2026-09-12", DEPARTURE="2026-09-13",
            CHECKOUT_TIME="11:00", STATUS="RESERVED", LOCATION_ZONE="main_tower",
        )

    async def request_late_checkout(self, *, stay_id, idempotency_key, requested_time=None):
        self.calls.append("request_late_checkout")
        return CheckoutResponse(GRANTED_TIME=requested_time or "13:00", CHARGE=0.0)

    async def create_service_request(
        self, *, stay_id, request_type, idempotency_key, detail=None, requested_window=None, dietary=None
    ):
        self.calls.append("create_service_request")
        return ServiceRequest(REQUEST_ID=f"req-{stay_id}", ETA_MINUTES=30, STATUS="accepted")
