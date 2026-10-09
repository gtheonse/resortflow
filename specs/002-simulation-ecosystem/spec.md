# Feature Specification: Simulation Ecosystem

**Feature Branch**: `002-simulation-ecosystem`

**Created**: 2026-09-10

**Status**: Draft

**Input**: User description: "Phase 1 — Simulation Ecosystem (Track A). Build five stateful, fault-injectable vendor simulators — amenities, casino, dining, pms, ticketing — that implement the frozen OpenAPI contracts in docs/contracts/openapi/. Each sim must support a two-phase hold-then-confirm lifecycle for reservable resources (rooms, spa slots, tables, tickets): a hold has a TTL and auto-expires/releases the resource if not confirmed; confirming or releasing a hold is idempotent via an idempotency key so retries never double-book or double-release. Sims must support fault injection (a header-driven mechanism that deterministically reproduces specific failure modes — timeouts, 5xx errors, partial failures, degraded latency — on demand for testing) so downstream error-handling and saga/compensation logic can be exercised reliably. Ticketing must emit an asynchronous webhook on fulfilment rather than confirming synchronously. Each sim's authentication/authorization scheme should reflect a distinct, independent vendor integration (not a unified internal scheme) since real vendor APIs are inconsistent and downstream error-taxonomy normalization work depends on that realism. The environment must be seeded with 2 tenants (MGV — Las Vegas/Nevada jurisdiction, MHB — Boston/Massachusetts jurisdiction), 8 venues, 40 events, 200 rooms, 60 spa slots, and 50 guest profiles, all tenant-scoped so no data leaks across tenants. Exit criteria for this phase: every sim passes a contract test suite against its frozen OpenAPI spec; a scripted hold→expire→release cycle runs unattended end-to-end; and every supported fault mode is reproducible on demand via the injection header."

## Clarifications

### Session 2026-09-10

