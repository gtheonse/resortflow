"""Seed data for dining-sim (T038): 5 MGV / 3 MHB venues, every `DietaryRestriction`
enum value supported by >=1 venue per tenant, per `contracts/seed-data-spec.md`.
"""

from __future__ import annotations

import json
import random

import aiosqlite

SEED = 42

DIETARY_RESTRICTIONS = [
    "shellfish", "tree_nut", "peanut", "dairy", "gluten", "egg", "soy",
    "sesame", "fish", "vegetarian", "vegan", "halal", "kosher", "low_sodium",
]

VENUES_MGV = [
    ("Meridian Steakhouse", "steakhouse", "$$$$", "smart casual", 2, 10, "tower_a"),
    ("Sakura Sushi Bar", "japanese", "$$$", "casual", 1, 8, "tower_b"),
    ("Cactus Flower Cantina", "mexican", "$$", "casual", 1, 12, "poolside"),
    ("The Green Table", "vegetarian", "$$$", "casual", 1, 6, "tower_a"),
    ("Neon Noodle House", "asian_fusion", "$$", "casual", 1, 10, "casino_floor"),
]

VENUES_MHB = [
    ("Harbor Chophouse", "steakhouse", "$$$$", "smart casual", 2, 10, "waterfront"),
    ("North End Trattoria", "italian", "$$$", "casual", 1, 8, "lobby_level"),
    ("Bayside Raw Bar", "seafood", "$$", "casual", 1, 10, "waterfront"),
]

SERVICE_WINDOWS = [
    {"meal": "breakfast", "opens": "07:00", "closes": "10:30"},
    {"meal": "lunch", "opens": "11:30", "closes": "14:30"},
    {"meal": "dinner", "opens": "17:00", "closes": "22:00"},
]


def _distribute_dietary(num_venues: int) -> list[list[str]]:
    """Round-robin the full DietaryRestriction enum across `num_venues` venues so every
    value is supported by at least one venue per tenant, per seed-data-spec.md."""
    buckets: list[list[str]] = [[] for _ in range(num_venues)]
    for i, restriction in enumerate(DIETARY_RESTRICTIONS):
        buckets[i % num_venues].append(restriction)
    return buckets


async def seed(conn: aiosqlite.Connection) -> None:
    rng = random.Random(SEED)

    for tenant_id, venues in (("MGV", VENUES_MGV), ("MHB", VENUES_MHB)):
        dietary_buckets = _distribute_dietary(len(venues))
        for i, (name, cuisine, price_band, dress_code, min_party, max_party, zone) in enumerate(venues):
            venue_id = f"ven-{tenant_id.lower()}-{i + 1:02d}"
            await conn.execute(
                "INSERT OR REPLACE INTO venues "
                "(venue_id, tenant_id, name, cuisine, price_band, dress_code, min_party, max_party, "
                "location_zone, supported_dietary, service_windows) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    venue_id, tenant_id, name, cuisine, price_band, dress_code, min_party, max_party,
                    zone, json.dumps(dietary_buckets[i]), json.dumps(SERVICE_WINDOWS),
                ),
            )
            # A venue-hosted event for a subset of venues.
            if rng.random() < 0.6:
                await conn.execute(
                    "INSERT OR REPLACE INTO venue_events "
                    "(event_id, tenant_id, venue_id, title, date, time, price_per_cover, seats_remaining) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"event-{venue_id}", tenant_id, venue_id, f"{name} Chef's Table",
                        "2026-11-15", "19:00", float(rng.choice([95, 125, 150])), rng.randint(4, 20),
                    ),
                )

    await conn.commit()
