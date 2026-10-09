---

description: "Task list for feature implementation"
---

# Tasks: Simulation Ecosystem

**Input**: Design documents from `/specs/002-simulation-ecosystem/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md
(all present)

**Tests**: Included. The working agreement ("Tests before the next phase starts. Especially
the saga permutations.") and the constitution's "Test before advance" standard, which names
saga failure permutations and tenant isolation as mandatory test categories, both make tests
non-optional for this feature — not merely offered per the template's default.

**Organization**: Tasks are grouped by user story (spec.md) to enable independent
implementation and testing of each story, except that User Stories 1–3 (all P1) are
explicitly sequential per spec.md's own reasoning: "none is a meaningful MVP without the
other two" — a hold that cannot fail on demand (US2) or cannot be retried safely (US3) is not
yet trustworthy infrastructure. That dependency is called out below rather than assumed away.

## Format: `[ID] [P?] [Story] [TIER] [CRIT?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1–US6)
- **[TIER]**: Build-time model tier — `FRONTIER` / `MID` / `ECONOMY`. See `docs/models.md`.
- **[CRIT]**: Present when the task falls in the never-economy table in `docs/models.md`.

## Model tier assignment *(mandatory — ResortFlow project override)*

No task in this feature carries `[CRIT]` — `docs/models.md`'s never-economy table lists no
Phase 1 item, and explicitly calls Phase 1 out as the project's one genuinely large economy
block ("five services generated from frozen specs... tier aggressively there"). Sim-side
idempotency handling (honoring a client-supplied key, deduping a replay) is tiered `MID`
here, not `FRONTIER`: the `[CRIT]` "idempotency key derivation" entry in `docs/models.md` is
scoped to phases 0 and 2 — the *client-side derivation formula* `(session_id, hold_id,
attempt)`, which is feature 002's (MCP layer's) responsibility, not this feature's. Honoring
an already-derived key is a fully-specified mechanical behavior per the frozen contracts and
`mcp-tool-contract.md`, which is why it doesn't need frontier judgment — but it's subtler
than pure boilerplate (fingerprint mismatch detection, replay-vs-retry distinction), so it's
bumped to `MID` rather than `ECONOMY`. Every `FRONTIER`-tier item in this feature is an ADR
(already drafted; see `plan.md`), consistent with `docs/models.md`'s "all ADRs" rule.

## Path Conventions

