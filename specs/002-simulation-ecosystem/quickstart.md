# Quickstart: Validating the Simulation Ecosystem

Proves the feature works end-to-end. Assumes `tasks.md` has been implemented and the sims
are wired into `docker-compose.yml` (an integration-commit step per `plan.md`'s Structure
Decision).

## Prerequisites

- Docker Compose stack up: `make up`
- Environment seeded: `make seed` (loads the counts in `contracts/seed-data-spec.md`)

## 1. Contract conformance (FR-011, SC-001)

```bash
pytest sims/tests/contract/
```

Expected: all five sims pass their `schemathesis` run against their own frozen
`docs/contracts/openapi/*.yaml`, zero contract violations.

## 2. Hold → confirm, with a safe retry (User Stories 1 & 3, FR-002/FR-004, SC-003)

Against any of amenities/dining/pms/ticketing (dining shown). The dining static key is
`dining-sim-static-key-MRI` (`sims/dining/main.py`'s `API_KEY` — every sim's dev
credentials are hard-coded local test-double secrets, not real secrets); `ven-mgv-01` is
one of the 5 seeded MGV venues (`sims/dining/seed.py`):

```bash
curl -X POST http://localhost:9002/v1/holds \
  -H "X-Tenant-Id: MGV" -H "X-Dining-Key: dining-sim-static-key-MRI" \
  -H "Idempotency-Key: demo-hold-1" \
  -d '{"venue_id": "ven-mgv-01", "date": "2026-10-01", "time": "18:30", "party_size": 2}'
```

Expected: `201` with a `hold_id` and `expires_at` ~600s out. Confirm it, then repeat the
exact same confirm request with the same `Idempotency-Key`:

```bash
curl -X POST http://localhost:9002/v1/reservations \
  -H "X-Tenant-Id: MGV" -H "X-Dining-Key: dining-sim-static-key-MRI" \
  -H "Idempotency-Key: demo-confirm-1" \
  -d '{"hold_id": "<hold_id>", "guest": {"guest_id": "guest-mgv-001"}}'
```

Expected: `201` the first time, `200` on the exact retry, same `reservation_id` both times —
never a second reservation.

## 3. Fault injection, deterministically (User Story 2, FR-005/FR-013, SC-004)

```bash
curl -X POST http://localhost:9002/v1/holds \
  -H "X-Tenant-Id: MGV" -H "X-Dining-Key: dining-sim-static-key-MRI" \
  -H "Idempotency-Key: demo-fault-1" -H "X-Sim-Fault: error_500" \
  -d '{"venue_id": "ven-mgv-01", "date": "2026-10-01", "time": "19:00", "party_size": 2}'
```

Expected: `500` in dining's `{error:{code,detail}}` envelope, every time this is sent, while
an identical request without the header succeeds normally. Repeat with `timeout`,
`rate_limit`, `inventory_race` and (against ticketing only) `webhook_delay` — see
`contracts/sim-control-plane.md` for the full table.

## 4. Unattended hold→expire→release (User Story 4, FR-003/FR-012, SC-002)

```bash
sims/tests/integration/hold_expiry_scenario.py --sim dining --tenant MGV
```

Expected (unattended, using the compressed test clock from `research.md` item 4): places a
hold, advances the clock past dining's 600s default TTL without confirming, and asserts the
resource is available again with no manual step — exits non-zero on failure, suitable for a
CI job.

## 5. Async ticketing fulfillment via webhook (User Story 5, FR-006, SC-006)

```bash
pytest sims/tests/integration/test_ticketing_webhook.py -q
```

Expected: registers `sims/_shared/webhook_harness`'s sink, holds seats, purchases (`202`),
and polls `GET /__sim__/webhook-sink/{order_id}` until the fulfillment callback appears —
asserting it never appeared *inside* the `202` response itself. A second test in the same
file sets `X-Sim-Fault: webhook_delay` on the purchase call to prove the bounded
retry-with-backoff path is what ultimately delivers the callback.

## 6. Tenant isolation (User Story 6, FR-010, SC-005)

```bash
pytest sims/tests/integration/test_tenant_isolation.py -q
```

Expected: for each sim, a request scoped to `MGV` cannot read or hold an `MHB`-seeded
resource (and vice versa) — every cross-tenant attempt returns the contract's not-found/
refused response, never the other tenant's data.

## 7. Reset to baseline (FR-014, SC-008)

```bash
curl -X POST http://localhost:9002/__sim__/reset
```

Expected: `200`, and a subsequent `GET /venues` shows exactly the seeded 8 (5 MGV / 3 MHB)
venues again, with no holds or reservations left over from steps 1–6.

## Full pass

Running all of the above for all five sims (casino is read-only — steps 1, 3, 6, 7 apply to
it; steps 2, 4, 5 do not) with zero failures is this feature's exit condition, matching
`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §Phase 1's stated exit criteria.
