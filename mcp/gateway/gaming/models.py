"""Pydantic response models for gaming/casino."""

from __future__ import annotations

from pydantic import BaseModel


class ResponsibleGamingStatus(BaseModel):
    model_config = {"extra": "forbid"}
    guest_id: str
    jurisdiction: str
    self_excluded: bool
    exclusion_scope: str | None = None
    limit_flags: list[str]
    offers_permitted: bool
    gaming_content_permitted: bool
    mandated_message: str | None = None
    support_resources: list[dict]


class LoyaltyProfile(BaseModel):
    model_config = {"extra": "forbid"}
    guest_id: str
    tier: str
    points_balance: int
    comp_dollars: float
    tier_expires: str | None = None
    entitlements: list[str]


class LoyaltyOffer(BaseModel):
    model_config = {"extra": "forbid"}
    offer_id: str
    category: str
    description: str
    value: float
    expires: str


class LoyaltyOffersResponse(BaseModel):
    model_config = {"extra": "forbid"}
    offers: list[LoyaltyOffer]


class GameFloor(BaseModel):
    model_config = {"extra": "forbid"}
    game_type: str
    location_zone: str
    table_minimum: float | None = None
    hours: str


class FloorGamesResponse(BaseModel):
    model_config = {"extra": "forbid"}
    games: list[GameFloor]
    mandated_message: str | None = None


class Tournament(BaseModel):
    model_config = {"extra": "forbid"}
    tournament_id: str
    name: str
    start: str
    buy_in: float
    registration_channel: str


class TournamentsResponse(BaseModel):
    model_config = {"extra": "forbid"}
    tournaments: list[Tournament]
    mandated_message: str | None = None
