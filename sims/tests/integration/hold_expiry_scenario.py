#!/usr/bin/env python
"""Unattended hold->expire->release scenario (US4, T031; FR-003/FR-012, SC-002).

Runs against a live sim instance over HTTP (e.g. `make up` + `docker compose --profile
sims`), per `quickstart.md` step 4: places a hold, lets it pass its TTL with no further
action, and asserts the underlying resource is available again with no manual step —
exits non-zero on failure, suitable for a CI job.

Uses each hold-creation endpoint's own contract-documented `ttl_seconds` override (a
real, frozen-contract field, not a new interface) with a short value so the scenario
completes in a few seconds of real wall-clock time rather than the real 8-15 minute
default TTLs, while still exercising the exact same expiry code path (the comparison
against `expires_at` does not care what the TTL value actually is — see
`specs/002-simulation-ecosystem/research.md` item 4).

Usage:
    python sims/tests/integration/hold_expiry_scenario.py --sim dining --tenant MGV
    python sims/tests/integration/hold_expiry_scenario.py --sim pms --tenant MHB --base-url http://localhost:9001
"""

from __future__ import annotations

import argparse
import sys
import time

import httpx

DEFAULT_BASE_URLS = {
    "amenities": "http://localhost:9005/amenities/v3",
    "dining": "http://localhost:9002/v1",
    "pms": "http://localhost:9001/pms/v5",
    "ticketing": "http://localhost:9003/discovery/v2",
}

# Test-only credentials matching each sim's hard-coded dev auth (sims/<name>/main.py).
# A real deployment would inject these from a secrets store; these sims are local test
# doubles whose credentials are intentionally static and documented in their own code.
AMENITIES_AUTH = ("amenities-mcp", "amenities-sim-secret")
DINING_KEY = "dining-sim-static-key-MRI"
PMS_CLIENT = ("pms-sim-client", "pms-sim-client-secret")


def _fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def run_dining(base_url: str, tenant: str) -> None:
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        h = {"X-Tenant-Id": tenant, "X-Dining-Key": DINING_KEY}
        venues = client.get("/venues", headers=h).json()["venues"]
        if not venues:
            _fail(f"no seeded venues for tenant {tenant} — run `make seed` first")
        venue_id = venues[0]["venue_id"]

        hold = client.post(
            "/holds",
            headers={**h, "Idempotency-Key": "expiry-scenario-hold"},
            json={"venue_id": venue_id, "date": "2026-12-01", "time": "18:30", "party_size": 2, "ttl_seconds": 2},
        )
        if hold.status_code != 201:
            _fail(f"hold creation failed: {hold.status_code} {hold.text}")
        hold_id = hold.json()["hold_id"]
        print(f"placed hold {hold_id}, ttl=2s")

        time.sleep(3)

        confirm = client.post(
            "/reservations",
            headers={**h, "Idempotency-Key": "expiry-scenario-confirm"},
            json={"hold_id": hold_id, "guest": {"guest_id": "expiry-scenario-guest"}},
        )
        if confirm.status_code != 410:
            _fail(f"expected 410 confirming an expired hold, got {confirm.status_code} {confirm.text}")
        print("confirming the expired hold correctly returned 410")

        avail = client.get(
            f"/venues/{venue_id}/availability",
            headers=h,
            params={"date": "2026-12-01", "party_size": 2},
        )
        slots = avail.json()["slots"]
        slot = next((s for s in slots if s["time"] == "18:30"), None)
        if slot is None or slot["available_covers"] <= 0:
            _fail("resource did not become available again after hold expiry")
        print("resource is available again with no manual step — PASS")


