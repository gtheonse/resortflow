"""User Story 4 integration test (T032): a confirm attempt against an already-expired
hold returns 410 rather than silently succeeding (spec.md acceptance scenario).

Uses each hold-creation endpoint's own contract-documented `ttl_seconds` override (a
real, frozen-contract field — not a new interface) with a short value so this proves
the exact confirm-after-expiry code path over real HTTP in well under a second of real
wall-clock wait, without needing a compressed/injectable clock at the HTTP layer.
"""

from __future__ import annotations

import asyncio
import time

import aiosqlite

from sims.tests.fixtures.clients import (
    amenities_auth_headers,
    dining_auth_headers,
    pms_auth_headers,
)


def _run(coro):
    return asyncio.run(coro)


def test_amenities_confirm_after_expiry_returns_410(amenities_client) -> None:
    from sims.amenities.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute("INSERT OR REPLACE INTO services VALUES ('svc-exp','MGV','spa','Massage',60,150.0,'zoneA','[]')")
        await conn.execute("INSERT OR REPLACE INTO practitioners VALUES ('prac-exp','MGV','svc-exp','female',0)")
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**amenities_auth_headers(), "X-Tenant-Id": "MGV"}

    hold_resp = amenities_client.post(
        "/amenities/v3/holds",
        headers={**h, "Idempotency-Key": "exp-hold-1"},
        json={"service_id": "svc-exp", "date": "2026-10-01", "time": "09:00", "practitioner_id": "prac-exp", "ttl_seconds": 1},
    )
    assert hold_resp.status_code == 201
    hold_id = hold_resp.json()["hold_id"]

    time.sleep(1.5)  # past the 1-second TTL, well under CI-friendly bounds

    confirm_resp = amenities_client.post(
        "/amenities/v3/bookings",
        headers={**h, "Idempotency-Key": "exp-confirm-1"},
        json={"hold_id": hold_id, "guest_id": "guest-a"},
    )
    assert confirm_resp.status_code == 410, "confirming an expired hold must be rejected, not silently succeed"

    # And the resource is available again with no manual step (FR-003).
    avail = amenities_client.get(
        "/amenities/v3/services/svc-exp/availability", params={"date": "2026-10-01"}, headers=h
    )
    assert any(s["time"] == "09:00" and s["practitioner_id"] == "prac-exp" for s in avail.json()["slots"])


def test_dining_confirm_after_expiry_returns_410(dining_client) -> None:
    from sims.dining.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute(
            "INSERT OR REPLACE INTO venues VALUES ('ven-exp','MGV','Chez Test','french','$$$','smart casual',1,8,'zoneA','[]','[]')"
        )
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**dining_auth_headers(), "X-Tenant-Id": "MGV"}

    hold_resp = dining_client.post(
        "/v1/holds",
        headers={**h, "Idempotency-Key": "dexp-hold-1"},
        json={"venue_id": "ven-exp", "date": "2026-10-01", "time": "18:30", "party_size": 2, "ttl_seconds": 1},
    )
    assert hold_resp.status_code == 201
    hold_id = hold_resp.json()["hold_id"]

    time.sleep(1.5)

    confirm_resp = dining_client.post(
        "/v1/reservations",
        headers={**h, "Idempotency-Key": "dexp-confirm-1"},
        json={"hold_id": hold_id, "guest": {"guest_id": "guest-a"}},
    )
    assert confirm_resp.status_code == 410

    avail = dining_client.get(
        "/v1/venues/ven-exp/availability", params={"date": "2026-10-01", "party_size": 2}, headers=h
    )
    slot = next(s for s in avail.json()["slots"] if s["time"] == "18:30")
    assert slot["available_covers"] > 0, "the resource must be available again with no manual step"


def test_pms_confirm_after_expiry_returns_410(pms_client) -> None:
    from sims.pms.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute(
            "INSERT OR REPLACE INTO room_offers VALUES ('offer-exp','MGV','STANDARD','King room',2,250.0,35.0,'flex',1)"
        )
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**pms_auth_headers(pms_client), "X-Tenant-Id": "MGV"}

    hold_resp = pms_client.post(
        "/pms/v5/holds/rooms",
        headers={**h, "Idempotency-Key": "pexp-hold-1"},
        json={"OFFER_ID": "offer-exp", "ARRIVAL": "2026-10-01", "DEPARTURE": "2026-10-03", "TTL_SECONDS": 1},
    )
    assert hold_resp.status_code == 201
    hold_id = hold_resp.json()["HOLD_ID"]

    time.sleep(1.5)

    confirm_resp = pms_client.post(
        "/pms/v5/reservations",
        headers={**h, "Idempotency-Key": "pexp-confirm-1"},
        json={"HOLD_ID": hold_id, "GUEST_PROFILE_ID": "guest-a", "PAYMENT_TOKEN": "tok"},
    )
    assert confirm_resp.status_code == 410

    avail = pms_client.get(
        "/pms/v5/availability/rooms",
        headers=h,
        params={"arrival": "2026-10-01", "departure": "2026-10-03", "adults": 2},
    )
    offers = avail.json()["RESPONSE"]["DATA"]["ROOM_OFFERS"]
    assert any(o["OFFER_ID"] == "offer-exp" for o in offers), "the room offer must be available again"