- Q: What should the hold time-to-live (TTL) be for reservable resources? → A: 5 minutes — standard hold duration for hospitality/ticketing vendors; balances realism and testability. **Superseded during `/speckit-plan`**: the frozen OpenAPI contracts in `docs/contracts/openapi/` already specify per-resource default/max TTLs (amenities 600s default; dining 600s default/900s max; pms 900s default; ticketing 480s default/600s max — deliberately shortest, since the plan's saga design commits the scarcest, most perishable inventory first). Contracts are frozen and take precedence over this spec's earlier general answer; see FR-003 and SC-002 below for the corrected, per-resource figures.
- Q: Should the seed data be split evenly across the two tenants, or weighted to reflect that MGV and MHB are different-sized properties? → A: Weighted — MGV (Las Vegas flagship) gets a larger share of each seeded count than MHB (Boston); exact per-tenant ratio is left to the implementation plan.
- Q: What should happen if the ticketing webhook fulfillment notification isn't delivered on the first attempt? → A: Bounded retry with backoff — the simulator retries delivery a small number of times before giving up; exact count/backoff left to the implementation plan.
- Q: Do the simulators need a way to reset or re-seed their state between test runs? → A: Explicit reset — each simulator exposes a way to reset its state back to the seeded baseline on demand.
- Correction (discovered during `/speckit-plan`, not a formal clarification question): the frozen `casino-sim` OpenAPI contract exposes no mutating endpoints at all — no holds, no bookings — consistent with the constitution's Governed Integration Surface principle ("the gaming domain is read-only... enforcement is the absence of mutating gaming tools from the registry"). FR-002/FR-003/FR-004/FR-012/FR-014 (hold lifecycle, idempotent confirm/release, unattended expiry test, reset) apply to the four simulators with reservable inventory — amenities, dining, pms, ticketing. Casino-sim is read-only informational/loyalty data by design and is covered instead by FR-001, FR-007, FR-010, and FR-011.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Build and test integrations against realistic vendor behavior (Priority: P1)

An engineer building the governed integration layer (MCP tools) or an agent flow needs a
stand-in for each of the five real hospitality vendor systems (amenities, casino, dining,
front-desk/PMS, ticketing) that behaves like a real vendor would: for the four systems with
reservable inventory, it holds state across calls and requires a hold before a booking is
final; for casino (read-only by design), it serves consistent informational and loyalty
data. Every simulator can be pointed at either tenant.

**Why this priority**: Every later phase (MCP layer, agents, retrieval-adjacent flows, eval)
depends on having something to call. Without working simulators, no downstream track can
build or test anything end-to-end.

**Independent Test**: Can be fully tested by placing a hold on a resource in each of the five
simulators, confirming it, and verifying the resource shows as booked — delivers a working
integration target on its own, independent of any other capability in this spec.

**Acceptance Scenarios**:

1. **Given** a simulator with available inventory for a tenant, **When** a caller requests a
   hold on a specific resource, **Then** the resource is marked held and unavailable to other
   callers for that tenant.
2. **Given** an active hold, **When** the caller confirms it, **Then** the resource becomes a
   finalized booking and is no longer releasable as a hold.
3. **Given** a confirmed booking, **When** any caller queries current availability, **Then**
   the previously held resource does not appear as available.

---

### User Story 2 - Exercise failure handling and compensation logic on demand (Priority: P1)

An engineer building error-handling, retry, or saga/compensation logic needs to reliably
force a specific vendor failure (a timeout, a server error, a partial failure, or degraded
latency) at a chosen point in a test, and get that exact failure every time the test runs —
not an occasional, unreproducible flake.

**Why this priority**: Compensation and error-taxonomy normalization logic cannot be
verified against failures that only happen by chance. This is equally foundational to Story
1 — without it, whole categories of downstream logic are untestable.

**Independent Test**: Can be fully tested by sending a request carrying a fault-injection
signal for a named failure mode and confirming that exact failure occurs, and that an
otherwise-identical request without the signal succeeds normally.

**Acceptance Scenarios**:

1. **Given** a request carrying a signal for a specific failure mode, **When** the simulator
   receives it, **Then** it returns that exact failure mode instead of processing the request
   normally.
2. **Given** an otherwise identical request with no fault-injection signal, **When** the
   simulator receives it, **Then** it processes the request normally with no injected
   failure.
3. **Given** a test that injects the same failure mode repeatedly, **When** it runs multiple
   times, **Then** the same failure mode occurs every time (the behavior is deterministic,
   not probabilistic).

---

### User Story 3 - Retry safely without double-booking or double-releasing (Priority: P1)

A caller (an agent, an orchestration layer, or a test) that does not know whether its last
confirm or release request actually reached the simulator needs to be able to retry that
exact request and get the same outcome as if it had only been sent once.

**Why this priority**: Two-phase mutation only works as a safety mechanism if retries are
safe. This is equally foundational — an unsafe retry defeats the purpose of holds.

**Independent Test**: Can be fully tested by sending the same confirm (or release) request
twice with the same idempotency key and verifying only one booking exists (or only one
release occurred), with no error caused by the repeat.

**Acceptance Scenarios**:

1. **Given** a hold and a confirm request with a specific idempotency key, **When** the exact
   same confirm request is sent again with that key, **Then** exactly one booking exists and
   no error indicating a duplicate booking occurs.
2. **Given** a hold that has already been released with a specific idempotency key, **When**
   the exact same release request is sent again with that key, **Then** the resource is not
   released a second time and no inconsistent state results.

---

### User Story 4 - Trust that expired holds clean up automatically (Priority: P2)

A caller places a hold, then never confirms or releases it (the flow was abandoned, the
caller crashed, or a human deliberately walked away). The resource must become available to
other guests again without anyone having to notice and manually intervene.

**Why this priority**: Self-healing hold expiry is what makes two-phase mutation safe under
real-world failure (crashed agents, abandoned sessions). It depends on holds and confirm
(Story 1) already existing, so it is scoped just behind the P1 stories.

**Independent Test**: Can be fully tested end-to-end, unattended, by placing a hold, waiting
past its time-to-live with no further action, and verifying the resource is available again
with no manual step.

**Acceptance Scenarios**:

1. **Given** an active hold that is never confirmed or released, **When** its time-to-live
   elapses, **Then** the resource is automatically released and becomes available again.
2. **Given** a hold that expired and released its resource, **When** a caller attempts to
   confirm that expired hold, **Then** the confirmation is rejected rather than silently
   succeeding.

---

### User Story 5 - Receive asynchronous fulfillment for ticket bookings (Priority: P2)

A caller confirms a ticketing hold and needs to know, separately from that confirmation, when
the ticket has actually been fulfilled — mirroring how a real ticketing vendor issues tickets
after acceptance rather than instantly.

**Why this priority**: This validates the platform's async/webhook handling path, which is
narrower in scope than the core hold lifecycle shared by all five simulators, so it is
scoped after the universal stories.

**Independent Test**: Can be fully tested by confirming a ticketing hold and verifying a
fulfillment notification arrives afterward via callback, separate from and after the
confirmation response.

**Acceptance Scenarios**:

1. **Given** a confirmed ticketing hold, **When** fulfillment completes, **Then** a
   notification of that fulfillment is delivered via callback rather than being included in
   the original confirmation response.
2. **Given** a confirmed ticketing hold, **When** the confirmation response is received,
   **Then** it does not itself claim the ticket is fulfilled.

---

### User Story 6 - Run realistic multi-tenant scenarios against seeded data (Priority: P3)

An engineer or automated test needs a populated environment — tenants, venues, events, rooms,
spa slots, and guest profiles — to run realistic scenarios against, and needs confidence that
one tenant's activity can never affect or expose another tenant's data.

**Why this priority**: Seed data and tenant isolation make prior stories realistic and
testable at scale, but the core hold/confirm/fault behaviors (P1) function without any
particular seed volume, so this is a supporting rather than foundational capability.

**Independent Test**: Can be fully tested by querying inventory and profile data for each
tenant and verifying the seeded counts, then attempting a cross-tenant read and verifying it
is refused or returns nothing.

**Acceptance Scenarios**:

1. **Given** the seeded environment, **When** each tenant's inventory is queried, **Then** the
   totals across both tenants equal 8 venues, 40 events, 200 rooms, 60 spa slots, and 50
   guest profiles, with MGV holding a greater share of each than MHB.
2. **Given** a caller scoped to one tenant, **When** it attempts to read or modify a resource
   belonging to the other tenant, **Then** the request is refused and no cross-tenant data is
   returned or altered.

---

### Edge Cases

- What happens when a confirm request arrives after its hold has already expired?
- What happens when a release request arrives for a hold that was already confirmed (not
  merely held)?
- What happens when two different idempotency keys are used for what is otherwise the same
  confirm request (same hold, same caller, different key each time)?
- What happens when a fault-injection signal names a failure mode a given simulator does not
  support?
- How does the system behave when a hold is requested for a resource that has zero remaining
  inventory?
- What happens if the asynchronous fulfillment notification for a ticket cannot be delivered
  on the first attempt?
- What happens when a request omits tenant scoping information entirely?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide five independently operable simulators — amenities,
  casino, dining, front-desk/PMS, and ticketing — each conforming to its own frozen,
  previously-agreed API contract.
- **FR-002**: Each of the four simulators with reservable inventory (amenities, dining, pms,
  ticketing) MUST support placing a time-limited hold on a reservable resource (a room, spa
  slot, table, or ticket) that reserves it without finalizing a booking. The casino simulator
  is read-only by design (informational and loyalty data only) and has no reservable
  inventory or hold lifecycle.
- **FR-003**: An active hold MUST automatically expire and release its resource back to
  availability if it is not confirmed within its time-to-live, with no further action
  required from any caller. Time-to-live is resource-specific, per the frozen API contracts:
  8 minutes default (10 minutes max) for ticket seat holds, 10 minutes default for spa/
  amenity and dining holds (dining capped at 15 minutes), and 15 minutes default for room
  holds. Ticket holds are intentionally shortest, since they are the scarcest and most
  perishable inventory and commit first in the booking sequence.
- **FR-004**: System MUST support confirming or releasing a hold using a caller-supplied
  idempotency key such that repeating the identical confirm or release request with the same
  key yields the same outcome as the first request — never a duplicate booking and never a
  duplicate release.
- **FR-005**: Each simulator MUST accept a fault-injection signal that deterministically
  produces a specific, named failure mode (at minimum: timeout, server error, partial
  failure, and added latency) on the request that carries it, and MUST behave normally on
  requests that do not carry it.
- **FR-006**: The ticketing simulator MUST fulfill confirmed ticket holds asynchronously,
  delivering fulfillment as a separate notification after confirmation rather than as part of
  the synchronous confirmation response, and MUST retry delivery a bounded number of times
  with backoff if the first delivery attempt fails, before giving up.
- **FR-007**: Each simulator MUST enforce its own authentication/authorization scheme,
  independent of and distinct from the schemes used by the other four simulators.
- **FR-008**: System MUST be pre-populated with seed data for exactly two tenants (Meridian
  Grand Las Vegas and Meridian Harbor Boston) before any scenario is run against it.
- **FR-009**: Seed data MUST include 8 venues, 40 events, 200 rooms, 60 spa slots, and 50
  guest profiles, distributed across the two tenants with MGV (Las Vegas, the larger flagship
  property) receiving a greater share of each count than MHB (Boston); the exact per-tenant
  ratio is an implementation-plan detail.
- **FR-010**: All data — seeded and created during use — MUST be scoped to its owning tenant;
  a request scoped to one tenant MUST NOT be able to read or modify another tenant's data.
- **FR-011**: System MUST provide an automated contract test suite, per simulator, that
  verifies the simulator's request/response behavior against its frozen API contract.
- **FR-012**: System MUST support running a scripted, unattended scenario that places a hold,
  allows it to expire without confirmation, and verifies the underlying resource was released
  — with no manual step required.
- **FR-013**: Every fault mode a simulator supports MUST be independently and repeatedly
  triggerable on demand via the fault-injection signal, producing the same failure behavior
  each time it is triggered.
- **FR-014**: Each simulator MUST provide a way to reset its state back to the seeded
  baseline on demand, without requiring a process or container restart.

### Key Entities

- **Hold**: A time-limited claim on a reservable resource. Carries an identifier, the
  resource it targets, its owning tenant, its time-to-live/expiry, and its current status
  (active, confirmed, released, expired).
- **Reservable Resource**: A room, spa slot, table, or ticket that can be held, confirmed
  into a booking, or remain available. Belongs to exactly one tenant.
- **Tenant**: One of the two resort properties (Meridian Grand Las Vegas / Meridian Harbor
  Boston). Every hold, resource, and guest profile belongs to exactly one tenant.
- **Guest Profile**: A seeded guest record used as the subject of scenarios; scoped to one
  tenant.
- **Fault Injection Signal**: A named failure mode a caller can request on a specific
  request, causing that request to fail (or degrade) in a specific, reproducible way instead
  of being processed normally.
- **Fulfillment Notification**: An asynchronous callback delivered after a ticketing hold is
  confirmed, indicating the ticket has actually been issued.
- **Idempotency Record**: The stored record of a mutating request's idempotency key, the
  request it was first used for, and the response returned — consulted on every repeat of
  that key so a retry replays the original outcome instead of re-executing.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of the automated contract tests pass for all five simulators, with zero
  contract violations, before this phase is considered complete.
- **SC-002**: In the unattended scripted hold→expire→release scenario, the held resource is
  confirmed available again within one hold time-to-live window of its resource type (per
  FR-003) after expiry, in 100% of runs, with zero manual intervention.
- **SC-003**: Repeating a confirm or release request with an unchanged idempotency key never
  produces a duplicate booking or a duplicate release, across 100% of tested retry
  scenarios (at least single-retry and multi-retry cases for both confirm and release).
- **SC-004**: Every documented fault mode is independently reproducible on demand, occurring
  correctly in 100% of attempts that request it, and never occurring on requests that do not
  request it.
- **SC-005**: Zero instances of cross-tenant data exposure or modification are observed
  across a full isolation test pass covering both tenants and all five simulators.
- **SC-006**: A fulfillment notification is delivered for 100% of confirmed ticketing holds
  in test runs, and is always observed after — never as part of — the confirmation response.
- **SC-007**: The seeded environment reflects the exact target volumes (2 tenants, 8 venues,
  40 events, 200 rooms, 60 spa slots, 50 guest profiles) on 100% of environment
  initializations.
- **SC-008**: Each simulator can be reset to its seeded baseline on demand, with no leftover
  holds, bookings, or fault-injection state from prior use, in 100% of tested resets — without
  requiring a process or container restart.
- **SC-009**: Every simulator rejects a request carrying no credentials and a request
  carrying another simulator's credentials, and accepts only its own authentication scheme,
  across 100% of tested credential combinations.

## Assumptions

- Each simulator supports one active fault-injection signal per request; combining multiple
  simultaneous fault modes on a single request is out of scope for this phase.
- Seed data volumes (8 venues, 40 events, 200 rooms, 60 spa slots, 50 guest profiles) are
  totals distributed across both tenants, not per-tenant counts, consistent with the project
  standard seeding target referenced in the project's operational documentation. Per
  Clarifications, the split is weighted toward MGV rather than even; the exact ratio is set
  during planning.
- The five simulators are the only external systems in scope for this phase; consumers of the
  simulators (MCP tools, agents) are out of scope and belong to later phases.
- "Distinct, independent" authentication per simulator means each simulator's
  auth/authorization scheme is defined independently of the others (may differ in mechanism,
  credential shape, or token format) — it does not mandate any particular set of five
  schemes.
- Bounded retry count and backoff strategy for fulfillment notification delivery are
  implementation-plan details; this spec requires only that retries happen (per
  Clarifications) and are eventually exhausted rather than retried forever.
