"""Pydantic response models for dining, mirroring
`docs/contracts/openapi/dining-sim.openapi.yaml` exactly (ADR-014).
"""

from __future__ import annotations

from pydantic import BaseModel


class ServiceWindow(BaseModel):
    model_config = {"extra": "forbid"}
    # dining-sim's service_windows is stored as a JSON blob per venue row; shape is
    # vendor-internal and not spelled out field-by-field in the frozen contract beyond
    # "an array" — kept permissive at the item level via dict passthrough below.


class Venue(BaseModel):
    model_config = {"extra": "forbid"}
    venue_id: str
    tenant_id: str
    name: str
    cuisine: str
    price_band: str
    dress_code: str
    min_party: int
    max_party: int
    location_zone: str
    supported_dietary: list[str]
    service_windows: list[dict]


class VenuesResponse(BaseModel):
    model_config = {"extra": "forbid"}
    venues: list[Venue]


class AvailabilitySlot(BaseModel):
    model_config = {"extra": "forbid"}
    time: str
    available_covers: int
    seating_types: list[str]


class AvailabilityResponse(BaseModel):
    model_config = {"extra": "forbid"}
    venue_id: str
    date: str
    slots: list[AvailabilitySlot]


class Hold(BaseModel):
    model_config = {"extra": "forbid"}
    hold_id: str
    venue_id: str
    date: str
    time: str
    party_size: int
    status: str
    expires_at: str


class Reservation(BaseModel):
    model_config = {"extra": "forbid"}
    reservation_id: str
    tenant_id: str
    venue_id: str
    venue_name: str
    date: str
    time: str
    party_size: int
    status: str
    dietary: list[str]
    occasion: str | None = None
    confirmation_code: str
    cancellation_deadline: str


class WaitlistResponse(BaseModel):
    model_config = {"extra": "forbid"}
    waitlist_id: str
    position: int
    quoted_wait_minutes: int


class VenueEvent(BaseModel):
    model_config = {"extra": "forbid"}
    event_id: str
    venue_id: str
    title: str
    date: str
    time: str
    price_per_cover: float
    seats_remaining: int


class VenueEventsResponse(BaseModel):
    model_config = {"extra": "forbid"}
    events: list[VenueEvent]


class DiningStatus(BaseModel):
    """Normalized union result for check_dining_status's `kind` discriminator (F7) —
    the tool returns whichever shape applies, both as one typed model for MCP schema
    publication purposes."""

    model_config = {"extra": "forbid"}
    kind: str
    hold: Hold | None = None
    reservation: Reservation | None = None


class EmptyResponse(BaseModel):
    """For 204-style operations (release_hold, cancel_reservation) that return no body."""

    model_config = {"extra": "forbid"}
    status: str = "ok"