def run_amenities(base_url: str, tenant: str) -> None:
    with httpx.Client(base_url=base_url, timeout=10.0, auth=AMENITIES_AUTH) as client:
        h = {"X-Tenant-Id": tenant}
        services = client.get("/services", headers=h).json()["services"]
        if not services:
            _fail(f"no seeded services for tenant {tenant} — run `make seed` first")
        service_id = services[0]["service_id"]
        avail = client.get(f"/services/{service_id}/availability", headers=h, params={"date": "2026-12-01"}).json()
        slots = avail["slots"]
        if not slots:
            _fail(f"no available slots for seeded service {service_id}")
        practitioner_id = slots[0]["practitioner_id"]
        time_slot = slots[0]["time"]

        hold = client.post(
            "/holds",
            headers={**h, "Idempotency-Key": "expiry-scenario-hold"},
            json={"service_id": service_id, "date": "2026-12-01", "time": time_slot, "practitioner_id": practitioner_id, "ttl_seconds": 2},
        )
        if hold.status_code != 201:
            _fail(f"hold creation failed: {hold.status_code} {hold.text}")
        hold_id = hold.json()["hold_id"]
        print(f"placed hold {hold_id}, ttl=2s")

        time.sleep(3)

        confirm = client.post(
            "/bookings",
            headers={**h, "Idempotency-Key": "expiry-scenario-confirm"},
            json={"hold_id": hold_id, "guest_id": "expiry-scenario-guest"},
        )
        if confirm.status_code != 410:
            _fail(f"expected 410 confirming an expired hold, got {confirm.status_code} {confirm.text}")
        print("confirming the expired hold correctly returned 410")

        avail_after = client.get(f"/services/{service_id}/availability", headers=h, params={"date": "2026-12-01"}).json()
        if not any(s["time"] == time_slot and s["practitioner_id"] == practitioner_id for s in avail_after["slots"]):
            _fail("resource did not become available again after hold expiry")
        print("resource is available again with no manual step — PASS")


def run_pms(base_url: str, tenant: str) -> None:
    with httpx.Client(base_url=base_url, timeout=10.0) as client:
        token_url = base_url.rsplit("/pms/v5", 1)[0] + "/pms/oauth2/token"
        token_resp = client.post(
            token_url,
            data={"grant_type": "client_credentials", "client_id": PMS_CLIENT[0], "client_secret": PMS_CLIENT[1]},
        )
        access_token = token_resp.json()["access_token"]
        h = {"X-Tenant-Id": tenant, "Authorization": f"Bearer {access_token}"}

        avail = client.get(
            "/availability/rooms", headers=h, params={"arrival": "2026-12-01", "departure": "2026-12-03", "adults": 2}
        ).json()
        offers = avail["RESPONSE"]["DATA"]["ROOM_OFFERS"]
        if not offers:
            _fail(f"no seeded room offers for tenant {tenant} — run `make seed` first")
        offer_id = offers[0]["OFFER_ID"]

        hold = client.post(
            "/holds/rooms",
            headers={**h, "Idempotency-Key": "expiry-scenario-hold"},
            json={"OFFER_ID": offer_id, "ARRIVAL": "2026-12-01", "DEPARTURE": "2026-12-03", "TTL_SECONDS": 2},
        )
        if hold.status_code != 201:
            _fail(f"hold creation failed: {hold.status_code} {hold.text}")
        hold_id = hold.json()["HOLD_ID"]
        print(f"placed hold {hold_id}, ttl=2s")

        time.sleep(3)

        confirm = client.post(
            "/reservations",
            headers={**h, "Idempotency-Key": "expiry-scenario-confirm"},
            json={"HOLD_ID": hold_id, "GUEST_PROFILE_ID": "expiry-scenario-guest", "PAYMENT_TOKEN": "tok"},
        )
        if confirm.status_code != 410:
            _fail(f"expected 410 confirming an expired hold, got {confirm.status_code} {confirm.text}")
        print("confirming the expired hold correctly returned 410")

        avail_after = client.get(
            "/availability/rooms", headers=h, params={"arrival": "2026-12-01", "departure": "2026-12-03", "adults": 2}
        ).json()
        offers_after = avail_after["RESPONSE"]["DATA"]["ROOM_OFFERS"]
        if not any(o["OFFER_ID"] == offer_id for o in offers_after):
            _fail("resource did not become available again after hold expiry")
        print("resource is available again with no manual step — PASS")


RUNNERS = {"amenities": run_amenities, "dining": run_dining, "pms": run_pms}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sim", required=True, choices=sorted(RUNNERS) + ["ticketing"])
    parser.add_argument("--tenant", required=True, choices=["MGV", "MHB"])
    parser.add_argument("--base-url", default=None, help="Override the sim's default local base URL")
    args = parser.parse_args()

    if args.sim == "ticketing":
        # Ticketing's shortest-in-the-system TTL (480s default) still accepts the same
        # ttl_seconds override; its hold-expiry story is otherwise identical to the
        # other three holding sims and is exercised end-to-end by
        # test_hold_expiry.py / test_ticketing_webhook.py rather than duplicated here.
        print("ticketing hold expiry: covered by sims/tests/integration/test_hold_expiry.py "
              "and the async purchase scenario (US5) — no separate CLI runner needed.")
        return

    base_url = args.base_url or DEFAULT_BASE_URLS[args.sim]
    RUNNERS[args.sim](base_url, args.tenant)


if __name__ == "__main__":
    main()
