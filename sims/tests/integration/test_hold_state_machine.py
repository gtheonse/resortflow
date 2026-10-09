"""User Story 3 integration test (T028): hold terminal-state transitions are refused
regardless of idempotency key — a confirm carrying a *different* key than the original,
successful confirm does not create a second booking (the hold is already confirmed,
terminal), and a release against an already-confirmed hold is refused with the
contract's appropriate conflict response (both spec.md edge cases, per data-model.md's
state-machine notes)."""

from __future__ import annotations

import asyncio

import aiosqlite

from sims.tests.fixtures.clients import amenities_auth_headers, dining_auth_headers


def _run(coro):
    return asyncio.run(coro)


def test_amenities_differently_keyed_confirm_does_not_double_book(amenities_client) -> None:
    from sims.amenities.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute("INSERT OR REPLACE INTO services VALUES ('svc-sm','MGV','spa','Massage',60,150.0,'zoneA','[]')")
        await conn.execute("INSERT OR REPLACE INTO practitioners VALUES ('prac-sm','MGV','svc-sm','female',0)")
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**amenities_auth_headers(), "X-Tenant-Id": "MGV"}

    hold_resp = amenities_client.post(
        "/amenities/v3/holds",
        headers={**h, "Idempotency-Key": "sm-hold-1"},
        json={"service_id": "svc-sm", "date": "2026-10-01", "time": "09:00", "practitioner_id": "prac-sm"},
    )
    hold_id = hold_resp.json()["hold_id"]

    first_confirm = amenities_client.post(
        "/amenities/v3/bookings",
        headers={**h, "Idempotency-Key": "sm-confirm-first"},
        json={"hold_id": hold_id, "guest_id": "guest-a"},
    )
    assert first_confirm.status_code == 201

    # A DIFFERENT idempotency key, same hold_id: the hold is already confirmed
    # (terminal) — this must be refused (410, the contract's only documented
    # "cannot confirm" code), never create a second booking.
    second_confirm = amenities_client.post(
        "/amenities/v3/bookings",
        headers={**h, "Idempotency-Key": "sm-confirm-second-different-key"},
        json={"hold_id": hold_id, "guest_id": "guest-a"},
    )
    assert second_confirm.status_code == 410

    # Prove no second booking exists.
    async def count_bookings():
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT COUNT(*) as n FROM bookings WHERE hold_id = ?", (hold_id,))
        row = await cur.fetchone()
        await cur.close()
        await conn.close()
        return row["n"]

    assert _run(count_bookings()) == 1


def test_amenities_release_against_confirmed_hold_is_refused(amenities_client) -> None:
    from sims.amenities.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute("INSERT OR REPLACE INTO services VALUES ('svc-sm2','MGV','spa','Massage',60,150.0,'zoneA','[]')")
        await conn.execute("INSERT OR REPLACE INTO practitioners VALUES ('prac-sm2','MGV','svc-sm2','female',0)")
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**amenities_auth_headers(), "X-Tenant-Id": "MGV"}

    hold_resp = amenities_client.post(
        "/amenities/v3/holds",
        headers={**h, "Idempotency-Key": "sm2-hold-1"},
        json={"service_id": "svc-sm2", "date": "2026-10-01", "time": "09:00", "practitioner_id": "prac-sm2"},
    )
    hold_id = hold_resp.json()["hold_id"]

    confirm = amenities_client.post(
        "/amenities/v3/bookings",
        headers={**h, "Idempotency-Key": "sm2-confirm-1"},
        json={"hold_id": hold_id, "guest_id": "guest-a"},
    )
    assert confirm.status_code == 201

    # amenities' DELETE /holds/{id} contract declares only 204 (no conflict code) — a
    # release against an already-confirmed hold is refused by being a no-op that
    # still returns 204, never by silently transitioning the hold to 'released'
    # (data-model.md: "no domain's release endpoint may transition it to released").
    release_after_confirm = amenities_client.delete(f"/amenities/v3/holds/{hold_id}", headers=h)
    assert release_after_confirm.status_code == 204

    async def get_status():
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT status FROM holds WHERE hold_id = ?", (hold_id,))
        row = await cur.fetchone()
        await cur.close()
        await conn.close()
        return row["status"]

    assert _run(get_status()) == "confirmed", "the hold must remain confirmed, never silently become released"


def test_dining_release_against_confirmed_hold_is_refused(dining_client) -> None:
    from sims.dining.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute(
            "INSERT OR REPLACE INTO venues VALUES ('ven-sm','MGV','Chez Test','french','$$$','smart casual',1,8,'zoneA','[]','[]')"
        )
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**dining_auth_headers(), "X-Tenant-Id": "MGV"}

    hold_resp = dining_client.post(
        "/v1/holds",
        headers={**h, "Idempotency-Key": "dsm-hold-1"},
        json={"venue_id": "ven-sm", "date": "2026-10-01", "time": "18:30", "party_size": 2},
    )
    hold_id = hold_resp.json()["hold_id"]

    confirm = dining_client.post(
        "/v1/reservations",
        headers={**h, "Idempotency-Key": "dsm-confirm-1"},
        json={"hold_id": hold_id, "guest": {"guest_id": "guest-a"}},
    )
    assert confirm.status_code == 201

    # Differently-keyed confirm against the now-confirmed hold -> 410 (only documented
    # "cannot confirm" code for this operation), never a second reservation.
    second_confirm = dining_client.post(
        "/v1/reservations",
        headers={**h, "Idempotency-Key": "dsm-confirm-2-different"},
        json={"hold_id": hold_id, "guest": {"guest_id": "guest-a"}},
    )
    assert second_confirm.status_code == 410

    async def count_reservations():
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT COUNT(*) as n FROM reservations WHERE hold_id = ?", (hold_id,))
        row = await cur.fetchone()
        await cur.close()
        await conn.close()
        return row["n"]

    assert _run(count_reservations()) == 1

    # dining's DELETE /holds/{id} declares 204/404 — the hold is 'confirmed', not
    # 'active', so this reads as "already expired" (404) per the contract's own
    # not-found semantics for a hold no longer live, never as a successful release.
    release_after_confirm = dining_client.delete(f"/v1/holds/{hold_id}", headers=h)
    assert release_after_confirm.status_code in (204, 404)

    async def get_status():
        conn = await aiosqlite.connect(DB_PATH)
        conn.row_factory = aiosqlite.Row
        cur = await conn.execute("SELECT status FROM holds WHERE hold_id = ?", (hold_id,))
        row = await cur.fetchone()
        await cur.close()
        await conn.close()
        return row["status"]

    assert _run(get_status()) == "confirmed", "the hold must remain confirmed, never silently become released"
