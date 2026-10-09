"""Seed data for ticketing-sim (T040): 24 MGV / 16 MHB events, every `classification`
value covered per tenant, >=1 `sold_out` and >=1 `cancelled` event per tenant, per
`contracts/seed-data-spec.md`.
"""

from __future__ import annotations

import os
import random
from datetime import UTC, date, datetime, timedelta

import aiosqlite

SEED = 42
CLASSIFICATIONS = ["concert", "theater", "comedy", "sports", "residency", "nightlife"]
TENANT_COUNTS = [("MGV", 24), ("MHB", 16)]

VENUE_NAMES = {
    "MGV": [("v-mgv-arena", "Meridian Grand Arena", True, "arena_zone"), ("v-mgv-theater", "Grand Showroom", True, "theater_zone")],
    "MHB": [("v-mhb-hall", "Harbor Hall", True, "hall_zone")],
}
CITY_WIDE_VENUE = {"MGV": ("v-city-mgv", "T-Mobile Arena", False, "las_vegas"), "MHB": ("v-city-mhb", "TD Garden", False, "boston")}

TITLE_WORDS = ["Midnight", "Neon", "Velvet", "Electric", "Golden", "Crimson", "Silver", "Echo", "Solstice", "Quantum"]
TITLE_NOUNS = ["Nights", "Revue", "Live", "Tour", "Sessions", "Showcase", "Unplugged", "Festival"]



def _base_date() -> date:
    """Seeded events roll relative to the seed date (found live, demo report:
    events were hardcoded to November 2026, so "what's on in the next 5 days"
    could never match anything). `SIM_SEED_BASE_DATE` (ISO) pins it for
    reproducible runs."""
    pinned = os.environ.get("SIM_SEED_BASE_DATE")
    return date.fromisoformat(pinned) if pinned else datetime.now(UTC).date()


def _event_day(event_index: int) -> str:
    """Spread events across the 28 days starting tomorrow."""
    return (_base_date() + timedelta(days=(event_index % 28) + 1)).isoformat()


async def seed(conn: aiosqlite.Connection) -> None:
    rng = random.Random(SEED)

    for tenant_id, total in TENANT_COUNTS:
        base_per_class = total // len(CLASSIFICATIONS)
        remainder = total - base_per_class * len(CLASSIFICATIONS)
        event_index = 0
        for cls_i, classification in enumerate(CLASSIFICATIONS):
            count = base_per_class + (1 if cls_i < remainder else 0)
            for _ in range(count):
                event_index += 1
                event_id = f"evt-{tenant_id.lower()}-{event_index:03d}"

                on_property = rng.random() < 0.75  # a minority city-wide
                if on_property:
                    venue_id, venue_name, on_prop_flag, zone = rng.choice(VENUE_NAMES[tenant_id])
                else:
                    venue_id, venue_name, on_prop_flag, zone = CITY_WIDE_VENUE[tenant_id]

                # Reserve the last two events per tenant for the mandatory sold_out /
                # cancelled statuses; everything else is on_sale.
                if event_index == total - 1:
                    status = "sold_out"
                elif event_index == total:
                    status = "cancelled"
                else:
                    status = "on_sale"

                title = f"{rng.choice(TITLE_WORDS)} {rng.choice(TITLE_NOUNS)}"
                price_min = float(rng.choice([35, 45, 65, 85]))
                price_max = price_min + float(rng.choice([50, 100, 150]))

                await conn.execute(
                    "INSERT OR REPLACE INTO events "
                    "(event_id, tenant_id, name, classification, start_utc, doors_utc, duration_minutes, "
                    "venue_id, venue_name, on_property, location_zone, address, price_min, price_max, currency, "
                    "status, description, age_restriction, dress_code, refundable) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event_id, tenant_id, title, classification,
                        f"{_event_day(event_index)}T20:00:00Z",
                        f"{_event_day(event_index)}T19:00:00Z",
                        rng.choice([90, 120, 150, 180]),
                        venue_id, venue_name, int(on_prop_flag), zone, f"{venue_name} address",
                        price_min, price_max, "USD", status, f"{title} — a {classification} experience.",
                        "" if classification != "nightlife" else "21+", "casual", 1,
                    ),
                )

                num_sections = 2
                for s in range(num_sections):
                    section_id = f"{event_id}-sec-{s + 1}"
                    capacity = 0 if status in ("sold_out", "cancelled") else rng.choice([50, 100, 200])
                    await conn.execute(
                        "INSERT OR REPLACE INTO sections "
                        "(section_id, event_id, tenant_id, name, price_per_seat, fees_per_seat, capacity, view_quality) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            section_id, event_id, tenant_id, "Floor" if s == 0 else "Balcony",
                            price_min + s * 20.0, 10.0, capacity, "premium" if s == 0 else "standard",
                        ),
                    )

    await conn.commit()
