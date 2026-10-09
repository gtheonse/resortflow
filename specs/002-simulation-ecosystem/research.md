# Phase 0 Research: Simulation Ecosystem

Each item: Decision, Rationale, Alternatives considered. No open `NEEDS CLARIFICATION`
markers remain after this file — every one is resolved either by a fact already fixed in a
frozen upstream artifact (the five OpenAPI contracts, the constitution, ADR-006) or by a
decision made here.

## 1. Per-sim state persistence

**Decision**: One SQLite file per simulator, at `sims/<name>/data/state.db`, opened via
`aiosqlite`. Reset (FR-014) deletes and re-runs that sim's `seed.py` against a fresh file.

**Rationale**: Sim state is vendor-side simulation state, not ResortFlow's own durable state — it
must not be conflated with the checkpointer or `hold_ledger` (both Postgres, both owned by
feature 001, both governed by constitution principle V). SQLite gives crash-survivable state
within a single sim process (useful for manually poking at a running sim during development)
at zero operational cost — no shared server, no connection pool, no dependency on Docker
Compose service startup order. Reset becomes "delete a file and reseed," which is what makes
FR-014 achievable without a container restart.

**Alternatives considered**:
- *In-memory only (a Python dict).* Simplest possible option and adequate for the contract
  test suite, but a sim restart during manual/demo use silently wipes state with no signal
  that anything changed, and it wouldn't demonstrate a sim "holding state across calls" in
  any way distinguishable from a stub if the same process never enters a hold cycle across
  a restart. Rejected as slightly too fragile for the "stateful, not stub" design principle
  in `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §9.
- *Shared Postgres schema (one database, five sims' tables).* Would let a future feature
  query across sims easily, but it couples five independently-owned services to one shared
  server for no benefit this phase needs, and would make FR-014's reset a cross-sim
  operation with lock contention risk instead of a per-sim file delete. Also risks confusion
  with the ResortFlow-owned `hold_ledger` in the same database, which principle V explicitly warns
  against conflating.

## 2. Fault-injection and reset placement relative to the frozen contracts

**Decision**: `X-Sim-Fault` and `X-Sim-Latency` are read as ordinary HTTP headers by shared
middleware in `sims/_shared/faults.py`, applied to every route, without appearing as a
formal `parameters` entry in any OpenAPI file. Reset is a single out-of-band route per sim
(e.g. `POST /__sim__/reset`) under a path prefix that the contract-conformance test suite
(FR-011) is configured to ignore.

**Rationale**: Every one of the five frozen specs already documents these controls only in
free-text `description` fields ("SIMULATION CONTROLS: X-Sim-Fault, X-Sim-Latency"), never as
formal parameters — that convention is already set by feature 001/the contract authors, this
decision just follows it consistently, including for `casino-sim`, whose description block
omits the "SIMULATION CONTROLS" line entirely (an inconsistency in the prose, not a signal
that casino is exempt — nothing in the contract or the constitution suggests fault injection
should be weaker on the read-only sim, and FR-005/FR-013 require it uniformly). Keeping both
controls outside the versioned contract surface means adding them never requires touching a
frozen file, which the working agreement forbids doing unilaterally. See ADR-008.

**Alternatives considered**:
- *Amend the OpenAPI files to formally declare these parameters.* Would make the contracts
  self-documenting, but editing a frozen Phase 0 artifact requires raising it as a blocker
  per the working agreement, not deciding it inside this plan. Deferred — if a later phase
  wants this, it goes through that process, not this one.
- *A separate control-plane HTTP service (a "fault injector" microservice sitting in front
  of the sims).* More closely mirrors how a real chaos-engineering harness might work, but
  adds a sixth service, a network hop, and a coordination problem (which sim does a given
  fault apply to) that a per-request header avoids entirely. Rejected as unnecessary
  complexity for what FR-005 actually requires: a per-request, deterministic, reproducible
  signal.

## 3. Async ticketing fulfillment: webhook target for Phase 1 testing

**Decision**: `sims/_shared/webhook_harness.py` provides a minimal in-process receiver
(a route that records incoming callbacks, queryable by test code) that ticketing-sim's
`/webhooks/registrations` can register against during tests, plus a configurable real target
URL for later integration with feature 002. The contract test suite and the unattended
FR-012-style scenario for ticketing use the in-process receiver; nothing about production
webhook delivery is invented here beyond the bounded-retry-with-backoff behavior already
required by the spec's Clarifications.

**Rationale**: `docs/contracts/openapi/ticketing-sim.openapi.yaml` requires purchase to be
asynchronous (`202` now, webhook later) and the spec's Clarifications require that delivery
retry with backoff. Feature 002 (the MCP layer, the natural production webhook consumer)
does not exist yet, so without an in-process receiver this phase could not independently
verify SC-006 ("delivered for 100% of confirmed ticketing holds") — it would have to defer
that proof to feature 002, weakening this phase's own exit criteria. See ADR-009.

**Alternatives considered**:
- *Defer all webhook-delivery testing to feature 002.* Simpler for this phase, but SC-006
  and FR-006 would then be unverifiable within Phase 1's own exit criteria, contradicting
  the working agreement's "tests before the next phase starts."
- *Use `/orders/{order_id}` polling only, skip webhook delivery entirely in tests.* The
  contract offers this as an explicit fallback ("poll fallback if webhook is missed"), but
  using only the fallback path would never actually exercise webhook delivery, which is the
  behavior FR-006/SC-006 are about.

## 4. Hold-expiry testing without waiting out real TTLs

**Decision**: The hold engine (`sims/_shared/holds.py`) accepts an injectable clock; test
configuration runs it with a compressed time source (e.g. 1 simulated minute per real
second) or a manual time-advance hook, rather than sleeping the real 8–15 minutes a contract
TTL specifies.

**Rationale**: FR-012's unattended scenario and SC-002 must complete in a normal CI cycle.
Waiting out a real 900-second PMS hold TTL in every test run would make the test suite
prohibitively slow without adding any confidence the compressed version doesn't already
provide, since the logic under test (a comparison against `expires_at`) is agnostic to how
fast wall-clock time actually moves.

**Alternatives considered**:
- *Real-time waits with a short TTL override just for tests (e.g. a 5-second test-only
  TTL).* Simpler than an injectable clock, but it means test code exercises a TTL value the
  contract never actually specifies, which weakens confidence that the *real* per-resource
  TTLs (which matter for the saga-ordering design in ADR-006) are what's under test.
  Rejected in favor of testing the real contract TTLs, just on compressed time.

## 5. Seed data volumes per tenant

**Decision**: A roughly 60/40 MGV/MHB split, rounded per category:

| Entity | Total | MGV | MHB |
|---|---|---|---|
| Venues | 8 | 5 | 3 |
| Events | 40 | 24 | 16 |
| Rooms | 200 | 120 | 80 |
| Spa slots | 60 | 36 | 24 |
| Guest profiles | 50 | 30 | 20 |

**Rationale**: The spec's Clarifications require MGV (the larger Las Vegas flagship) to
receive a greater share of every count than MHB, with the exact ratio left to this plan.
60/40 is a clean, easily-explained ratio (`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` names
MGV as the flagship without giving a number) that divides four of the five totals exactly;
venues (8) is rounded up to 5/3 rather than the un-rounded 4.8/3.2, since a fractional venue
count is meaningless. See `contracts/seed-data-spec.md` for the full per-category record
shapes.

**Alternatives considered**: An exact ratio dividing all five totals cleanly does not exist
(the greatest common divisor of 8, 40, 200, 60, 50 is 2, which only yields an even split) —
so any weighted split requires rounding somewhere. 60/40 was chosen over other round numbers
(e.g. 55/45, 70/30) as the most defensible "clearly weighted, not arbitrary" figure without
overstating a size difference the source documents don't quantify.

## 6. Auth scheme confirmation (no decision needed — already fixed by contract)

Each simulator's authentication is already fully specified and mutually distinct in its
frozen OpenAPI file, satisfying FR-007 without any decision needed here:

| Sim | Scheme | Contract reference |
|---|---|---|
| amenities | HTTP Basic | `amenities-sim.openapi.yaml` `securitySchemes.BasicAuth` |
| dining | Static API key (`X-Dining-Key` header) | `dining-sim.openapi.yaml` `securitySchemes.DiningApiKey` |
| pms | OAuth2 client-credentials (own token endpoint) | `pms-sim.openapi.yaml` `securitySchemes.PmsOAuth` |
| ticketing | OAuth2 client-credentials + HMAC-SHA256 request signature | `ticketing-sim.openapi.yaml` `securitySchemes.OAuth2` + `Signature` parameter |
| casino | HTTP Bearer | `casino-sim.openapi.yaml` `securitySchemes.CasinoBearer` |

## 7. Contract-conformance test tooling

**Decision**: `schemathesis`, run once per sim directly against its frozen OpenAPI file and
a running instance of that sim, as the FR-011 contract test suite.

**Rationale**: Generates and validates requests/responses straight from the OpenAPI schema,
so contract drift (a field renamed, a status code removed) fails the suite without hand
duplicating every schema assertion the YAML file already states. Property-based generation
also incidentally exercises edge cases (boundary `party_size`, `quantity` limits) the spec's
Edge Cases section calls out.

**Alternatives considered**: Hand-written `pytest` assertions per endpoint — full control,
but five sims' worth of manually-maintained schema assertions is exactly the kind of drift
risk the working agreement calls out ("contract drift is the primary delivery risk"), since
nothing forces the hand-written assertions to stay in sync with the YAML if either changes.
