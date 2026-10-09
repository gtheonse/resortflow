# Phase 1 Data Model: Simulation Ecosystem

This is the data model of the **simulators' own vendor-side state** — it is deliberately
separate from, and must never be confused with, feature 001's `ResortFlowState` / `hold_ledger`
(ResortFlow's own conversation state and write-ahead mutation record). Every entity below lives
inside one sim's SQLite file, scoped to that sim.

## Cross-cutting entities (shared shape, sim-specific field names)

### Tenant

Not a stored entity — a value validated on every request via the `X-Tenant-Id` header,
constrained to `{MGV, MHB}` by every frozen contract. Every other entity below carries a
tenant scope, either explicitly (a `tenant_id` column) or implicitly (rows are partitioned
into per-tenant SQLite files/tables — an implementation choice left to `tasks.md`, not fixed
here, since either satisfies FR-010 equally).

### Hold (amenities, dining, pms, ticketing only — not casino)

The generic shape `sims/_shared/holds.py` operates on; each sim's `main.py` maps its
contract-specific request/response fields onto this shape at the boundary.

| Field | Type | Notes |
|---|---|---|
| `hold_id` | string | Vendor-assigned, generated on creation. Contract field name varies (`hold_id`, `HOLD_ID`). |
| `tenant_id` | enum `{MGV, MHB}` | From `X-Tenant-Id`. |
| `resource_ref` | string | What's held — a room offer, a spa slot, a dining slot, a seat section. Sim-specific shape. |
| `idempotency_key` | string | From the `Idempotency-Key` header that created the hold. |
| `status` | enum `{active, confirmed, released, expired}` | Contract-visible as `active/expired/converted/released` (dining, ticketing) or `ACTIVE/EXPIRED/CONVERTED/RELEASED` (pms) — `confirmed` here maps to the contract's `converted`. |
| `ttl_seconds` | integer | Resource-specific default, from the frozen contract (see table below); caller may override down to the contract's stated max. |
| `expires_at` | timestamp | `created_at + ttl_seconds`, recomputed only if never confirmed/released. |
| `created_at` | timestamp | |

**Resource-specific TTL defaults/maxima** (from the frozen contracts — not decided in this
feature, see `checklists/requirements.md` iteration 3):

| Sim | Endpoint | Default TTL | Max TTL |
|---|---|---|---|
| amenities | `POST /holds` | 600s | — (none stated) |
| dining | `POST /holds` | 600s | 900s |
| pms | `POST /holds/rooms` | 900s | — (none stated) |
| ticketing | `POST /reservations` | 480s | 600s |

**State transitions** (identical across all four sims, enforced by `sims/_shared/holds.py`):

```
        create              confirm
(none) ────────▶ active ─────────────▶ confirmed (terminal)
                   │  │
          release  │  │ ttl elapses, no confirm
                   ▼  ▼
              released      expired      (both terminal)
```

- `active → confirmed`: only valid transition into a terminal, bookable state. Requires a
  live (non-expired) hold and an `Idempotency-Key`; retried with the same key, returns the
  original confirmation (FR-004).
- `active → released`: explicit caller action (`DELETE /holds/{id}`) or the compensation
  path. Retried with the same key, is a no-op (FR-004).
- `active → expired`: automatic, no caller action, enforced by the reaper described in
  research.md item 4 (FR-003).
- `expired → *` and `confirmed → *`, `released → *`: no transition permitted. A confirm
  against an expired hold returns the contract's `410` (every sim); a second release against
  an already-released hold is a safe no-op, never an error that implies state changed (edge
  cases in spec.md).
- **A confirm carrying a *different* idempotency key than the first, successful confirm**
  (spec.md edge case) cannot produce a second booking: the hold is already `confirmed`
  (terminal) by the time the second call arrives, so `confirmed → *` refuses the transition
  regardless of which key accompanies it. The protection here is the state machine, not the
  idempotency store — the store only governs *retries of the same call*, not a second,
  differently-keyed call arriving after the hold is already terminal.
- **A release against an already-`confirmed` hold** (spec.md edge case, distinct from
  releasing an already-*released* hold above) is refused for the same reason: `confirmed` is
  terminal, so no domain's release endpoint may transition it to `released`. Each sim
  returns its contract's appropriate conflict response (e.g. amenities' cancellation-window
  `409`) rather than silently accepting the release.

