"""Seed data for amenities-sim (T036): 36 MGV / 24 MHB spa/amenity slots across
`category` (spa, fitness, cabana, golf), multiple `practitioner_id`s per service, per
`contracts/seed-data-spec.md`.
"""

from __future__ import annotations

import json
import random

import aiosqlite

SEED = 42
CATEGORIES = ["spa", "fitness", "cabana", "golf"]
TENANT_COUNTS = [("MGV", 36), ("MHB", 24)]

SERVICE_NAMES = {
    "spa": ["Swedish Massage", "Deep Tissue Massage", "Hot Stone Therapy", "Facial Renewal", "Body Wrap", "Couples Massage"],
    "fitness": ["Personal Training", "Yoga Session", "Pilates Reformer", "HIIT Class", "Boxing Cardio"],
    "cabana": ["Poolside Cabana Half-Day", "Poolside Cabana Full-Day", "Daybed Reservation"],
    "golf": ["18-Hole Round", "9-Hole Twilight", "Private Lesson", "Club Fitting"],
}

FIRST_NAMES = ["Alex", "Jordan", "Sam", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Drew", "Quinn"]


async def seed(conn: aiosqlite.Connection) -> None:
    rng = random.Random(SEED)

    for tenant_id, total in TENANT_COUNTS:
        base_per_category = total // len(CATEGORIES)
        remainder = total - base_per_category * len(CATEGORIES)
        service_index = 0
        for cat_i, category in enumerate(CATEGORIES):
            count = base_per_category + (1 if cat_i < remainder else 0)
            names = SERVICE_NAMES[category]
            for i in range(count):
                service_index += 1
                service_id = f"svc-{tenant_id.lower()}-{category}-{i + 1:02d}"
                name = names[i % len(names)]
                duration = rng.choice([30, 45, 60, 75, 90, 120])
                price = float(rng.choice([80, 120, 150, 200, 250, 350]))
                contraindications = ["pregnancy", "recent_surgery"] if category == "spa" and rng.random() < 0.4 else []
                await conn.execute(
                    "INSERT OR REPLACE INTO services "
                    "(service_id, tenant_id, category, name, duration_minutes, price, location_zone, contraindications) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (service_id, tenant_id, category, name, duration, price, f"{category}_zone", json.dumps(contraindications)),
                )

                # Multiple practitioners per service — availability is
                # practitioner-constrained, per the frozen contract's DELIBERATE QUIRKS.
                num_practitioners = rng.choice([2, 2, 3])
                for p in range(num_practitioners):
                    practitioner_id = f"{service_id}-prac-{p + 1}"
                    gender = rng.choice(["female", "male"])
                    accessible = rng.random() < 0.5
                    await conn.execute(
                        "INSERT OR REPLACE INTO practitioners "
                        "(practitioner_id, tenant_id, service_id, gender, accessible) VALUES (?, ?, ?, ?, ?)",
                        (practitioner_id, tenant_id, service_id, gender, int(accessible)),
                    )

    await conn.commit()
