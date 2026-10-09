"""User Story 6 integration test (T043, SC-007): seeded counts match
`contracts/seed-data-spec.md` exactly per tenant, across all five sims."""

from __future__ import annotations

import asyncio
import json

import aiosqlite

DIETARY_RESTRICTIONS = {
    "shellfish", "tree_nut", "peanut", "dairy", "gluten", "egg", "soy",
    "sesame", "fish", "vegetarian", "vegan", "halal", "kosher", "low_sodium",
}
CLASSIFICATIONS = {"concert", "theater", "comedy", "sports", "residency", "nightlife"}
ROOM_CLASSES = {"STANDARD", "DELUXE", "SUITE", "VILLA"}


def _run(coro):
    return asyncio.run(coro)


def test_amenities_seed_counts(amenities_client) -> None:
    from sims.amenities.main import DB_PATH

    async def check():
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT tenant_id, COUNT(*) as n FROM services GROUP BY tenant_id")
        counts = {r["tenant_id"]: r["n"] for r in await cur.fetchall()}
        await conn.close()
        return counts

    counts = _run(check())
    assert counts == {"MGV": 36, "MHB": 24}


def test_casino_seed_counts(casino_client) -> None:
    from sims.casino.main import DB_PATH

    async def check():
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT tenant_id, COUNT(*) as n FROM responsible_gaming GROUP BY tenant_id")
        counts = {r["tenant_id"]: r["n"] for r in await cur.fetchall()}
        cur2 = await conn.execute("SELECT tenant_id, COUNT(*) as n FROM responsible_gaming WHERE self_excluded = 1 GROUP BY tenant_id")
        self_excluded = {r["tenant_id"]: r["n"] for r in await cur2.fetchall()}
        await conn.close()
        return counts, self_excluded

    counts, self_excluded = _run(check())
    assert counts == {"MGV": 30, "MHB": 20}
    assert counts["MGV"] + counts["MHB"] == 50
    assert self_excluded.get("MGV", 0) >= 1
    assert self_excluded.get("MHB", 0) >= 1


def test_dining_seed_counts_and_dietary_coverage(dining_client) -> None:
    from sims.dining.main import DB_PATH

    async def check():
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT tenant_id, COUNT(*) as n FROM venues GROUP BY tenant_id")
        counts = {r["tenant_id"]: r["n"] for r in await cur.fetchall()}
        cur2 = await conn.execute("SELECT tenant_id, supported_dietary FROM venues")
        coverage: dict[str, set] = {}
        for r in await cur2.fetchall():
            coverage.setdefault(r["tenant_id"], set()).update(json.loads(r["supported_dietary"]))
        await conn.close()
        return counts, coverage

    counts, coverage = _run(check())
    assert counts == {"MGV": 5, "MHB": 3}
    assert coverage["MGV"] == DIETARY_RESTRICTIONS
    assert coverage["MHB"] == DIETARY_RESTRICTIONS


def test_pms_seed_counts(pms_client) -> None:
    from sims.pms.main import DB_PATH

    async def check():
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT tenant_id, SUM(available_units) as total FROM room_offers GROUP BY tenant_id")
        totals = {r["tenant_id"]: r["total"] for r in await cur.fetchall()}
        cur2 = await conn.execute("SELECT DISTINCT room_class FROM room_offers")
        classes = {r["room_class"] for r in await cur2.fetchall()}
        await conn.close()
        return totals, classes

    totals, classes = _run(check())
    assert totals == {"MGV": 120, "MHB": 80}
    assert classes == ROOM_CLASSES


def test_ticketing_seed_counts_and_classification_coverage(ticketing_client) -> None:
    from sims.ticketing.main import DB_PATH

    async def check():
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT tenant_id, COUNT(*) as n FROM events GROUP BY tenant_id")
        counts = {r["tenant_id"]: r["n"] for r in await cur.fetchall()}
        cur2 = await conn.execute("SELECT tenant_id, classification FROM events")
        coverage: dict[str, set] = {}
        for r in await cur2.fetchall():
            coverage.setdefault(r["tenant_id"], set()).add(r["classification"])
        cur3 = await conn.execute("SELECT tenant_id, status, COUNT(*) as n FROM events WHERE status IN ('sold_out','cancelled') GROUP BY tenant_id, status")
        status_counts = {(r["tenant_id"], r["status"]): r["n"] for r in await cur3.fetchall()}
        await conn.close()
        return counts, coverage, status_counts

    counts, coverage, status_counts = _run(check())
    assert counts == {"MGV": 24, "MHB": 16}
    assert coverage["MGV"] == CLASSIFICATIONS
    assert coverage["MHB"] == CLASSIFICATIONS
    for tenant in ("MGV", "MHB"):
        assert status_counts.get((tenant, "sold_out"), 0) >= 1
        assert status_counts.get((tenant, "cancelled"), 0) >= 1


def test_total_seed_volumes_match_spec(amenities_client, dining_client, pms_client, ticketing_client, casino_client) -> None:
    """SC-007: the seeded environment reflects the exact target volumes (2 tenants,
    8 venues, 40 events, 200 rooms, 60 spa slots, 50 guest profiles)."""
    from sims.amenities.main import DB_PATH as AMEN_DB
    from sims.casino.main import DB_PATH as CASINO_DB
    from sims.dining.main import DB_PATH as DINING_DB
    from sims.pms.main import DB_PATH as PMS_DB
    from sims.ticketing.main import DB_PATH as TICKETING_DB

    async def count(db_path, table, agg="COUNT(*)"):
        conn = await aiosqlite.connect(db_path)
        cur = await conn.execute(f"SELECT {agg} FROM {table}")
        row = await cur.fetchone()
        await conn.close()
        return row[0]

    assert _run(count(DINING_DB, "venues")) == 8
    assert _run(count(TICKETING_DB, "events")) == 40
    assert _run(count(PMS_DB, "room_offers", "SUM(available_units)")) == 200
    assert _run(count(AMEN_DB, "services")) == 60
    assert _run(count(CASINO_DB, "responsible_gaming")) == 50
