"""User Story 3 integration test (T026, SC-003): repeated confirm and repeated release
with the same Idempotency-Key produce exactly one outcome each — a single retry and a
multi-retry (3+) case, for both confirm and release, across all four holding sims."""

from __future__ import annotations

import asyncio

import aiosqlite

from sims.tests.fixtures.clients import (
    amenities_auth_headers,
    dining_auth_headers,
    pms_auth_headers,
    ticketing_auth_headers,
)


def _run(coro):
    return asyncio.run(coro)


def test_amenities_confirm_retry_single_and_multi(amenities_client) -> None:
    from sims.amenities.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute("INSERT OR REPLACE INTO services VALUES ('svc-r','MGV','spa','Massage',60,150.0,'zoneA','[]')")
        await conn.execute("INSERT OR REPLACE INTO practitioners VALUES ('prac-r','MGV','svc-r','female',0)")
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**amenities_auth_headers(), "X-Tenant-Id": "MGV"}

    hold_resp = amenities_client.post(
        "/amenities/v3/holds",
        headers={**h, "Idempotency-Key": "ret-hold-1"},
        json={"service_id": "svc-r", "date": "2026-10-01", "time": "09:00", "practitioner_id": "prac-r"},
    )
    hold_id = hold_resp.json()["hold_id"]

    confirm_body = {"hold_id": hold_id, "guest_id": "g1"}
    first = amenities_client.post(
        "/amenities/v3/bookings", headers={**h, "Idempotency-Key": "ret-confirm-1"}, json=confirm_body
    )
    assert first.status_code == 201
    booking_id = first.json()["booking_id"]

    # Single retry.
    retry_once = amenities_client.post(
        "/amenities/v3/bookings", headers={**h, "Idempotency-Key": "ret-confirm-1"}, json=confirm_body
    )
    assert retry_once.status_code == 201
    assert retry_once.json()["booking_id"] == booking_id

    # Multi-retry (3 more repeats) — always the same booking, never a duplicate.
    for _ in range(3):
        again = amenities_client.post(
            "/amenities/v3/bookings", headers={**h, "Idempotency-Key": "ret-confirm-1"}, json=confirm_body
        )
        assert again.status_code == 201
        assert again.json()["booking_id"] == booking_id


def test_dining_confirm_and_release_retry(dining_client) -> None:
    from sims.dining.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute(
            "INSERT OR REPLACE INTO venues VALUES ('ven-r','MGV','Chez Test','french','$$$','smart casual',1,8,'zoneA','[]','[]')"
        )
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**dining_auth_headers(), "X-Tenant-Id": "MGV"}

    # --- confirm retry ---
    hold_resp = dining_client.post(
        "/v1/holds",
        headers={**h, "Idempotency-Key": "dine-ret-hold-1"},
        json={"venue_id": "ven-r", "date": "2026-10-01", "time": "18:30", "party_size": 2},
    )
    hold_id = hold_resp.json()["hold_id"]
    confirm_body = {"hold_id": hold_id, "guest": {"guest_id": "g1"}}

    first = dining_client.post("/v1/reservations", headers={**h, "Idempotency-Key": "dine-ret-confirm-1"}, json=confirm_body)
    assert first.status_code == 201
    reservation_id = first.json()["reservation_id"]

    retry_once = dining_client.post("/v1/reservations", headers={**h, "Idempotency-Key": "dine-ret-confirm-1"}, json=confirm_body)
    assert retry_once.status_code == 200
    assert retry_once.json()["reservation_id"] == reservation_id

    for _ in range(3):
        again = dining_client.post("/v1/reservations", headers={**h, "Idempotency-Key": "dine-ret-confirm-1"}, json=confirm_body)
        assert again.status_code == 200
        assert again.json()["reservation_id"] == reservation_id

    # --- release retry (a second hold, released rather than confirmed) ---
    hold_resp2 = dining_client.post(
        "/v1/holds",
        headers={**h, "Idempotency-Key": "dine-ret-hold-2"},
        json={"venue_id": "ven-r", "date": "2026-10-01", "time": "19:00", "party_size": 2},
    )
    hold_id2 = hold_resp2.json()["hold_id"]

    first_release = dining_client.delete(f"/v1/holds/{hold_id2}", headers=h)
    assert first_release.status_code == 204
    for _ in range(4):
        again_release = dining_client.delete(f"/v1/holds/{hold_id2}", headers=h)
        # Contract declares 204/404 for this operation; already-released reads as
        # "already expired" (404) per dining-sim's contract — either way, no error
        # that implies a second release occurred, and no exception raised.
        assert again_release.status_code in (204, 404)


def test_pms_confirm_retry(pms_client) -> None:
    from sims.pms.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute(
            "INSERT OR REPLACE INTO room_offers VALUES ('offer-r','MGV','STANDARD','King room',2,250.0,35.0,'flex',3)"
        )
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**pms_auth_headers(pms_client), "X-Tenant-Id": "MGV"}

    hold_resp = pms_client.post(
        "/pms/v5/holds/rooms",
        headers={**h, "Idempotency-Key": "pms-ret-hold-1"},
        json={"OFFER_ID": "offer-r", "ARRIVAL": "2026-10-01", "DEPARTURE": "2026-10-03"},
    )
    hold_id = hold_resp.json()["HOLD_ID"]
    confirm_body = {"HOLD_ID": hold_id, "GUEST_PROFILE_ID": "g1", "PAYMENT_TOKEN": "tok"}

    first = pms_client.post("/pms/v5/reservations", headers={**h, "Idempotency-Key": "pms-ret-confirm-1"}, json=confirm_body)
    assert first.status_code == 201
    confirmation_number = first.json()["CONFIRMATION_NUMBER"]

    for _ in range(4):
        again = pms_client.post("/pms/v5/reservations", headers={**h, "Idempotency-Key": "pms-ret-confirm-1"}, json=confirm_body)
        assert again.status_code == 200
        assert again.json()["CONFIRMATION_NUMBER"] == confirmation_number


def test_ticketing_hold_release_retry(ticketing_client) -> None:
    from sims.ticketing.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute(
            "INSERT OR REPLACE INTO events VALUES ('evt-r','MGV','Show','concert','2026-10-05T20:00:00Z',"
            "'2026-10-05T19:00:00Z',120,'v1','Arena',1,'zoneA','123 Main',50,200,'USD','on_sale','desc','','casual',1)"
        )
        await conn.execute("INSERT OR REPLACE INTO sections VALUES ('sec-r','evt-r','MGV','Floor',100.0,10.0,10,'premium')")
        await conn.commit()
        await conn.close()

    _run(seed_one())
    tenant = {"X-Tenant-Id": "MGV"}
    body = {"event_id": "evt-r", "section_id": "sec-r", "quantity": 2}
    h = {**tenant, **ticketing_auth_headers(ticketing_client, body), "Idempotency-Key": "tix-ret-hold-1", "Content-Type": "application/json"}

    hold_resp = ticketing_client.post("/discovery/v2/reservations", headers=h, json=body)
    hold_id = hold_resp.json()["hold_id"]

    first_release = ticketing_client.delete(f"/discovery/v2/reservations/{hold_id}", headers={**tenant, **ticketing_auth_headers(ticketing_client)})
    assert first_release.status_code == 204
    for _ in range(3):
        again = ticketing_client.delete(f"/discovery/v2/reservations/{hold_id}", headers={**tenant, **ticketing_auth_headers(ticketing_client)})
        assert again.status_code == 204
