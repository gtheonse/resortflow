"""User Story 3 integration test (T027): the same Idempotency-Key reused for a
genuinely different request body is detected and rejected rather than silently
replayed (spec.md edge case)."""

from __future__ import annotations

import asyncio

import aiosqlite

from sims.tests.fixtures.clients import amenities_auth_headers, dining_auth_headers


def _run(coro):
    return asyncio.run(coro)


def test_amenities_idempotency_key_reuse_is_rejected(amenities_client) -> None:
    from sims.amenities.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute("INSERT OR REPLACE INTO services VALUES ('svc-k','MGV','spa','Massage',60,150.0,'zoneA','[]')")
        await conn.execute("INSERT OR REPLACE INTO practitioners VALUES ('prac-k1','MGV','svc-k','female',0)")
        await conn.execute("INSERT OR REPLACE INTO practitioners VALUES ('prac-k2','MGV','svc-k','male',0)")
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**amenities_auth_headers(), "X-Tenant-Id": "MGV"}

    hold1 = amenities_client.post(
        "/amenities/v3/holds",
        headers={**h, "Idempotency-Key": "k-hold-a"},
        json={"service_id": "svc-k", "date": "2026-10-01", "time": "09:00", "practitioner_id": "prac-k1"},
    )
    hold2 = amenities_client.post(
        "/amenities/v3/holds",
        headers={**h, "Idempotency-Key": "k-hold-b"},
        json={"service_id": "svc-k", "date": "2026-10-01", "time": "10:00", "practitioner_id": "prac-k2"},
    )
    hold_id_1 = hold1.json()["hold_id"]
    hold_id_2 = hold2.json()["hold_id"]

    first_confirm = amenities_client.post(
        "/amenities/v3/bookings",
        headers={**h, "Idempotency-Key": "reused-key"},
        json={"hold_id": hold_id_1, "guest_id": "guest-a"},
    )
    assert first_confirm.status_code == 201

    # Same key, genuinely different request body (different hold_id/guest) — must be
    # rejected, never silently replayed as if it were the first request.
    conflicting_confirm = amenities_client.post(
        "/amenities/v3/bookings",
        headers={**h, "Idempotency-Key": "reused-key"},
        json={"hold_id": hold_id_2, "guest_id": "guest-b"},
    )
    assert conflicting_confirm.status_code == 409
    assert conflicting_confirm.json()["err_code"] == "IDEMPOTENCY_KEY_CONFLICT"


def test_dining_idempotency_key_reuse_is_rejected(dining_client) -> None:
    from sims.dining.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute(
            "INSERT OR REPLACE INTO venues VALUES ('ven-k','MGV','Chez Test','french','$$$','smart casual',1,8,'zoneA','[]','[]')"
        )
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**dining_auth_headers(), "X-Tenant-Id": "MGV"}

    hold1 = dining_client.post(
        "/v1/holds",
        headers={**h, "Idempotency-Key": "dk-hold-a"},
        json={"venue_id": "ven-k", "date": "2026-10-01", "time": "18:30", "party_size": 2},
    )
    hold2 = dining_client.post(
        "/v1/holds",
        headers={**h, "Idempotency-Key": "dk-hold-b"},
        json={"venue_id": "ven-k", "date": "2026-10-01", "time": "19:00", "party_size": 2},
    )
    hold_id_1 = hold1.json()["hold_id"]
    hold_id_2 = hold2.json()["hold_id"]

    first = dining_client.post(
        "/v1/reservations",
        headers={**h, "Idempotency-Key": "dine-reused-key"},
        json={"hold_id": hold_id_1, "guest": {"guest_id": "guest-a"}},
    )
    assert first.status_code == 201

    conflicting = dining_client.post(
        "/v1/reservations",
        headers={**h, "Idempotency-Key": "dine-reused-key"},
        json={"hold_id": hold_id_2, "guest": {"guest_id": "guest-b"}},
    )
    assert conflicting.status_code == 409
