"""Pydantic response models for wellness/amenities."""

from __future__ import annotations

from pydantic import BaseModel


class Service(BaseModel):
    model_config = {"extra": "forbid"}
    service_id: str
    category: str
    name: str
    duration_minutes: int
    price: float
    location_zone: str
    contraindications: list[str]


class ListServicesResponse(BaseModel):
    model_config = {"extra": "forbid"}
    services: list[Service]


class AvailabilitySlot(BaseModel):
    model_config = {"extra": "forbid"}
    time: str
    practitioner_id: str
    practitioner_gender: str
    accessible: bool


class AvailabilityResponse(BaseModel):
    model_config = {"extra": "forbid"}
    slots: list[AvailabilitySlot]


class AmenityHold(BaseModel):
    model_config = {"extra": "forbid"}
    hold_id: str
    expires_at: str


class AmenityBooking(BaseModel):
    model_config = {"extra": "forbid"}
    booking_id: str
    service_name: str
    date: str
    time: str
    location_zone: str
    arrive_minutes_early: int
    cancellation_deadline: str


class EmptyResponse(BaseModel):
    """For 204-style operations (release/cancel) that return no body."""

    model_config = {"extra": "forbid"}
    status: str = "ok"
