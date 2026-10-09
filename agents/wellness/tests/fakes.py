"""A deterministic in-memory `WellnessTools` fake — implements the exact
`agents.wellness.tools.WellnessTools` Protocol so node/graph tests never
need a live `sims/wellness` + MCP gateway process. Mirrors
`agents.dining.tests.fakes.FakeDiningTools`'s shape and failure-injection
style.

`gateway.wellness.models.Service` carries no `tenant_id` field (unlike
dining's `Venue`) — tenant scoping is simulated here the way the real
gateway does it at the HTTP boundary (an identity-derived tenant filter),
via a `service_id` naming convention (`svc-mgv-*` / `svc-mhb-*`) rather than
a model field that doesn't exist.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from gateway.wellness.models import (
    AmenityBooking,
    AmenityHold,
    AvailabilityResponse,
    AvailabilitySlot,
    ListServicesResponse,
    Service,
)

from agents.wellness.tools import WellnessToolError

FAKE_SERVICES: list[Service] = [
    Service(
        service_id="svc-mgv-spa-01", category="spa", name="Signature Deep Tissue Massage",
        duration_minutes=60, price=180.0, location_zone="spa_zone", contraindications=["pregnancy"],
    ),
    Service(
        service_id="svc-mgv-fitness-01", category="fitness", name="Personal Training Session",
        duration_minutes=45, price=90.0, location_zone="fitness_zone", contraindications=[],
    ),
    Service(
        service_id="svc-mgv-cabana-01", category="cabana", name="Poolside Cabana Half-Day",
        duration_minutes=240, price=350.0, location_zone="cabana_zone", contraindications=[],
    ),
    Service(
        service_id="svc-mgv-golf-01", category="golf", name="18-Hole Tee Time",
        duration_minutes=240, price=220.0, location_zone="golf_zone", contraindications=[],
    ),
    Service(
        service_id="svc-mhb-spa-01", category="spa", name="Harbor Couples Massage",
        duration_minutes=90, price=320.0, location_zone="spa_zone", contraindications=["severe hypertension"],
    ),
    Service(
        service_id="svc-mhb-fitness-01", category="fitness", name="Group Fitness Class",
        duration_minutes=50, price=40.0, location_zone="fitness_zone", contraindications=[],
    ),
    Service(
        service_id="svc-mhb-cabana-01", category="cabana", name="Rooftop Cabana",
        duration_minutes=180, price=260.0, location_zone="cabana_zone", contraindications=[],
    ),
]


class FakeWellnessTools:
    """Implements `agents.wellness.tools.WellnessTools`. Configurable
    failure injection via constructor flags keeps each eval case a
    one-liner."""

    def __init__(
        self,
        *,
        tenant_id: str = "MGV",
        services: list[Service] | None = None,
        available_times: list[str] | None = None,
        fail_availability: WellnessToolError | None = None,
        fail_hold: WellnessToolError | None = None,
        fail_confirm: WellnessToolError | None = None,
        hold_expired_on_revalidate: bool = False,
    ) -> None:
        self.tenant_id = tenant_id
        self.services = services if services is not None else FAKE_SERVICES
        self.available_times = available_times if available_times is not None else ["10:00", "14:00"]
        self.fail_availability = fail_availability
        self.fail_hold = fail_hold
        self.fail_confirm = fail_confirm
        self.hold_expired_on_revalidate = hold_expired_on_revalidate
        self.holds: dict[str, dict] = {}
        self.bookings: dict[str, AmenityBooking] = {}
        self.released: list[str] = []
        self.calls: list[str] = []

    def _tenant_prefix(self) -> str:
        return "svc-mgv-" if self.tenant_id == "MGV" else "svc-mhb-"

    async def list_services(self, *, category=None):
        self.calls.append("list_services")
        prefix = self._tenant_prefix()
        services = [
            s for s in self.services
            if s.service_id.startswith(prefix) and (category is None or s.category == category)
        ]
        return ListServicesResponse(services=services)

    async def check_service_availability(
        self, *, service_id, date, practitioner_gender_preference=None, accessibility_required=None
    ):
        self.calls.append("check_service_availability")
        if self.fail_availability:
            raise self.fail_availability
        # Once a hold exists for this service, `hold_expired_on_revalidate`
        # makes a *second* availability check (confirm's revalidation) see
        # no slots at all — simulating the vendor having expired the hold
        # out from under a resumed conversation.
        if self.hold_expired_on_revalidate and any(
            h["service_id"] == service_id for h in self.holds.values()
        ):
            return AvailabilityResponse(slots=[])
        slots = [
            AvailabilitySlot(
                time=t, practitioner_id=f"prac-{service_id}-{t}",
                practitioner_gender="unspecified", accessible=True,
            )
            for t in self.available_times
        ]
        return AvailabilityResponse(slots=slots)

    async def hold_amenity_slot(
        self, *, service_id, date, time, practitioner_id, idempotency_key, ttl_seconds=None
    ):
        self.calls.append("hold_amenity_slot")
        if self.fail_hold:
            raise self.fail_hold
        hold_id = f"hold-{idempotency_key}"
        expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_seconds or 600)).isoformat()
        self.holds[hold_id] = {
            "service_id": service_id, "date": date, "time": time,
            "practitioner_id": practitioner_id,
        }
        return AmenityHold(hold_id=hold_id, expires_at=expires_at)

    async def release_amenity_hold(self, *, hold_id, idempotency_key):
        self.calls.append("release_amenity_hold")
        self.released.append(hold_id)
        self.holds.pop(hold_id, None)
        return {"status": "released"}

    async def confirm_amenity_booking(
        self, *, hold_id, guest_id, idempotency_key, health_declarations=None, accessibility_needs=None
    ):
        self.calls.append("confirm_amenity_booking")
        if self.fail_confirm:
            raise self.fail_confirm
        hold = self.holds.get(hold_id)
        if hold is None:
            raise WellnessToolError("hold not found", vendor_code="HOLD_EXPIRED", http_status=410)
        booking_id = f"bkg-{hold_id}"
        service = next((s for s in self.services if s.service_id == hold["service_id"]), None)
        booking = AmenityBooking(
            booking_id=booking_id,
            service_name=service.name if service else "Fake Service",
            date=hold["date"],
            time=hold["time"],
            location_zone=service.location_zone if service else "spa_zone",
            arrive_minutes_early=15,
            cancellation_deadline=(datetime.now(UTC) + timedelta(hours=2)).isoformat(),
        )
        self.bookings[booking_id] = booking
        return booking

    async def cancel_amenity_booking(self, *, booking_id, idempotency_key):
        self.calls.append("cancel_amenity_booking")
        self.bookings.pop(booking_id, None)
        return {"status": "cancelled"}
