"""User Story 1 integration test (T021, SC-009): each sim rejects a request with no
credentials and a request carrying one of the *other* four sims' credentials, accepting
only its own scheme — amenities (Basic), casino (Bearer), dining (X-Dining-Key), pms
(OAuth2), ticketing (OAuth2 + HMAC, including a deliberately invalid X-TM-Signature)."""

from __future__ import annotations

import json

from sims.tests.fixtures.clients import (
    amenities_auth_headers,
    casino_auth_headers,
    dining_auth_headers,
    pms_auth_headers,
    ticketing_auth_headers,
)

TENANT = {"X-Tenant-Id": "MGV"}


def test_amenities_rejects_no_auth_and_foreign_auth(
    amenities_client, casino_client, dining_client, pms_client, ticketing_client
) -> None:
    no_auth = amenities_client.get("/amenities/v3/services", headers=TENANT)
    assert no_auth.status_code == 401

    for foreign in (
        casino_auth_headers(),
        dining_auth_headers(),
        pms_auth_headers(pms_client),
        ticketing_auth_headers(ticketing_client),
    ):
        resp = amenities_client.get("/amenities/v3/services", headers={**TENANT, **foreign})
        assert resp.status_code == 401, f"amenities accepted a foreign credential: {foreign}"

    ok = amenities_client.get("/amenities/v3/services", headers={**TENANT, **amenities_auth_headers()})
    assert ok.status_code == 200


def test_casino_rejects_no_auth_and_foreign_auth(
    amenities_client, casino_client, dining_client, pms_client, ticketing_client
) -> None:
    no_auth = casino_client.get(
        "/casino/v1/responsible-gaming/status", headers=TENANT, params={"guest_id": "g1"}
    )
    assert no_auth.status_code in (401, 403)

    for foreign in (
        amenities_auth_headers(),
        dining_auth_headers(),
        pms_auth_headers(pms_client),
        ticketing_auth_headers(ticketing_client),
    ):
        resp = casino_client.get(
            "/casino/v1/responsible-gaming/status", headers={**TENANT, **foreign}, params={"guest_id": "g1"}
        )
        assert resp.status_code in (401, 403), f"casino accepted a foreign credential: {foreign}"

    ok = casino_client.get(
        "/casino/v1/responsible-gaming/status", headers={**TENANT, **casino_auth_headers()}, params={"guest_id": "g1"}
    )
    assert ok.status_code == 200


def test_dining_rejects_no_auth_and_foreign_auth(
    amenities_client, casino_client, dining_client, pms_client, ticketing_client
) -> None:
    no_auth = dining_client.get("/v1/venues", headers=TENANT)
    assert no_auth.status_code == 401

    for foreign in (
        amenities_auth_headers(),
        casino_auth_headers(),
        pms_auth_headers(pms_client),
        ticketing_auth_headers(ticketing_client),
    ):
        resp = dining_client.get("/v1/venues", headers={**TENANT, **foreign})
        assert resp.status_code == 401, f"dining accepted a foreign credential: {foreign}"

    ok = dining_client.get("/v1/venues", headers={**TENANT, **dining_auth_headers()})
    assert ok.status_code == 200


def test_pms_rejects_no_auth_and_foreign_auth(
    amenities_client, casino_client, dining_client, pms_client, ticketing_client
) -> None:
    params = {"arrival": "2026-10-01", "departure": "2026-10-03", "adults": 2}
    no_auth = pms_client.get("/pms/v5/availability/rooms", headers=TENANT, params=params)
    assert no_auth.status_code == 401

    for foreign in (
        amenities_auth_headers(),
        casino_auth_headers(),
        dining_auth_headers(),
        ticketing_auth_headers(ticketing_client),
    ):
        resp = pms_client.get("/pms/v5/availability/rooms", headers={**TENANT, **foreign}, params=params)
        assert resp.status_code == 401, f"pms accepted a foreign credential: {foreign}"

    ok = pms_client.get(
        "/pms/v5/availability/rooms", headers={**TENANT, **pms_auth_headers(pms_client)}, params=params
    )
    assert ok.status_code == 200


def test_ticketing_rejects_no_auth_foreign_auth_and_bad_signature(
    amenities_client, casino_client, dining_client, pms_client, ticketing_client
) -> None:
    params = {"start_utc": "2026-01-01T00:00:00Z", "end_utc": "2027-01-01T00:00:00Z"}
    no_auth = ticketing_client.get("/discovery/v2/events", headers=TENANT, params=params)
    assert no_auth.status_code == 401

    for foreign in (
        amenities_auth_headers(),
        casino_auth_headers(),
        dining_auth_headers(),
        pms_auth_headers(pms_client),
    ):
        resp = ticketing_client.get("/discovery/v2/events", headers={**TENANT, **foreign}, params=params)
        assert resp.status_code == 401, f"ticketing accepted a foreign credential: {foreign}"

    # A valid bearer token but a deliberately invalid X-TM-Signature on a mutating call.
    from sims.ticketing.main import CLIENT_ID, CLIENT_SECRET

    token_resp = ticketing_client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
    )
    access_token = token_resp.json()["access_token"]
    body = {"event_id": "evt-x", "section_id": "sec-x", "quantity": 1}
    bad_sig_resp = ticketing_client.post(
        "/discovery/v2/reservations",
        headers={
            **TENANT,
            "Authorization": f"Bearer {access_token}",
            "X-TM-Signature": "0" * 64,
            "Idempotency-Key": "bad-sig-1",
            "Content-Type": "application/json",
        },
        content=json.dumps(body),
    )
    assert bad_sig_resp.status_code == 401
    assert bad_sig_resp.json()["detail"]["fault"]["type"] == "SIGNATURE_INVALID"

    ok = ticketing_client.get("/discovery/v2/events", headers={**TENANT, **ticketing_auth_headers(ticketing_client)}, params=params)
    assert ok.status_code == 200
