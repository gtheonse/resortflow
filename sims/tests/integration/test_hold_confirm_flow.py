"""User Story 1 integration test (T020): hold -> confirm -> availability-reflects-booking
across the four holding sims, a casino read-only smoke check, and a zero-remaining-
inventory hold request returning the contract's 409 (spec.md edge case)."""

from __future__ import annotations

import asyncio

import aiosqlite

from sims.tests.fixtures.clients import (
    amenities_auth_headers,
    casino_auth_headers,
    dining_auth_headers,
    pms_auth_headers,
    ticketing_auth_headers,
)


def _run(coro):
    return asyncio.run(coro)


def test_amenities_hold_confirm_and_availability(amenities_client) -> None:
    from sims.amenities.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute(
            "INSERT OR REPLACE INTO services VALUES ('svc-a','MGV','spa','Massage',60,150.0,'zoneA','[]')"
        )
        await conn.execute(
            "INSERT OR REPLACE INTO practitioners VALUES ('prac-a','MGV','svc-a','female',0)"
        )
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**amenities_auth_headers(), "X-Tenant-Id": "MGV"}

    avail = amenities_client.get(
        "/amenities/v3/services/svc-a/availability", params={"date": "2026-10-01"}, headers=h
    )
    assert avail.status_code == 200
    assert any(s["time"] == "09:00" for s in avail.json()["slots"])

    hold_resp = amenities_client.post(
        "/amenities/v3/holds",
        headers={**h, "Idempotency-Key": "amen-hold-1"},
        json={"service_id": "svc-a", "date": "2026-10-01", "time": "09:00", "practitioner_id": "prac-a"},
    )
    assert hold_resp.status_code == 201
    hold_id = hold_resp.json()["hold_id"]

    confirm_resp = amenities_client.post(
        "/amenities/v3/bookings",
        headers={**h, "Idempotency-Key": "amen-confirm-1"},
        json={"hold_id": hold_id, "guest_id": "guest-1"},
    )
    assert confirm_resp.status_code == 201

    avail_after = amenities_client.get(
        "/amenities/v3/services/svc-a/availability", params={"date": "2026-10-01"}, headers=h
    )
    assert not any(s["time"] == "09:00" for s in avail_after.json()["slots"]), "confirmed slot must not show as available"

    # Zero-remaining-inventory: same slot is now taken -> 409 on a second hold attempt.
    second_hold = amenities_client.post(
        "/amenities/v3/holds",
        headers={**h, "Idempotency-Key": "amen-hold-2"},
        json={"service_id": "svc-a", "date": "2026-10-01", "time": "09:00", "practitioner_id": "prac-a"},
    )
    assert second_hold.status_code == 409


def test_dining_hold_confirm_and_availability(dining_client) -> None:
    from sims.dining.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute(
            "INSERT OR REPLACE INTO venues VALUES ('ven-a','MGV','Chez Test','french','$$$','smart casual',1,4,'zoneA','[\"shellfish\"]','[]')"
        )
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**dining_auth_headers(), "X-Tenant-Id": "MGV"}

    from sims.dining.main import VENUE_CAPACITY_COVERS

    # Fill the entire flat per-slot capacity in one hold so availability/zero-inventory
    # assertions below don't depend on a fractional remainder.
    hold_resp = dining_client.post(
        "/v1/holds",
        headers={**h, "Idempotency-Key": "dine-hold-1"},
        json={"venue_id": "ven-a", "date": "2026-10-01", "time": "18:30", "party_size": VENUE_CAPACITY_COVERS},
    )
    assert hold_resp.status_code == 201
    hold_id = hold_resp.json()["hold_id"]

    confirm_resp = dining_client.post(
        "/v1/reservations",
        headers={**h, "Idempotency-Key": "dine-confirm-1"},
        json={"hold_id": hold_id, "guest": {"guest_id": "guest-1"}},
    )
    assert confirm_resp.status_code == 201

    avail_after = dining_client.get(
        "/v1/venues/ven-a/availability", params={"date": "2026-10-01", "party_size": 1}, headers=h
    )
    times = [s["time"] for s in avail_after.json()["slots"]]
    assert "18:30" not in times, "a fully-booked slot must not appear as available"

    # Zero-remaining-inventory at this exact slot -> 409.
    second_hold = dining_client.post(
        "/v1/holds",
        headers={**h, "Idempotency-Key": "dine-hold-2"},
        json={"venue_id": "ven-a", "date": "2026-10-01", "time": "18:30", "party_size": 1},
    )
    assert second_hold.status_code == 409


