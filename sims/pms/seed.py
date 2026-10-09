"""Seed data for pms-sim (T039): 120 MGV / 80 MHB room offers across all four
`room_class` values, weighted toward STANDARD/DELUXE, per `contracts/seed-data-spec.md`.

`AVAILABLE_UNITS` per offer is set so total capacity matches the tenant split exactly —
not 200 independently-available units per offer row (seed-data-spec.md).
"""

from __future__ import annotations

import aiosqlite

ROOM_CLASSES = ["STANDARD", "DELUXE", "SUITE", "VILLA"]

DESCRIPTIONS = {
    "STANDARD": "Standard Queen Room",
    "DELUXE": "Deluxe King Room",
    "SUITE": "One-Bedroom Suite",
    "VILLA": "Private Pool Villa",
}
RATES = {"STANDARD": 189.0, "DELUXE": 259.0, "SUITE": 459.0, "VILLA": 1200.0}
MAX_OCCUPANCY = {"STANDARD": 2, "DELUXE": 3, "SUITE": 4, "VILLA": 6}
RESORT_FEE = {"STANDARD": 35.0, "DELUXE": 35.0, "SUITE": 45.0, "VILLA": 0.0}
CANCELLATION = {"STANDARD": "flex_48h", "DELUXE": "flex_48h", "SUITE": "flex_72h", "VILLA": "non_refundable"}

# Weighted toward STANDARD/DELUXE, two offer rows per class (a "product" split, e.g.
# city-view vs. strip-view), summing exactly to the tenant total.
TENANT_UNIT_TOTALS = {
    "MGV": {"STANDARD": 45, "DELUXE": 40, "SUITE": 25, "VILLA": 10},
    "MHB": {"STANDARD": 30, "DELUXE": 26, "SUITE": 16, "VILLA": 8},
}


async def seed(conn: aiosqlite.Connection) -> None:
    for tenant_id, totals in TENANT_UNIT_TOTALS.items():
        assert sum(totals.values()) == (120 if tenant_id == "MGV" else 80)
        for room_class in ROOM_CLASSES:
            total_units = totals[room_class]
            # Split into two offer rows per class so a class isn't represented by a
            # single monolithic row — half rounded, remainder to the second row.
            first_half = total_units // 2
            second_half = total_units - first_half
            for i, units in enumerate((first_half, second_half)):
                if units <= 0:
                    continue
                offer_id = f"offer-{tenant_id.lower()}-{room_class.lower()}-{i + 1}"
                await conn.execute(
                    "INSERT OR REPLACE INTO room_offers "
                    "(offer_id, tenant_id, room_class, room_description, max_occupancy, nightly_rate, "
                    "resort_fee_per_night, cancellation_policy, available_units) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        offer_id, tenant_id, room_class, DESCRIPTIONS[room_class], MAX_OCCUPANCY[room_class],
                        RATES[room_class] + (i * 20.0), RESORT_FEE[room_class], CANCELLATION[room_class], units,
                    ),
                )
    await conn.commit()