Single project. Runtime code under `sims/` (Track A ownership per `AGENTS.md`); tests under
`sims/tests/` (kept inside Track A's own directory rather than the shared top-level `tests/`
feature 001 already populated — see `plan.md`'s Structure Decision).

---

## Phase 1: Setup

**Purpose**: Project initialization and basic structure

- [X] T001 [ECONOMY] Create the `sims/` directory tree per `plan.md`'s Project Structure:
  `sims/_shared/`, `sims/amenities/`, `sims/casino/`, `sims/dining/`, `sims/pms/`,
  `sims/ticketing/` (each with a `data/` subfolder, gitignored), and
  `sims/tests/{contract,integration,fixtures}/`
- [X] T002 [P] [ECONOMY] Add sim dependencies (`fastapi`, `uvicorn`, `pydantic>=2`, `httpx`,
  `aiosqlite`, `schemathesis`, `pytest`, `pytest-asyncio`) to the repository's `pyproject.toml`
  (extends feature 001's existing file; do not create a second one)
- [X] T003 [P] [ECONOMY] Extend the existing `ruff`/`mypy` configuration (feature 001) to
  cover `sims/`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared infrastructure every sim and every later user story depends on

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T004 [MID] Implement tenant validation/scoping in `sims/_shared/tenancy.py`: a FastAPI
  dependency that validates `X-Tenant-Id` against `{MGV, MHB}` (400 on anything else, per
  spec.md edge case "request omits tenant scoping information entirely") and a per-tenant
  SQLite scoping helper, per `data-model.md`
- [X] T005 [P] [MID] Implement the generic Hold state machine in `sims/_shared/holds.py`:
  `active → confirmed | released | expired` transitions, resource-specific TTL config keyed
  by sim (values from `data-model.md`'s TTL table), and an injectable clock hook (no reaper
  yet — that's US4, T029). Once a hold reaches `confirmed`, `released`, or `expired`, every
  further transition attempt (a differently-keyed confirm, a release of a confirmed hold,
  etc.) MUST be refused rather than silently accepted — per `data-model.md`'s state-machine
  section, this is the mechanism that protects against double-booking, not the idempotency
  store
- [X] T006 [P] [MID] Implement the idempotency store in `sims/_shared/idempotency.py`:
  key → response replay, and request-fingerprint mismatch detection (spec.md edge case: same
  key reused for a genuinely different request), per `data-model.md`'s Idempotency Record
- [X] T007 [P] [ECONOMY] Implement the reset endpoint in `sims/_shared/reset.py`
  (`POST /__sim__/reset`: delete that sim's SQLite file, rerun its `seed.py`, return
  `{"reset": true, "tenant_ids": ["MGV", "MHB"]}`) per
  `contracts/sim-control-plane.md`
- [X] T008 [ECONOMY] Create the shared FastAPI app factory, SQLite bootstrap, and structured
  logging helper in `sims/_shared/app.py`: per-sim DB path config; mounts the tenancy
  dependency (T004) and the reset route (T007); a `log_event(kind, **ids)` helper that logs
  identifiers and event types only (fault triggered, idempotency replay, hold expired) and
  never full request/response bodies, so guest contact fields and `DietaryRequirement` free
  text can never reach a log line (per `plan.md`'s logging-discipline constraint)

**Checkpoint**: Foundation ready — user story implementation can now begin

---

## Phase 3: User Story 1 - Build and test integrations against realistic vendor behavior (Priority: P1) 🎯 MVP

**Goal**: All five sims exist and conform to their frozen OpenAPI contracts; the four
holding sims support place-hold → confirm → resource-shows-as-booked; each sim enforces its
own distinct authentication scheme.

**Independent Test**: Place a hold on a resource in each of the five simulators, confirm it,
verify the resource shows as booked (per spec.md).

### Tests for User Story 1

> Write these first; they fail until the implementation tasks below are done.

- [X] T009 [P] [US1] [MID] Contract-conformance suite for amenities-sim (`schemathesis`) in
  `sims/tests/contract/test_amenities_contract.py` against
  `docs/contracts/openapi/amenities-sim.openapi.yaml`
- [X] T010 [P] [US1] [MID] Contract-conformance suite for casino-sim in
  `sims/tests/contract/test_casino_contract.py` against
  `docs/contracts/openapi/casino-sim.openapi.yaml`
- [X] T011 [P] [US1] [MID] Contract-conformance suite for dining-sim in
  `sims/tests/contract/test_dining_contract.py` against
  `docs/contracts/openapi/dining-sim.openapi.yaml`
- [X] T012 [P] [US1] [MID] Contract-conformance suite for pms-sim in
  `sims/tests/contract/test_pms_contract.py` against
  `docs/contracts/openapi/pms-sim.openapi.yaml`
- [X] T013 [P] [US1] [MID] Contract-conformance suite for ticketing-sim in
  `sims/tests/contract/test_ticketing_contract.py` against
  `docs/contracts/openapi/ticketing-sim.openapi.yaml`

### Implementation for User Story 1

- [X] T014 [P] [US1] [ECONOMY] Implement amenities-sim (`/services`,
  `/services/{id}/availability`, `/holds`, `/holds/{id}`, `/bookings`,
  `/bookings/{id}`, HTTP Basic auth) in `sims/amenities/main.py`, using
  `sims/_shared/{app,tenancy,holds,idempotency}.py`, per `amenities-sim.openapi.yaml`
- [X] T015 [P] [US1] [ECONOMY] Implement casino-sim (`/responsible-gaming/status`,
  `/loyalty/{id}`, `/loyalty/{id}/offers`, `/floor/games`, `/tournaments` — all read-only, no
  `holds` import; HTTP Bearer auth) in `sims/casino/main.py`, using
  `sims/_shared/{app,tenancy}.py`, per `casino-sim.openapi.yaml`
- [X] T016 [P] [US1] [ECONOMY] Implement dining-sim (`/venues`,
  `/venues/{id}/availability`, `/holds`, `/holds/{id}`, `/reservations`,
  `/reservations/{id}`, `/waitlist`, `/venue-events`, static `X-Dining-Key` auth) in
  `sims/dining/main.py`, using `sims/_shared/{app,tenancy,holds,idempotency}.py`, per
  `dining-sim.openapi.yaml`
- [X] T017 [P] [US1] [ECONOMY] Implement pms-sim (`/availability/rooms`, `/holds/rooms`,
  `/holds/rooms/{id}`, `/reservations`, `/stays/{id}`, `/stays/{id}/checkout-time`,
  `/stays/{id}/upgrade-offers`, `/stays/{id}/service-requests`, `/stays/{id}/folio`, OAuth2
  client-credentials auth with its own token endpoint) in `sims/pms/main.py`, using
  `sims/_shared/{app,tenancy,holds,idempotency}.py`, per `pms-sim.openapi.yaml`
- [X] T018 [P] [US1] [ECONOMY] Implement ticketing-sim's synchronous surface (`/events`,
  `/events/{id}`, `/events/{id}/inventory`, `/reservations` [hold seats],
  `/reservations/{id}`, OAuth2 client-credentials + `X-TM-Signature` HMAC auth, rejecting an
  invalid signature with the contract's `SIGNATURE_INVALID` fault) in
  `sims/ticketing/main.py`, using `sims/_shared/{app,tenancy,holds,idempotency}.py`, per
  `ticketing-sim.openapi.yaml` (`/orders` purchase + webhook is US5, T034)
- [X] T019 [US1] [ECONOMY] Wire all five `main.py` apps to the shared app factory (T008) and
  add an empty seed-stub call for each (real seed data lands in US6) — depends on
  T014–T018
- [X] T020 [US1] [MID] Integration test: hold → confirm → availability-reflects-booking
  across the four holding sims, plus a casino read-only smoke check, and a
  zero-remaining-inventory hold request returning the contract's `409` (spec.md edge case),
  in `sims/tests/integration/test_hold_confirm_flow.py`
- [X] T021 [US1] [MID] Integration test: each sim rejects a request with no credentials and
  a request carrying one of the *other* four sims' credentials, accepting only its own
  scheme (SC-009) — amenities (Basic), casino (Bearer), dining (`X-Dining-Key`), pms
  (OAuth2), ticketing (OAuth2 + HMAC, including a deliberately invalid `X-TM-Signature`) — in
  `sims/tests/integration/test_auth_schemes.py`

**Checkpoint**: All five sims exist and are contract-conformant, with distinct enforced
auth; basic hold/confirm works (no fault injection, no idempotent-replay guarantee, no
auto-expiry, no real seed data yet).

---

## Phase 4: User Story 2 - Exercise failure handling and compensation logic on demand (Priority: P1)

**Goal**: Every sim accepts a deterministic, on-demand fault signal.

**Independent Test**: Send a request with a fault-injection signal and confirm that exact
failure occurs; confirm an otherwise-identical request without it succeeds normally (per
spec.md). Depends on US1 (sims must exist first).

- [X] T022 [US2] [MID] Implement the fault-injection middleware in `sims/_shared/faults.py`:
  `X-Sim-Fault` (`timeout | error_500 | rate_limit | hold_expired | inventory_race |
  webhook_delay`) and `X-Sim-Latency` (`p50 | p95 | p99`) parsing and effects, `400` on an
  unrecognized or inapplicable value, per `contracts/sim-control-plane.md`
- [X] T023 [US2] [ECONOMY] Register the fault middleware (T022) on all five sims'
  FastAPI apps (`sims/amenities/main.py`, `sims/casino/main.py`, `sims/dining/main.py`,
  `sims/pms/main.py`, `sims/ticketing/main.py`) — depends on T022, T014–T018
- [X] T024 [P] [US2] [MID] Integration test: each applicable fault mode produces its exact
  documented failure deterministically across repeated attempts, an absent header behaves
  normally, and an inapplicable/unrecognized value returns `400`, in
  `sims/tests/integration/test_fault_injection.py`

**Checkpoint**: Fault injection works, deterministically, across all five sims.

---

## Phase 5: User Story 3 - Retry safely without double-booking or double-releasing (Priority: P1)

**Goal**: Repeating a confirm or release with the same idempotency key is always safe, and
every terminal-state hold refuses further transitions regardless of key.

**Independent Test**: Send the same confirm (or release) request twice with the same
`Idempotency-Key` and verify only one booking (or one release) results (per spec.md).
Depends on US1 (confirm/release endpoints must exist).

- [X] T025 [US3] [ECONOMY] Wire the idempotency store (T006) into each holding sim's
  confirm/release endpoints for fingerprint checking and cached-response replay
  (`sims/amenities/main.py`, `sims/dining/main.py`, `sims/pms/main.py`,
  `sims/ticketing/main.py`) — depends on T006, T014, T016–T018
- [X] T026 [P] [US3] [MID] Integration test: repeated confirm and repeated release with the
  same `Idempotency-Key` produce exactly one outcome each — covering both a single retry
  (one repeat) and a multi-retry case (three or more repeats), for both confirm and
  release, matching SC-003's literal bar — across all four holding sims, in
  `sims/tests/integration/test_idempotent_retry.py`
- [X] T027 [P] [US3] [MID] Integration test: the same `Idempotency-Key` reused for a
  genuinely different request body is detected and rejected rather than silently replayed
  (spec.md edge case) in `sims/tests/integration/test_idempotency_key_reuse.py`
- [X] T028 [P] [US3] [MID] Integration test: hold terminal-state transitions are refused
  regardless of idempotency key — a confirm carrying a *different* key than the original,
  successful confirm does not create a second booking (the hold is already `confirmed`,
  terminal), and a release against an already-`confirmed` hold is refused with the
  contract's appropriate conflict response (both spec.md edge cases, per
  `data-model.md`'s state-machine notes) — in
  `sims/tests/integration/test_hold_state_machine.py`

**Checkpoint**: Retries are safe, and terminal states are genuinely terminal, across all
four holding sims.

---

## Phase 6: User Story 4 - Trust that expired holds clean up automatically (Priority: P2)

**Goal**: An unconfirmed hold expires and releases its resource with no manual action.

**Independent Test**: Place a hold, let it pass its TTL untouched, verify the resource is
available again with no manual step (per spec.md). Depends on US1 (Hold state machine and
endpoints from T005/T014–T018).

- [X] T029 [US4] [MID] Add a background expiry reaper to `sims/_shared/holds.py` (extends
  T005): sweeps `active` holds past `expires_at` to `expired` and releases the resource,
  running against the injectable clock from T005 so tests can compress real 8–15 minute TTLs
  (per `research.md` item 4)
- [X] T030 [US4] [ECONOMY] Start the reaper (T029) from the shared app factory
  (`sims/_shared/app.py`) for the four holding sims
- [X] T031 [US4] [ECONOMY] Write the unattended hold-expiry scenario script
  `sims/tests/integration/hold_expiry_scenario.py --sim <name> --tenant <id>` per
  `quickstart.md` step 4 — exits non-zero on failure, suitable for CI
- [X] T032 [P] [US4] [MID] Integration test: a confirm attempt against an already-expired
  hold returns `410` rather than silently succeeding (spec.md acceptance scenario), in
  `sims/tests/integration/test_hold_expiry.py`

**Checkpoint**: FR-003/FR-012/SC-002 are provable, unattended, on compressed time.

---

## Phase 7: User Story 5 - Receive asynchronous fulfillment for ticket bookings (Priority: P2)

**Goal**: Ticket purchase confirms via `202` now, fulfillment notification arrives later via
webhook, retried with backoff if delivery fails.

**Independent Test**: Confirm a ticketing hold, verify a fulfillment notification arrives
afterward via callback, separate from the confirmation response (per spec.md). Depends on
US1 (ticketing-sim's synchronous surface, T018).

- [X] T033 [US5] [MID] Implement the webhook test harness in
  `sims/_shared/webhook_harness.py`: `POST /__sim__/webhook-sink` (records any payload,
  `204`) and `GET /__sim__/webhook-sink/{order_id}` (returns recorded callbacks or `404`)
  per `contracts/sim-control-plane.md`
- [X] T034 [US5] [ECONOMY] Implement ticketing-sim's async purchase path
  (`POST /orders` → `202 PENDING`; `GET /orders/{id}` poll fallback;
  `POST /webhooks/registrations`; delivery to the registered `callback_url` with bounded
  retry-with-backoff on failure, per spec.md Clarifications) in `sims/ticketing/main.py` —
  depends on T018, T033
- [X] T035 [P] [US5] [MID] Integration test: purchase response never claims fulfillment,
  the fulfillment callback arrives afterward via the webhook sink, and
  `X-Sim-Fault: webhook_delay` demonstrably triggers the bounded retry-with-backoff path, in
  `sims/tests/integration/test_ticketing_webhook.py`

**Checkpoint**: FR-006/SC-006 are provable within this feature, independent of feature 002.

---

## Phase 8: User Story 6 - Run realistic multi-tenant scenarios against seeded data (Priority: P3)

**Goal**: The environment is seeded to the exact volumes in `contracts/seed-data-spec.md`,
weighted toward MGV, with zero cross-tenant leakage and a working reset-to-baseline.

**Independent Test**: Query inventory/profile data per tenant and verify seeded counts; then
attempt a cross-tenant read and verify it is refused (per spec.md). Depends on US1 (sims and
endpoints must exist to seed and query against).

- [X] T036 [P] [US6] [ECONOMY] Implement amenities seed data (36 MGV / 24 MHB spa/amenity
  slots, multiple `practitioner_id`s per service) in `sims/amenities/seed.py` per
  `contracts/seed-data-spec.md`
- [X] T037 [P] [US6] [ECONOMY] Implement casino seed data (loyalty + responsible-gaming
  status records for all 50 guest profiles, ≥1 `self_excluded: true` per tenant) in
  `sims/casino/seed.py` per `contracts/seed-data-spec.md`
- [X] T038 [P] [US6] [ECONOMY] Implement dining seed data (5 MGV / 3 MHB venues, every
  `DietaryRestriction` enum value supported by ≥1 venue per tenant) in
  `sims/dining/seed.py` per `contracts/seed-data-spec.md`
- [X] T039 [P] [US6] [ECONOMY] Implement pms seed data (120 MGV / 80 MHB room offers across
  all four `room_class` values, weighted toward `STANDARD`/`DELUXE`) in `sims/pms/seed.py`
  per `contracts/seed-data-spec.md`
- [X] T040 [P] [US6] [ECONOMY] Implement ticketing seed data (24 MGV / 16 MHB events, every
  `classification` value covered per tenant, ≥1 `sold_out` and ≥1 `cancelled` event per
  tenant) in `sims/ticketing/seed.py` per `contracts/seed-data-spec.md`
- [X] T041 [US6] [ECONOMY] Implement the shared 50-guest-profile seed (30 MGV / 20 MHB,
  deterministic fixed random seed) referenced by the five sims' seed scripts — depends on
  T036–T040
- [X] T042 [US6] [ECONOMY] Wire the `make seed` Makefile target to invoke all five
  `seed.py` scripts (flagged as an integration-commit concern in `plan.md`'s Structure
  Decision, not claimed solely by this feature)
- [X] T043 [P] [US6] [MID] Integration test: seeded counts match
  `contracts/seed-data-spec.md` exactly per tenant, across all five sims, in
  `sims/tests/integration/test_seed_data.py`
- [X] T044 [P] [US6] [MID] Integration test (mandatory saga/tenant-isolation category):
  cross-tenant read/hold/confirm attempts are refused with no data leakage, across all five
  sims, in `sims/tests/integration/test_tenant_isolation.py`
- [X] T045 [US6] [MID] Integration test: reset-to-baseline restores exact seeded counts and
  clears all holds/bookings/idempotency records from prior use, across all five sims
  (FR-014/SC-008), in `sims/tests/integration/test_reset.py`

**Checkpoint**: All six user stories are independently functional; the environment matches
`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md`'s Phase 1 exit criteria.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T046 [P] [ECONOMY] Add each sim's `Dockerfile` (`sims/amenities/Dockerfile`,
  `sims/casino/Dockerfile`, `sims/dining/Dockerfile`, `sims/pms/Dockerfile`,
  `sims/ticketing/Dockerfile`) — wiring them into the shared `docker-compose.yml` is flagged
  as an integration-commit concern per `plan.md`'s Structure Decision, not done here
- [X] T047 [ECONOMY] Run `quickstart.md` end-to-end against the fully implemented feature and
  fix any discrepancies found between the guide and actual behavior
- [X] T048 [P] [ECONOMY] Add module-level docstrings to `sims/_shared/*.py` cross-referencing
  `contracts/sim-control-plane.md` for the fault/reset/webhook-harness contract

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories
- **US1 (Phase 3)**: Depends on Foundational — BLOCKS US2, US3, US4, US5, US6 (every later
  story either edits the sims US1 creates or tests behavior that requires them to exist)
- **US2 (Phase 4)** and **US3 (Phase 5)**: Both depend on US1. Both touch the same four
  holding sims' `main.py` files (fault middleware registration vs. idempotency wiring) — safe
  to implement in either order or by different people, but not truly file-parallel; sequence
  them or coordinate merges rather than treating as `[P]` at the phase level.
- **US4 (Phase 6)**: Depends on US1 (T005/T014–T018); independent of US2/US3
- **US5 (Phase 7)**: Depends on US1 (T018); independent of US2/US3/US4
- **US6 (Phase 8)**: Depends on US1 (sims must exist to seed/query); independent of
  US2–US5, though its tenant-isolation test (T044) is more informative once fault injection
  and idempotency exist, it does not require them
- **Polish (Phase 9)**: Depends on all desired user stories being complete

### Within Each User Story

- Tests are written first and fail before the corresponding implementation task
- Shared/foundational pieces before per-sim wiring
- Story complete before moving to the next priority tier (P1 stories before P2, P2 before P3)

### Parallel Opportunities

- T002, T003 (Setup) in parallel
- T005, T006, T007 (Foundational) in parallel — different files, only T004/T008 are
  sequential anchors
- T009–T013 (US1 contract tests) in parallel; T014–T018 (US1 per-sim implementations) in
  parallel — five independent files
- T024 (US2), T026/T027/T028 (US3), T032 (US4), T035 (US5), T043/T044 (US6) are each
  independently parallelizable within their own story
- T036–T040 (US6 per-sim seed data) in parallel

---

## Parallel Example: User Story 1

```bash
# Contract tests, in parallel:
Task: "Contract-conformance suite for amenities-sim in sims/tests/contract/test_amenities_contract.py"
Task: "Contract-conformance suite for dining-sim in sims/tests/contract/test_dining_contract.py"
Task: "Contract-conformance suite for pms-sim in sims/tests/contract/test_pms_contract.py"
Task: "Contract-conformance suite for ticketing-sim in sims/tests/contract/test_ticketing_contract.py"
Task: "Contract-conformance suite for casino-sim in sims/tests/contract/test_casino_contract.py"

# Per-sim implementations, in parallel:
Task: "Implement amenities-sim in sims/amenities/main.py"
Task: "Implement dining-sim in sims/dining/main.py"
Task: "Implement pms-sim in sims/pms/main.py"
Task: "Implement ticketing-sim's synchronous surface in sims/ticketing/main.py"
Task: "Implement casino-sim in sims/casino/main.py"
```

---

## Implementation Strategy

### MVP First (User Stories 1–3 together)

Per spec.md, US1–US3 share P1 because none is a meaningful MVP alone. Complete Setup →
Foundational → US1 → US2 → US3, then stop and validate: contract-conformant sims, with
distinct enforced auth, deterministic fault injection, and safe retries, is the smallest
genuinely useful increment.

### Incremental Delivery

1. Setup + Foundational → foundation ready
2. US1 + US2 + US3 → the P1 MVP (sims exist and enforce distinct auth, faults are
   deterministic, retries and terminal states are safe)
3. US4 → auto-expiry provable unattended
4. US5 → async ticketing fulfillment provable
5. US6 → full seeded, tenant-isolated, resettable environment — Phase 1 exit criteria met
6. Polish

---

## Model tier summary *(mandatory — ResortFlow project override)*

| Tier | Tasks | `[CRIT]` | Notes |
|---|---|---|---|
| FRONTIER | — | — | No task in this feature required frontier judgment; the three obligated ADRs (007–009) were drafted directly in `plan.md`'s planning phase, not as a `tasks.md` row. |
| MID | T004, T005, T006, T009–T013, T020, T021, T022, T024, T026, T027, T028, T029, T032, T033, T035, T043, T044, T045 | none | Shared-library logic (tenancy, holds, idempotency, fault middleware, webhook harness) and all test-design tasks — genuine judgment, but errors surface fast (a wrong test fails loudly; a wrong dedup rule is caught by T026–T028). 22 tasks. |
| ECONOMY | T001, T002, T003, T007, T008, T014–T019, T023, T025, T030, T031, T034, T036–T042, T046, T047, T048 | none | Sim implementations generated directly from frozen OpenAPI specs, seed data, config/scaffolding, and Dockerfiles — exactly the block `docs/models.md` names as Phase 1's large economy opportunity. 26 tasks. |

22 + 26 = 48 tasks total, matching T001–T048 with no gaps.

### Verification

Run against `/speckit-analyze`:

```
Flag any task marked ECONOMY whose acceptance criteria require a judgment
not fully specified in plan.md. Flag any [CRIT] task not on FRONTIER.
```

---

## Phase 10: Convergence

- [X] T049 [HIGH] Write the Phase 1 handoff per Constitution: Development Workflow
  & Quality Gates — Phase handoff (missing): what was built (five sims + `sims/_shared`
  library, 53 passing tests), deviations from spec (the disclosed items in the
  `/speckit-implement` completion report — idempotency wired during US1 not deferred to
  US3, three schemathesis checks excluded per `sims/tests/fixtures/contract_helpers.py`,
  undeclared-status mapping, mypy `disallow_any_generics` relaxation for `sims/*`),
  interfaces published (`contracts/sim-control-plane.md`, `contracts/seed-data-spec.md`,
  the five sims' live OpenAPI surfaces), and open questions resolved (the `docker-compose.yml`
  build-context/directory-naming mismatch found and fixed post-implementation)
- [X] T050 [HIGH] Review the out-of-scope `FOUNDRY_PROJECT_ENDPOINT` /
  `FOUNDRY_*_DEPLOYMENT_NAME` lines added to `.env.example` (unrequested): confirm whether
  the endpoint hostname is a real, non-placeholder value that should not be committed, and
  either revert the addition (it is unrelated to this feature) or, if intentional, move it
  to whichever feature/commit actually owns it