def test_pms_hold_confirm_and_availability(pms_client) -> None:
    from sims.pms.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute(
            "INSERT OR REPLACE INTO room_offers VALUES ('offer-a','MGV','STANDARD','King room',2,250.0,35.0,'flex',1)"
        )
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**pms_auth_headers(pms_client), "X-Tenant-Id": "MGV"}

    hold_resp = pms_client.post(
        "/pms/v5/holds/rooms",
        headers={**h, "Idempotency-Key": "pms-hold-1"},
        json={"OFFER_ID": "offer-a", "ARRIVAL": "2026-10-01", "DEPARTURE": "2026-10-03"},
    )
    assert hold_resp.status_code == 201
    hold_id = hold_resp.json()["HOLD_ID"]

    confirm_resp = pms_client.post(
        "/pms/v5/reservations",
        headers={**h, "Idempotency-Key": "pms-confirm-1"},
        json={"HOLD_ID": hold_id, "GUEST_PROFILE_ID": "guest-1", "PAYMENT_TOKEN": "tok"},
    )
    assert confirm_resp.status_code == 201

    avail_after = pms_client.get(
        "/pms/v5/availability/rooms",
        headers=h,
        params={"arrival": "2026-10-01", "departure": "2026-10-03", "adults": 2},
    )
    offers = avail_after.json()["RESPONSE"]["DATA"]["ROOM_OFFERS"]
    assert not any(o["OFFER_ID"] == "offer-a" for o in offers), "the only unit is now booked"

    # Zero-remaining-inventory -> 409.
    second_hold = pms_client.post(
        "/pms/v5/holds/rooms",
        headers={**h, "Idempotency-Key": "pms-hold-2"},
        json={"OFFER_ID": "offer-a", "ARRIVAL": "2026-10-01", "DEPARTURE": "2026-10-03"},
    )
    assert second_hold.status_code == 409


def test_ticketing_hold_confirm_and_availability(ticketing_client) -> None:
    from sims.ticketing.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute(
            "INSERT OR REPLACE INTO events VALUES ('evt-a','MGV','Show','concert','2026-10-05T20:00:00Z',"
            "'2026-10-05T19:00:00Z',120,'v1','Arena',1,'zoneA','123 Main',50,200,'USD','on_sale','desc','','casual',1)"
        )
        await conn.execute(
            "INSERT OR REPLACE INTO sections VALUES ('sec-a','evt-a','MGV','Floor',100.0,10.0,2,'premium')"
        )
        await conn.commit()
        await conn.close()

    _run(seed_one())
    tenant_headers = {"X-Tenant-Id": "MGV"}
    body = {"event_id": "evt-a", "section_id": "sec-a", "quantity": 2}
    h = {**tenant_headers, **ticketing_auth_headers(ticketing_client, body), "Idempotency-Key": "tix-hold-1", "Content-Type": "application/json"}

    hold_resp = ticketing_client.post("/discovery/v2/reservations", headers=h, json=body)
    assert hold_resp.status_code == 201

    inv = ticketing_client.get(
        "/discovery/v2/events/evt-a/inventory",
        headers={**tenant_headers, **ticketing_auth_headers(ticketing_client)},
        params={"quantity": 1, "contiguous_only": False},
    )
    section = next(s for s in inv.json()["sections"] if s["section_id"] == "sec-a")
    assert section["available"] == 0, "all seats in the section are now held"

    # Zero-remaining-inventory -> 409.
    second_body = {"event_id": "evt-a", "section_id": "sec-a", "quantity": 1}
    second_h = {**tenant_headers, **ticketing_auth_headers(ticketing_client, second_body), "Idempotency-Key": "tix-hold-2", "Content-Type": "application/json"}
    second_hold = ticketing_client.post("/discovery/v2/reservations", headers=second_h, json=second_body)
    assert second_hold.status_code == 409


def test_casino_read_only_smoke(casino_client) -> None:
    h = {**casino_auth_headers(), "X-Tenant-Id": "MGV"}
    resp = casino_client.get(
        "/casino/v1/responsible-gaming/status", headers=h, params={"guest_id": "any-guest"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "self_excluded" in body and "offers_permitted" in body and "jurisdiction" in body
