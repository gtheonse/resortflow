"""User Story 2 integration test (T024): each applicable fault mode produces its exact
documented failure deterministically across repeated attempts, an absent header behaves
normally, and an inapplicable/unrecognized value returns 400."""

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


# ---------------------------------------------------------------------------
# Generic faults (timeout / error_500 / rate_limit) — deterministic, repeated.
# ---------------------------------------------------------------------------


def test_dining_error_500_is_deterministic_and_absent_header_is_normal(dining_client) -> None:
    h = {**dining_auth_headers(), "X-Tenant-Id": "MGV"}
    for _ in range(3):
        resp = dining_client.get("/v1/venues", headers={**h, "X-Sim-Fault": "error_500"})
        assert resp.status_code == 500
        assert resp.json()["error"]["code"] == "UPSTREAM_ERROR"

    normal = dining_client.get("/v1/venues", headers=h)
    assert normal.status_code == 200


def test_dining_rate_limit(dining_client) -> None:
    h = {**dining_auth_headers(), "X-Tenant-Id": "MGV"}
    resp = dining_client.get("/v1/venues", headers={**h, "X-Sim-Fault": "rate_limit"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


def test_amenities_error_500(amenities_client) -> None:
    h = {**amenities_auth_headers(), "X-Tenant-Id": "MGV"}
    resp = amenities_client.get("/amenities/v3/services", headers={**h, "X-Sim-Fault": "error_500"})
    assert resp.status_code == 500
    assert resp.json()["err_code"] == "UPSTREAM_ERROR"


def test_pms_error_500(pms_client) -> None:
    h = {**pms_auth_headers(pms_client), "X-Tenant-Id": "MGV"}
    resp = pms_client.get(
        "/pms/v5/availability/rooms",
        headers={**h, "X-Sim-Fault": "error_500"},
        params={"arrival": "2026-10-01", "departure": "2026-10-03", "adults": 2},
    )
    assert resp.status_code == 500


def test_ticketing_error_500(ticketing_client) -> None:
    h = {**ticketing_auth_headers(ticketing_client), "X-Tenant-Id": "MGV"}
    resp = ticketing_client.get(
        "/discovery/v2/events",
        headers={**h, "X-Sim-Fault": "error_500"},
        params={"start_utc": "2026-01-01T00:00:00Z", "end_utc": "2027-01-01T00:00:00Z"},
    )
    assert resp.status_code == 500
    assert resp.json()["fault"]["type"] == "UPSTREAM_ERROR"


def test_casino_error_500_and_rate_limit(casino_client) -> None:
    h = {**casino_auth_headers(), "X-Tenant-Id": "MGV"}
    resp = casino_client.get(
        "/casino/v1/responsible-gaming/status",
        headers={**h, "X-Sim-Fault": "error_500"},
        params={"guest_id": "g1"},
    )
    assert resp.status_code == 500

    resp2 = casino_client.get(
        "/casino/v1/responsible-gaming/status",
        headers={**h, "X-Sim-Fault": "rate_limit"},
        params={"guest_id": "g1"},
    )
    assert resp2.status_code == 429


# ---------------------------------------------------------------------------
# Unrecognized / inapplicable fault values -> 400
# ---------------------------------------------------------------------------


def test_unrecognized_fault_value_returns_400(dining_client) -> None:
    h = {**dining_auth_headers(), "X-Tenant-Id": "MGV"}
    resp = dining_client.get("/v1/venues", headers={**h, "X-Sim-Fault": "not_a_real_fault"})
    assert resp.status_code == 400


def test_inapplicable_fault_value_returns_400_on_casino(casino_client) -> None:
    # webhook_delay is ticketing-only; casino (and every other sim) must reject it.
    h = {**casino_auth_headers(), "X-Tenant-Id": "MGV"}
    resp = casino_client.get(
        "/casino/v1/responsible-gaming/status",
        headers={**h, "X-Sim-Fault": "webhook_delay"},
        params={"guest_id": "g1"},
    )
    assert resp.status_code == 400


def test_unrecognized_latency_value_returns_400(dining_client) -> None:
    h = {**dining_auth_headers(), "X-Tenant-Id": "MGV"}
    resp = dining_client.get("/v1/venues", headers={**h, "X-Sim-Latency": "p999"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Business-logic faults: hold_expired / inventory_race, across holding sims.
# ---------------------------------------------------------------------------


def test_amenities_inventory_race_and_hold_expired(amenities_client) -> None:
    from sims.amenities.main import DB_PATH

    async def seed_one():
        conn = await aiosqlite.connect(DB_PATH)
        await conn.execute("INSERT OR REPLACE INTO services VALUES ('svc-f','MGV','spa','Massage',60,150.0,'zoneA','[]')")
        await conn.execute("INSERT OR REPLACE INTO practitioners VALUES ('prac-f','MGV','svc-f','female',0)")
        await conn.commit()
        await conn.close()

    _run(seed_one())
    h = {**amenities_auth_headers(), "X-Tenant-Id": "MGV"}

    # inventory_race: hold creation forced to 409 even though the slot is free.
    race_resp = amenities_client.post(
        "/amenities/v3/holds",
        headers={**h, "Idempotency-Key": "race-1", "X-Sim-Fault": "inventory_race"},
        json={"service_id": "svc-f", "date": "2026-10-01", "time": "09:00", "practitioner_id": "prac-f"},
    )
    assert race_resp.status_code == 409

    # Otherwise-identical request without the fault header succeeds.
    ok_resp = amenities_client.post(
        "/amenities/v3/holds",
        headers={**h, "Idempotency-Key": "race-2"},
        json={"service_id": "svc-f", "date": "2026-10-01", "time": "09:00", "practitioner_id": "prac-f"},
    )
    assert ok_resp.status_code == 201
    hold_id = ok_resp.json()["hold_id"]

    # hold_expired: confirm forced to behave as though this live hold had expired.
    expired_confirm = amenities_client.post(
        "/amenities/v3/bookings",
        headers={**h, "Idempotency-Key": "race-3", "X-Sim-Fault": "hold_expired"},
        json={"hold_id": hold_id, "guest_id": "g1"},
    )
    assert expired_confirm.status_code == 410

    # The hold is now genuinely expired (the fault forced a real state transition) —
    # a normal confirm afterward still fails, proving the fault had a real effect,
    # not just a one-off fake response.
    normal_confirm = amenities_client.post(
        "/amenities/v3/bookings",
        headers={**h, "Idempotency-Key": "race-4"},
        json={"hold_id": hold_id, "guest_id": "g1"},
    )
    assert normal_confirm.status_code == 410
