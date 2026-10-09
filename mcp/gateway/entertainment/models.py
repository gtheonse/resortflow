"""Pydantic response models for entertainment/ticketing."""

from __future__ import annotations

from pydantic import BaseModel


class Event(BaseModel):
    model_config = {"extra": "forbid"}
    event_id: str
    name: str
    classification: str
    start_utc: str
    doors_utc: str
    duration_minutes: int
    venue: dict
    price_range: dict
    status: str


class EventDetail(Event):
    model_config = {"extra": "forbid"}
    description: str | None = None
    age_restriction: str | None = None
    dress_code: str | None = None
    refundable: bool | None = None


class SearchEventsResponse(BaseModel):
    model_config = {"extra": "forbid"}
    events: list[Event]
    next_page_token: str | None = None


class Section(BaseModel):
    model_config = {"extra": "forbid"}
    section_id: str
    name: str
    price_per_seat: float
    fees_per_seat: float
    available: int
    contiguous_available: int
    view_quality: str


class InventoryResponse(BaseModel):
    model_config = {"extra": "forbid"}
    event_id: str
    sections: list[Section]


class SeatHold(BaseModel):
    model_config = {"extra": "forbid"}
    hold_id: str
    event_id: str
    section_id: str
    quantity: int
    total_amount: float
    status: str
    expires_at: str


class PurchaseResponse(BaseModel):
    model_config = {"extra": "forbid"}
    order_id: str
    status: str
    expected_resolution_seconds: int


class Order(BaseModel):
    model_config = {"extra": "forbid"}
    order_id: str
    status: str
    event_id: str
    quantity: int
    total_charged: float
    seats: list[str]
    failure_reason: str | None = None


class OrderStatus(BaseModel):
    """Normalized union result for get_order_status's `kind` discriminator (F7)."""

    model_config = {"extra": "forbid"}
    kind: str
    hold: SeatHold | None = None
    order: Order | None = None


class RefundResponse(BaseModel):
    model_config = {"extra": "forbid"}
    status: str


class EmptyResponse(BaseModel):
    """For 204-style operations (release/cancel) that return no body."""

    model_config = {"extra": "forbid"}
    status: str = "ok"
