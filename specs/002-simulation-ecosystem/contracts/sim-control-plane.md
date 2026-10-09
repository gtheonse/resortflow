# Interface Contract: Simulator Control Plane

**Feature**: `002-simulation-ecosystem` | Permanent home: this file (Track A, `sims/`)

This is a **test/ops interface**, deliberately kept separate from each simulator's frozen
vendor-facing OpenAPI contract (see `research.md` item 2 and ADR-008). Downstream tracks —
Track B writing MCP adapters, Track E writing evals against these sims, anyone running the
contract test suite — read this file to know how to drive the sims' non-vendor behavior.
It is not versioned by, and is not part of, `docs/contracts/openapi/*.yaml`.

## Fault injection

**Header**: `X-Sim-Fault: <value>` on any request to any of the five sims.

| Value | Effect | Applies to |
|---|---|---|
| `timeout` | Handler does not respond within the caller's timeout window. | All five |
| `error_500` | Immediate `500`, in that sim's own error envelope shape. | All five |
| `rate_limit` | `429` with `Retry-After` header. | All five |
| `hold_expired` | Next hold/confirm operation behaves as though the targeted hold had already expired. | amenities, dining, pms, ticketing |
| `inventory_race` | `409`, as if another caller claimed the resource first. | amenities, dining, pms, ticketing |
| `webhook_delay` | Fulfillment notification delayed past the first retry attempt. | ticketing only |
| `schema_drift` | The handler runs normally, but a successful (`<400`) JSON response is corrupted before it leaves the sim: one top-level field the real schema declares required is dropped, and one field no schema declares at all is added — the body is intentionally, doubly schema-invalid regardless of which endpoint was hit. Added by Feature 003 (T071) so the MCP gateway's error-classification layer (`gateway._shared.errors.classify_body`'s `ValidationError` branch) can be proven against a genuinely malformed live response, not only a hand-constructed unit-test payload (SC-016). | All five |

Absence of the header means normal processing — no fault. An unrecognized value or a value
that does not apply to the targeted sim (e.g. `webhook_delay` sent to amenities) is a `400`
naming the invalid value; it does not silently no-op, since a silently-ignored typo in a
test's fault header would defeat the determinism FR-013 requires.

Exactly one `X-Sim-Fault` value is honored per request (`research.md` — Phase 1 spec's
Assumptions rule out combining multiple simultaneous faults on one request).

## Latency injection

**Header**: `X-Sim-Latency: p50 | p95 | p99`

Adds a delay drawn from that sim's configured latency profile before responding. May be
combined with `X-Sim-Fault` on the same request (the fault still applies; the response is
just slower). Latency profile values (the actual millisecond figures per `p50/p95/p99`) are
an implementation/config detail set in `tasks.md`, not fixed by this contract.

## Reset

**Endpoint**: `POST /__sim__/reset` on each sim, no request body.

Deletes that sim's SQLite state file and re-runs its `seed.py`, restoring the seeded
baseline (`contracts/seed-data-spec.md`) — all holds, bookings, and idempotency records from
prior use are gone. Response: `200` with `{"reset": true, "tenant_ids": ["MGV", "MHB"]}`.
Idempotent: resetting an already-freshly-reset sim is a no-op that still returns `200`.

This path is outside every sim's contract-conformance test run (FR-011 tests the vendor
surface only) and is never called by anything other than test/dev tooling — no production
analog exists, because no real vendor exposes a "wipe your booking history" endpoint.

## Webhook test harness (ticketing only)

`ticketing-sim` supports `POST /webhooks/registrations` per its frozen contract. For local
testing, `sims/_shared/webhook_harness.py` exposes:

- `POST /__sim__/webhook-sink` — accepts any payload, records it, returns `204`. Tests
  register this URL as their `callback_url`.
- `GET /__sim__/webhook-sink/{order_id}` — returns the recorded callback(s) for that order,
  or `404` if none arrived yet. Used to assert delivery in the FR-012-style unattended
  scenario for ticketing, and to verify the bounded-retry-with-backoff behavior when
  `X-Sim-Fault: webhook_delay` is set on the triggering purchase.

This harness is Phase 1's own stand-in for feature 002's eventual real webhook consumer
(ADR-009) — it is not a production interface and carries no auth, since it only exists
inside the local/test environment.
