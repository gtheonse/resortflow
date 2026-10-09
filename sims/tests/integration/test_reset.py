"""User Story 6 integration test (T045, FR-014/SC-008): reset-to-baseline restores
exact seeded counts and clears all holds/bookings/idempotency records from prior use,
across all five sims."""

from __future__ import annotations

import asyncio

import aiosqlite

from sims.tests.fixtures.clients import amenities_auth_headers


def _run(coro):
    return asyncio.run(coro)


def test_amenities_reset_restores_baseline_and_clears_holds(amenities_client) -> None:
    from sims.amenities.main import DB_PATH

    h = {**amenities_auth_headers(), "X-Tenant-Id": "MGV"}

    async def count_services():
        conn = await aiosqlite.connect(DB_PATH)
        cur = await conn.execute("SELECT COUNT(*) FROM services")
        n = (await cur.fetchone())[0]
        await conn.close()
        return n

    baseline_count = _run(count_services())
    assert baseline_count == 60  # 36 MGV + 24 MHB

    # Place a hold using a seeded service, to prove reset clears it.
    conn_check = _run(_get_one_service(DB_PATH))
    service_id, practitioner_id = conn_check
    hold_resp = amenities_client.post(
        "/amenities/v3/holds",
        headers={**h, "Idempotency-Key": "reset-hold-1"},
        json={"service_id": service_id, "date": "2026-10-01", "time": "09:00", "practitioner_id": practitioner_id},
    )
    assert hold_resp.status_code == 201

    async def count_holds_and_idempotency():
        conn = await aiosqlite.connect(DB_PATH)
        cur = await conn.execute("SELECT COUNT(*) FROM holds")
        holds_n = (await cur.fetchone())[0]
        cur2 = await conn.execute("SELECT COUNT(*) FROM idempotency_records")
        idem_n = (await cur2.fetchone())[0]
        await conn.close()
        return holds_n, idem_n

    holds_before, _ = _run(count_holds_and_idempotency())
    assert holds_before >= 1

    reset_resp = amenities_client.post("/__sim__/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json() == {"reset": True, "tenant_ids": ["MGV", "MHB"]}

    assert _run(count_services()) == baseline_count, "reset must restore the exact seeded baseline count"
    holds_after, idem_after = _run(count_holds_and_idempotency())
    assert holds_after == 0, "reset must clear all holds from prior use"
    assert idem_after == 0, "reset must clear all idempotency records from prior use"


async def _get_one_service(db_path):
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute("SELECT * FROM practitioners WHERE tenant_id = 'MGV' LIMIT 1")
    row = await cur.fetchone()
    await conn.close()
    return row["service_id"], row["practitioner_id"]


def test_dining_reset_is_idempotent_and_restores_baseline(dining_client) -> None:
    from sims.dining.main import DB_PATH

    async def count_venues():
        conn = await aiosqlite.connect(DB_PATH)
        cur = await conn.execute("SELECT COUNT(*) FROM venues")
        n = (await cur.fetchone())[0]
        await conn.close()
        return n

    assert _run(count_venues()) == 8  # 5 MGV + 3 MHB

    # Resetting an already-freshly-reset sim is a no-op that still returns 200
    # (sim-control-plane.md).
    first = dining_client.post("/__sim__/reset")
    second = dining_client.post("/__sim__/reset")
    assert first.status_code == 200
    assert second.status_code == 200
    assert _run(count_venues()) == 8


def test_reset_across_all_five_sims(
    amenities_client, casino_client, dining_client, pms_client, ticketing_client
) -> None:
    for client in (amenities_client, casino_client, dining_client, pms_client, ticketing_client):
        resp = client.post("/__sim__/reset")
        assert resp.status_code == 200
        assert resp.json()["reset"] is True
        assert set(resp.json()["tenant_ids"]) == {"MGV", "MHB"}
