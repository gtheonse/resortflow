"""Seed data for casino-sim (T037): loyalty + responsible-gaming status records for all
50 shared guest profiles, >=1 self_excluded per tenant, per
`contracts/seed-data-spec.md`. Also seeds a small, deterministic floor-games and
tournaments catalog per tenant so those informational endpoints have real data.
"""

from __future__ import annotations

import json
import random

import aiosqlite

from sims._shared.guest_seed import GUEST_PROFILES, JURISDICTION, MANDATED_MESSAGE

SEED = 42

FLOOR_GAMES = {
    "MGV": [
        ("blackjack", "main_floor", 25.0, "24/7"),
        ("roulette", "main_floor", 15.0, "24/7"),
        ("baccarat", "high_limit_salon", 100.0, "24/7"),
        ("craps", "main_floor", 10.0, "10:00-04:00"),
        ("poker", "poker_room", None, "24/7"),
        ("slots", "main_floor", None, "24/7"),
    ],
    "MHB": [
        ("blackjack", "main_floor", 15.0, "10:00-02:00"),
        ("roulette", "main_floor", 10.0, "10:00-02:00"),
        ("poker", "poker_room", None, "12:00-02:00"),
        ("slots", "main_floor", None, "24/7"),
    ],
}

TOURNAMENTS = {
    "MGV": [
        ("High Roller Poker Classic", "2026-11-01T18:00:00Z", 5000.0),
        ("Weekly Slots Freeroll", "2026-10-15T20:00:00Z", 0.0),
    ],
    "MHB": [
        ("Harbor Poker Open", "2026-11-05T18:00:00Z", 1000.0),
    ],
}


async def seed(conn: aiosqlite.Connection) -> None:
    rng = random.Random(SEED)

    for g in GUEST_PROFILES:
        await conn.execute(
            "INSERT OR REPLACE INTO responsible_gaming "
            "(guest_id, tenant_id, jurisdiction, self_excluded, exclusion_scope, limit_flags, "
            "offers_permitted, gaming_content_permitted, mandated_message, support_resources) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                g.guest_id,
                g.tenant_id,
                JURISDICTION[g.tenant_id],
                int(g.self_excluded),
                g.exclusion_scope,
                json.dumps(g.limit_flags),
                int(g.offers_permitted),
                int(g.gaming_content_permitted),
                MANDATED_MESSAGE[g.tenant_id],
                json.dumps(
                    [{"name": "GameSense/NCPG", "contact": "1-800-522-4700", "jurisdiction": JURISDICTION[g.tenant_id]}]
                ),
            ),
        )
        await conn.execute(
            "INSERT OR REPLACE INTO loyalty "
            "(guest_id, tenant_id, tier, points_balance, comp_dollars, tier_expires, entitlements) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                g.guest_id,
                g.tenant_id,
                g.tier,
                g.points_balance,
                g.comp_dollars,
                "2027-12-31",
                json.dumps(["lounge_access"] if g.tier in ("GOLD", "PLATINUM", "CHAIRMAN") else []),
            ),
        )
        if not g.self_excluded and rng.random() < 0.3:
            await conn.execute(
                "INSERT INTO loyalty_offers (offer_id, guest_id, tenant_id, category, description, value, expires) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"offer-{g.guest_id}",
                    g.guest_id,
                    g.tenant_id,
                    rng.choice(["dining", "spa", "room_credit", "entertainment", "retail"]),
                    "Complimentary comp offer",
                    round(rng.uniform(20, 200), 2),
                    "2026-12-31",
                ),
            )

    for tenant_id, games in FLOOR_GAMES.items():
        for game_type, zone, minimum, hours in games:
            await conn.execute(
                "INSERT INTO floor_games (tenant_id, game_type, location_zone, table_minimum, hours) VALUES (?, ?, ?, ?, ?)",
                (tenant_id, game_type, zone, minimum, hours),
            )

    for tenant_id, tournaments in TOURNAMENTS.items():
        for i, (name, start, buy_in) in enumerate(tournaments):
            await conn.execute(
                "INSERT INTO tournaments (tournament_id, tenant_id, name, start, buy_in, registration_channel) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (f"tourn-{tenant_id.lower()}-{i + 1}", tenant_id, name, start, buy_in, "front_desk"),
            )

    await conn.commit()
