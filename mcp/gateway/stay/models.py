"""Pydantic response models for stay/PMS."""

from __future__ import annotations

from pydantic import BaseModel


class NightlyRate(BaseModel):
    model_config = {"extra": "forbid"}
    DATE: str
    AMOUNT: float


class RoomOffer(BaseModel):
    model_config = {"extra": "forbid"}
    OFFER_ID: str
    ROOM_CLASS: str
    ROOM_DESCRIPTION: str
    MAX_OCCUPANCY: int
    NIGHTLY_RATES: list[NightlyRate]
    RESORT_FEE_PER_NIGHT: float
    CANCELLATION_POLICY: str
    AVAILABLE_UNITS: int


class SearchRoomsResponse(BaseModel):
    model_config = {"extra": "forbid"}
    RESPONSE: dict


class PmsHold(BaseModel):
    model_config = {"extra": "forbid"}
    HOLD_ID: str
    OFFER_ID: str
    STATUS: str
    EXPIRES_AT: str


class RoomReservation(BaseModel):
    model_config = {"extra": "forbid"}
    CONFIRMATION_NUMBER: str
    STAY_ID: str
    ROOM_CLASS: str
    ARRIVAL: str
    DEPARTURE: str
    TOTAL_AMOUNT: float
    STATUS: str


class Stay(BaseModel):
    model_config = {"extra": "forbid"}
    STAY_ID: str
    TENANT_ID: str
    GUEST_PROFILE_ID: str
    ROOM_NUMBER: str
    ROOM_CLASS: str
    ARRIVAL: str
    DEPARTURE: str
    CHECKOUT_TIME: str
    STATUS: str
    LOCATION_ZONE: str


class CheckoutResponse(BaseModel):
    model_config = {"extra": "forbid"}
    GRANTED_TIME: str
    CHARGE: float


class UpgradeOffer(BaseModel):
    model_config = {"extra": "forbid"}
    OFFER_ID: str
    FROM_CLASS: str
    TO_CLASS: str
    NIGHTLY_UPLIFT: float
    AVAILABLE_UNITS: int


class UpgradeOffersResponse(BaseModel):
    model_config = {"extra": "forbid"}
    OFFERS: list[UpgradeOffer]


class ServiceRequest(BaseModel):
    model_config = {"extra": "forbid"}
    REQUEST_ID: str
    ETA_MINUTES: int
    STATUS: str


class Folio(BaseModel):
    model_config = {"extra": "forbid"}
    BALANCE: float
    LINE_ITEMS: list[dict]


class EmptyResponse(BaseModel):
    """For 204-style operations (release/cancel) that return no body."""

    model_config = {"extra": "forbid"}
    status: str = "ok"
