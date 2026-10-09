"""A deterministic in-memory `EntertainmentTools` fake — implements the
exact `agents.entertainment.tools.EntertainmentTools` Protocol so node/graph
tests never need a live `sims/entertainment` + MCP gateway process. Mirrors
`agents/dining/tests/fakes.py`'s `FakeDiningTools` pattern.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gateway.entertainment.models import (
    Event,
    EventDetail,
    InventoryResponse,
    Order,
    OrderStatus,
    PurchaseResponse,
    RefundResponse,
    SearchEventsResponse,
    SeatHold,
    Section,
)

from agents.entertainment.tools import EmptyResponse, EntertainmentToolError

FAKE_EVENTS: list[Event] = [
    Event(
        event_id="evt-mgv-01",
        name="Neon Nights Arena Tour",
        classification="concert",  # matches sims/ticketing's real seed vocabulary
        # (verified: SELECT DISTINCT classification FROM events) and the
        # corrected intent_parser.py mapping -- was "music", a value the
        # real vendor never actually uses, until feature 008 found the
        # mismatch by exercising the real MCP path for the first time.
        start_utc="2026-09-13T02:00:00Z",
        doors_utc="2026-09-13T01:00:00Z",
        duration_minutes=120,
        venue={"tenant_id": "MGV", "name": "Meridian Arena", "zone": "arena_zone"},
        price_range={"min": 80, "max": 400},
        status="on_sale",
    ),
    Event(
        event_id="evt-mgv-02",
        name="Grand Illusion Theater Show",
        classification="theater",
        start_utc="2026-09-13T01:30:00Z",
        doors_utc="2026-09-13T00:45:00Z",
        duration_minutes=100,
        venue={"tenant_id": "MGV", "name": "Meridian Theater", "zone": "theater_zone"},
        price_range={"min": 60, "max": 250},
        status="on_sale",
    ),
    Event(
        event_id="evt-mhb-01",
        name="Harbor Comedy Night",
        classification="comedy",
        start_utc="2026-09-13T00:30:00Z",
        doors_utc="2026-09-12T23:45:00Z",
        duration_minutes=90,
        venue={"tenant_id": "MHB", "name": "Harbor Hall", "zone": "hall_zone"},
        price_range={"min": 40, "max": 150},
        status="on_sale",
    ),
]


class FakeEntertainmentTools:
    """Implements `agents.entertainment.tools.EntertainmentTools`.
    Configurable failure injection via constructor flags keeps each eval
    case a one-liner."""

    def __init__(
        self,
        *,
        tenant_id: str = "MGV",
        events: list[Event] | None = None,
        section_available: int = 40,
        fail_search: EntertainmentToolError | None = None,
        fail_inventory: EntertainmentToolError | None = None,
        fail_hold: EntertainmentToolError | None = None,
        fail_purchase: EntertainmentToolError | None = None,
        hold_expired_on_revalidate: bool = False,
    ) -> None:
        self.tenant_id = tenant_id
        self.events = events if events is not None else FAKE_EVENTS
        self.section_available = section_available
        self.fail_search = fail_search
        self.fail_inventory = fail_inventory
        self.fail_hold = fail_hold
        self.fail_purchase = fail_purchase
        self.hold_expired_on_revalidate = hold_expired_on_revalidate
        self.holds: dict[str, SeatHold] = {}
        self.orders: dict[str, Order] = {}
        self.released: list[str] = []
        self.calls: list[str] = []

    def _tenant_events(self) -> list[Event]:
        return [e for e in self.events if e.venue.get("tenant_id") == self.tenant_id]

    async def search_events(self, *, classification=None, date=None, tenant_venue=None):
        self.calls.append("search_events")
        if self.fail_search:
            raise self.fail_search
        events = [
            e
            for e in self._tenant_events()
            if classification is None or e.classification == classification
        ]
        return SearchEventsResponse(events=events)

    async def get_event_detail(self, *, event_id):
        self.calls.append("get_event_detail")
        event = next(e for e in self.events if e.event_id == event_id)
        return EventDetail(**event.model_dump())

    async def check_inventory(self, *, event_id):
        self.calls.append("check_inventory")
        if self.fail_inventory:
            raise self.fail_inventory
        sections = [
            Section(
                section_id=f"sec-{event_id}-a",
                name="Floor",
                price_per_seat=150.0,
                fees_per_seat=15.0,
                available=self.section_available,
                contiguous_available=self.section_available,
                view_quality="excellent",
            ),
            Section(
                section_id=f"sec-{event_id}-b",
                name="Upper Deck",
                price_per_seat=75.0,
                fees_per_seat=10.0,
                available=self.section_available,
                contiguous_available=self.section_available,
                view_quality="good",
            ),
        ]
        return InventoryResponse(event_id=event_id, sections=sections)

    async def hold_seats(self, *, event_id, section_id, quantity, idempotency_key, ttl_seconds=None):
        self.calls.append("hold_seats")
        if self.fail_hold:
            raise self.fail_hold
        hold_id = f"hold-{idempotency_key}"
        expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_seconds or 600)).isoformat()
        hold = SeatHold(
            hold_id=hold_id,
            event_id=event_id,
            section_id=section_id,
            quantity=quantity,
            total_amount=quantity * 90.0,
            status="active",
            expires_at=expires_at,
        )
        self.holds[hold_id] = hold
        return hold

    async def release_seat_hold(self, *, hold_id, idempotency_key):
        self.calls.append("release_seat_hold")
        self.released.append(hold_id)
        self.holds.pop(hold_id, None)
        return EmptyResponse()

    async def purchase_tickets(self, *, hold_id, guest, idempotency_key):
        self.calls.append("purchase_tickets")
        if self.fail_purchase:
            raise self.fail_purchase
        hold = self.holds.get(hold_id)
        if hold is None:
            raise EntertainmentToolError("hold not found", vendor_code="HOLD_EXPIRED", http_status=410)
        order_id = f"order-{hold_id}"
        order = Order(
            order_id=order_id,
            status="pending",
            event_id=hold.event_id,
            quantity=hold.quantity,
            total_charged=hold.total_amount,
            seats=[f"seat-{i}" for i in range(hold.quantity)],
        )
        self.orders[order_id] = order
        return PurchaseResponse(order_id=order_id, status="PENDING", expected_resolution_seconds=30)

    async def get_order_status(self, *, hold_id=None, order_id=None):
        self.calls.append("get_order_status")
        if order_id is not None:
            return OrderStatus(kind="order", order=self.orders.get(order_id))
        hold = self.holds.get(hold_id)
        if hold is None or self.hold_expired_on_revalidate:
            return OrderStatus(kind="hold", hold=None)
        return OrderStatus(kind="hold", hold=hold)

    async def request_refund(self, *, order_id, idempotency_key):
        self.calls.append("request_refund")
        order = self.orders.get(order_id)
        if order is not None:
            self.orders[order_id] = order.model_copy(update={"status": "refunded"})
        return RefundResponse(status="refunded")