### Booking / Reservation / Order (the confirmed counterpart to Hold)

Sim-specific name and fields (`booking_id` + amenities fields, `reservation_id` +
dining fields, `CONFIRMATION_NUMBER` + pms fields, `order_id` + ticketing fields — ticketing
additionally carries `status: PENDING → CONFIRMED|FAILED` since purchase is async). Common
shape: an identifier, a back-reference to the `hold_id` it was confirmed from, the
tenant, and a cancellation/refund path that is each contract's compensating action.

### Idempotency Record

Not guest-visible; the store `sims/_shared/idempotency.py` maintains per sim.

| Field | Type | Notes |
|---|---|---|
| `idempotency_key` | string, unique per sim | The join key — same role as feature 001's `hold_ledger.idempotency_key`, but this is the *vendor's* copy, not ResortFlow's. |
| `endpoint` | string | Which mutating operation this key was used for. |
| `request_fingerprint` | string | Hash of the normalized request body, to detect a genuinely different request reusing the same key (edge case in spec.md). |
| `response_snapshot` | JSON | The exact response returned the first time; replayed verbatim on retry. |
| `created_at` | timestamp | |

### Fault Injection Signal

Not stored — parsed per-request from `X-Sim-Fault` and `X-Sim-Latency` headers by
`sims/_shared/faults.py`. Enumerated values, taken from the frozen contracts' description
blocks (dining and ticketing enumerate them explicitly; amenities and pms declare the
headers without enumerating values, so this feature fixes the shared set below for
consistency across all five sims per FR-005):

| Value | Effect |
|---|---|
| `timeout` | Handler hangs past a client-visible timeout threshold, no response body. |
| `error_500` | Immediate `500` with that sim's own error envelope shape. |
| `rate_limit` | `429` with `Retry-After` (dining, ticketing already contract this; extended uniformly). |
| `hold_expired` | Forces the next hold/confirm operation to behave as though the hold had already expired, regardless of actual `expires_at`. |
| `inventory_race` | Forces a `409` as if another caller claimed the resource first. |
| `webhook_delay` | Ticketing only — delays fulfillment notification past its first bounded-retry window, to exercise the retry-with-backoff path (spec Clarifications). |

`X-Sim-Latency`: `p50 | p95 | p99` — adds a simulated delay drawn from that sim's configured
latency profile instead of (or in addition to) a fault.

### Reset Operation

Not stored — a `POST /__sim__/reset` per sim (research.md item 2) that deletes and
re-seeds that sim's SQLite file. No request/response body beyond a confirmation, since reset
is idempotent by nature (resetting an already-reset sim is a no-op).

## Sim-specific inventory entities

### Venue (dining) / Service (amenities) / Room Offer (pms) / Event + Section (ticketing)

These are read from each contract's own schemas verbatim — this feature does not redefine
them, only seeds them. See `contracts/seed-data-spec.md` for the exact seeded counts and
representative field values per tenant.

### Guest Profile

Shared shape across sims that reference a guest (dining's `GuestContact`, pms's
`GUEST_PROFILE_ID`, amenities' `guest_id`, casino's `guest_id`): `guest_id`, `tenant_id`,
`display_name`, `phone`, `email`, plus casino-specific loyalty fields (`tier`,
`points_balance`, `comp_dollars`) and a responsible-gaming status record (`self_excluded`,
`exclusion_scope`, `limit_flags`, `offers_permitted`, `gaming_content_permitted`,
`mandated_message`) exactly matching `casino-sim.openapi.yaml`'s
`/responsible-gaming/status` schema — seeded, never computed, since this feature has no
policy logic of its own (constitution principle IV lives downstream, in feature 006).

## What this feature does NOT model

- `ResortFlowState`, `Hold`/`Confirmation` as ResortFlow-side domain entities, `hold_ledger` rows,
  `SagaEvent` — all feature 001, all Postgres, all out of scope here. The `Hold` entity
  above is the *vendor's* record of a hold, not ResortFlow's.
- MCP tool schemas, scopes, or the `resortflow.<domain>.<verb>_<noun>` naming convention —
  feature 002.
- Any dietary/allergen *enforcement* logic (the post-flight structured-field assertion) —
  feature 006. This feature only preserves the structured fields the contracts already
  define.
