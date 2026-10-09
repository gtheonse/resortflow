# Implementation Plan: Simulation Ecosystem

**Branch**: `002-simulation-ecosystem` | **Date**: 2026-09-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-simulation-ecosystem/spec.md`

## Summary

Build five independent FastAPI services that stand in for the five real hospitality vendors
(amenities, casino, dining, PMS, ticketing) ResortFlow will eventually integrate through MCP. Four
of the five (amenities, dining, pms, ticketing) implement the hold → confirm two-phase
mutation lifecycle from `docs/adr/006-hold-then-confirm-saga.md`, with resource-specific TTL
expiry and idempotency-key-honoring confirm/release, exactly as already specified in the
frozen `docs/contracts/openapi/*.yaml`. Casino is read-only by contract — no mutating
endpoints exist for it, which is the constitution's gaming-read-only principle expressed
structurally rather than by convention. Every sim carries its own genuinely distinct auth
scheme (Basic, static API key, OAuth2 client-credentials, OAuth2 + HMAC signature, Bearer)
and its own error envelope shape, because the whole point of this phase is a hostile,
inconsistent integration surface for the MCP layer (feature 003) to normalize — a demo where
all five vendors look alike would prove nothing.

The technical core is three pieces of infrastructure shared by all five sims but owned
entirely within `sims/`: a TTL-driven hold engine (place → expire-or-confirm → release,
resource-specific durations already fixed by the frozen contracts), a fault-injection
middleware keyed on `X-Sim-Fault`/`X-Sim-Latency` headers that deterministically forces one
named failure per request, and an idempotency store that makes confirm/release calls safe to
retry. Ticketing additionally needs an async purchase path (`202` now, webhook later) and,
since feature 002 (MCP layer) does not exist yet, Phase 1 ships its own lightweight webhook
receiver so the async path is independently testable now rather than deferred.

This feature ships zero LLM nodes, zero MCP tools, and zero graph nodes. It is five
governed-by-contract test doubles plus the shared harness that makes them stateful,
fault-injectable, and idempotent.

## Technical Context

**Language/Version**: Python 3.11+ (matches feature 001's tech stack)

**Primary Dependencies**: `fastapi`, `uvicorn`, `pydantic>=2`, `httpx` (webhook delivery and
test client), `aiosqlite` (per-sim state), `apscheduler` or a plain `asyncio` background task
(hold-expiry reaper — see research.md), `schemathesis` (contract-conformance testing driven
directly from each frozen OpenAPI file), `pytest`, `pytest-asyncio`

**Storage**: One SQLite file per simulator (`sims/<name>/data/state.db`), not the shared
Postgres instance. Postgres in this project is reserved for the checkpointer and the
ResortFlow-owned `hold_ledger` (feature 001, ADR-004) — vendor-side state belongs to the vendor
simulation, not to ResortFlow's own durability story, and keeping it file-local per sim is what
makes the reset-to-baseline requirement (FR-014) cheap and dependency-free.

**Testing**: `pytest` for unit/integration tests; `schemathesis` for automated
contract-conformance testing directly against each frozen `docs/contracts/openapi/*.yaml`
(FR-011); `pytest-asyncio` for hold-expiry timing tests using a test-only clock override
rather than real per-resource TTL sleeps (see research.md — real TTLs range 8–15 minutes,
too slow for CI as wall-clock waits).

**Target Platform**: Linux containers via Docker Compose locally (one container per sim,
matching `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §9/§10), Azure Container Apps behind APIM
in the production target — unchanged from feature 001's stated topology.

**Project Type**: Five independent backend services, each a small FastAPI app, plus one
internal shared library (fault injection, hold/TTL engine, idempotency store, reset
endpoint, seed loader) that all five import. Not a monolith, and not five copy-pasted
implementations of the same logic.

**Performance Goals**: Not a performance-sensitive phase — these are test doubles, not
production services. The one real constraint: the hold-expiry reaper's check interval must
be fine-grained enough (seconds, not minutes) that the unattended hold→expire→release test
(FR-012) doesn't itself become the slow part of CI once TTLs are compressed for testing.

**Constraints**:
- Every sim MUST implement exactly the paths, schemas, and status codes in its frozen
  OpenAPI file — no additions to the versioned contract surface. Fault-injection and reset
  are test/ops concerns and MUST live outside the contract-versioned paths (see
  research.md and ADR-008).
- Auth divergence across the five sims is a requirement, not an accident — it MUST be
  preserved even though a single shared auth check would be less code.
- `X-Tenant-Id` is a caller-supplied header at the sim boundary, per every frozen contract.
  Sims validate and scope by it; they do not know about token claims or MCP-layer injection
  — that enforcement belongs to feature 002 and is out of scope here.
- State MUST reset to the seeded baseline on demand (FR-014) without a process/container
  restart, and MUST NOT require any shared external dependency (no Postgres, no Redis) to do
  so, since that would make reset only as reliable as a service this phase doesn't own.
- Sim logs (fault triggered, idempotency replay, hold expired) MUST record identifiers and
  event types only, never full request/response bodies — guest contact fields and
  `DietaryRequirement` free-text (health data under constitution principle IV) MUST NOT
  reach a log line, even though seed data is synthetic. This is ordinary logging discipline,
  not Phase 6's Presidio redaction pulled forward.

**Scale/Scope**: 5 simulators, ~35 endpoints total across the frozen specs, seed data of 8
venues / 40 events / 200 rooms / 60 spa slots / 50 guest profiles split roughly 60/40 toward
MGV (research.md has the exact per-category counts), 4 sims with a hold lifecycle, 1
read-only sim, 1 async (webhook) fulfillment path.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` v1.1.0.

| Principle | Verdict | Evidence |
|---|---|---|
| **I. Ambiguity-Based Routing** | ✅ PASS | Zero LLM nodes. Every sim is deterministic FastAPI request handling against a frozen schema. |
| **II. Governed Integration Surface** | ✅ PASS | Casino-sim's contract has no mutating endpoints — the gaming-read-only rule is structural here, not a policy this feature has to add. `X-Tenant-Id` scoping (FR-010) is enforced sim-side per the frozen contracts; server-side injection from a token claim is feature 002's job and is explicitly out of scope (Technical Context, Constraints). |
| **III. Two-Phase Mutation** | ✅ PASS — *enabling work* | The four non-casino sims implement hold → confirm/release exactly per ADR-006, with contract-fixed, resource-specific TTLs (FR-003) and mandatory idempotency-key honoring (FR-004). This feature is what makes ADR-006 real rather than aspirational. |
| **IV. Policy Is Code, Not Prompt** | ✅ PASS | Dining and PMS contracts already carry structured `DietaryRequirement { restriction, severity }` — this feature preserves that shape exactly; it invents no new dietary representation. Casino's `/responsible-gaming/status` fields (`self_excluded`, `offers_permitted`, `mandated_message`) are served verbatim from seed data, unmodified by any sim logic, so Phase 6 has a reliable foundation to enforce against. |
| **V. Durable, Inspectable State** | ✅ PASS — *not applicable to the checkpointer/ledger split* | This feature's state is vendor-side simulation state, not ResortFlow conversation state or the write-ahead ledger (both feature 001, both Postgres). No conflation risk: sims don't read or write `hold_ledger` or checkpoints. Inspectability here means FR-014's on-demand reset and the contract test suite, not replay. |
| **VI. Declared Model Tier** | ✅ PASS | Per `docs/models.md`: "the sims (Phase 1) are the one genuinely large economy block... tier aggressively there." No Phase 1 item appears in the `[CRIT]` table. `tasks.md` tiers sim implementations and seed data as `ECONOMY`; contract-test-suite design and the fault-injection/hold-engine shared library (genuine judgment, errors surface fast) as `MID`. Runtime tier: not applicable — no LLM-invoking node exists in this feature. |

| Development Standard | Verdict | Evidence |
|---|---|---|
| Determinism first | ✅ PASS | No model call anywhere in this feature. Fault injection is itself deterministic per FR-013 — a design requirement, not just an implementation nicety. |
| Contract stability | ✅ PASS | The five OpenAPI files are frozen inputs (Track A owns `docs/contracts/openapi/` but this feature does not edit them — see the two corrections in `checklists/requirements.md` iteration 3, both resolved by conforming the spec to the contract, never the reverse). |
| Every decision documented | ✅ PASS | Three ADRs obligated — see the ADR table below. |
| Test before advance | ✅ PASS | Contract-conformance suite (FR-011) plus the unattended hold→expire→release scenario (FR-012) plus idempotent-retry and tenant-isolation test categories, matching this project's mandatory test categories. |
| Observability not optional | ✅ PASS | Three independent reasons this standard is satisfied, not deferred. (a) **Scope**: the MUST governs ResortFlow's *own* external calls and graph nodes. The sims are the callee being simulated — a vendor emitting its caller's `gen_ai.*`/`langfuse.*` vocabulary would be wrong, not compliant. Spans for calls *to* these sims are emitted by the MCP adapters that make them. (b) **Emission ownership**: `observability/attributes.py`, frozen by feature 001, states that "emission (the tracer, the exporter, the dual-backend callback handlers) is feature 007's responsibility" — no feature before 007 emits spans, and feature 001 passed this identical gate by freezing the attribute vocabulary rather than emitting. This feature is not an exception to the standard; it is on the same footing as every feature before 007. (c) **PII redaction**: the standard's redaction half is a Phase 6 deliverable (Presidio-based, `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §Phase 6), and no real guest data ever reaches a simulator — seed data is synthetic by contract (`contracts/seed-data-spec.md`). The logging-discipline constraint in Technical Context keeps sim logs free of guest contact fields and dietary free-text regardless. |
| No real client names | ✅ PASS | Meridian Resorts International, `MGV`, `MHB` only — matches the frozen contracts. |

**Gate result: PASS.** One recorded deviation, justified below rather than silently
accepted. **Re-checked after Phase 1 design** (research.md, data-model.md, contracts/,
quickstart.md): no new violations surfaced. The two spec corrections discovered during
research (per-resource TTL, casino's read-only scope — `checklists/requirements.md`
iteration 3) strengthened rather than weakened the II and III verdicts above, since both
corrections make the feature conform more closely to the frozen contracts and to ADR-006,
not less. A third correction, made during `/speckit-analyze` (checklists/requirements.md
iteration 4), reclassified the Observability standard from an asserted "deviation" to a
verified PASS — see that row above; it was a self-graded error in this plan, not an actual
gap, so it carries no ADR (the constitution's "any accepted deviation MUST be recorded as an
ADR" rule does not trigger for a deviation that does not exist).

### Recorded deviations

1. **Fault-injection and reset endpoints exist outside each sim's frozen OpenAPI surface.**
   Adding `X-Sim-Fault`/`X-Sim-Latency` as formal parameters or a `/reset` path to the frozen
   specs would mean editing a Phase 0 contract, which the working agreement forbids without
   raising it as a blocker. Instead they're implemented as middleware (headers, which every
   HTTP framework accepts unconditionally regardless of whether OpenAPI declares them) and an
   out-of-band path convention that a contract-conformance run never exercises. See ADR-008.

## Project Structure

### Documentation (this feature)

```text
specs/002-simulation-ecosystem/
├── plan.md              # This file
├── research.md          # Phase 0 output — decisions with alternatives
├── data-model.md         # Phase 1 output — entities + Hold state machine per sim
├── quickstart.md        # Phase 1 output — runnable validation guide
├── contracts/           # Phase 1 output — design-time interface specifications
│   ├── sim-control-plane.md   # Fault injection, reset, and webhook-harness contract
│   └── seed-data-spec.md      # Exact per-tenant seed counts and entity shapes
├── checklists/
│   └── requirements.md  # 16/16 passing
└── tasks.md              # Created by /speckit-tasks, not by this command
```

### Source Code (repository root)

```text
sims/                                # Track A — this feature's entire runtime surface
├── _shared/                         # Internal library imported by all five sims
│   ├── __init__.py
│   ├── faults.py                    # X-Sim-Fault / X-Sim-Latency middleware
│   ├── holds.py                     # Generic hold/TTL engine (place, expire, confirm, release)
│   ├── idempotency.py               # Idempotency-Key store, replay-safe confirm/release
│   ├── reset.py                     # Out-of-band reset-to-seeded-baseline endpoint
│   ├── tenancy.py                   # X-Tenant-Id validation/scoping shared by all sims
│   └── webhook_harness.py           # Lightweight receiver used to test async fulfillment
├── amenities/
│   ├── main.py                      # FastAPI app implementing amenities-sim.openapi.yaml
│   ├── seed.py
│   └── data/                        # SQLite file lives here at runtime (gitignored)
├── casino/
│   ├── main.py                      # Read-only; no holds import from _shared.holds
│   └── seed.py
├── dining/
│   ├── main.py
│   └── seed.py
├── pms/
│   ├── main.py
│   └── seed.py
├── ticketing/
│   ├── main.py                      # Only sim using _shared.webhook_harness for purchase
│   └── seed.py
└── tests/                           # Track A's own tests — kept inside sims/ (see Structure Decision)
    ├── contract/                    # schemathesis runs, one per sim, against the frozen specs
    ├── integration/                 # hold→expire→release, idempotent retry, tenant isolation
    └── fixtures/

docs/contracts/openapi/              # Frozen inputs — unchanged by this feature
docker-compose.yml                   # Cross-cutting — wiring five new services in is an
                                      #   integration commit, not owned solely by this feature
Makefile                             # `make seed` target invokes sims/*/seed.py — same note
docs/adr/                            # 007-009 added by this feature
```

**Structure Decision**: All runtime code lives under `sims/`, matching Track A's ownership
in `AGENTS.md`. Tests live at `sims/tests/`, not the shared top-level `tests/` directory that
feature 001 already populated for Track C — this keeps Track A file-disjoint per the working
agreement ("Tracks are file-disjoint... Cross-cutting changes go through a single integration
commit") rather than two tracks writing into the same top-level directory. `_shared/` exists
because the alternative — five independent implementations of hold/TTL/idempotency/fault
logic — would mean four rewrites of ADR-006's state machine and a near-certainty that they
drift from each other; a shared library used only within `sims/` doesn't create the
cross-track import problem that made feature 001 put `observability/` at the repository root.
`docker-compose.yml` and `Makefile` already exist from feature 001 as topology/target
placeholders ("sims and app behind profiles" / `make seed`); filling in the five real service
blocks and seed invocation is flagged as an integration-commit concern rather than claimed as
this feature's alone, since both files are shared, cross-track artifacts.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations. The Constitution Check gate passed, with one recorded deviation (sims do not
emit OTel spans) justified above rather than left silent. This section is intentionally
empty.

## Architecture Decision Records *(mandatory — ResortFlow project override)*

Applying the significance bar from the template: a decision qualifies if it constrains a
later feature, could reasonably have gone the other way, would be breaking to reverse, or
trades one project value against another.

### Plan completion gate

| ADR | Decision | Status |
|-----|----------|--------|
| [007](../../docs/adr/007-sim-state-sqlite.md) | Per-sim SQLite state over in-memory-only or shared Postgres | [x] drafted / [x] complete |
| [008](../../docs/adr/008-sim-test-controls-outside-contract.md) | Fault-injection and reset kept outside the frozen OpenAPI contract surface | [x] drafted / [x] complete |
| [009](../../docs/adr/009-phase1-webhook-test-harness.md) | Phase 1 ships its own webhook test harness rather than deferring async testing to feature 002 | [x] drafted / [x] complete |

Decisions made in this plan that did **not** meet the significance bar, and why:

- Five separate FastAPI processes/containers (not one multi-mount app) — already established
  in `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §9/§10 ("All five run in Docker locally...
  sit behind APIM"); this plan applies that decision, it doesn't make it.
- Sequential riskiest-first commit ordering, idempotency key derivation formula, and the
  hold-then-confirm pattern itself — all already decided in ADR-006 (feature 001). This
  feature implements them; it does not re-decide them.
- Choosing `schemathesis` for contract-conformance testing over hand-written per-endpoint
  assertions — conventional tooling choice for "test against an OpenAPI spec," reversible
  without touching any interface, constrains no downstream feature.
- Per-resource hold TTL values (480s/600s/900s) — not a decision this feature makes at all;
  they are read directly from the already-frozen contracts (see `checklists/requirements.md`
  iteration 3).
