---

description: "Task list for feature 003-mcp-tool-layer"
---

# Tasks: MCP Tool Layer

**Input**: Design documents from `/specs/003-mcp-tool-layer/`

**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/`, `quickstart.md`

**Tests**: Included. Required by the constitution's TEST BEFORE ADVANCE standard, and every
one of this feature's 16 success criteria is stated as a measurable test outcome. Tenant
isolation is a mandatory test category for this project.

**Organization**: Tasks are grouped by user story (US1–US8 from `spec.md`) so each story can
be implemented and verified independently.

## Format: `[ID] [P?] [Story] [TIER] [CRIT?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to
- **[TIER]**: Build-time model tier — `FRONTIER` / `MID` / `ECONOMY`. See `docs/models.md`.
- **[CRIT]**: Present when the task falls in the never-economy table in `docs/models.md`.

---

## Plan corrections folded into this task list

`/speckit-plan`'s output was reviewed before these tasks were generated. Seven findings change
what the work actually is; they are resolved here rather than left to be discovered during
implementation. Each carries the tasks that close it.

| # | Finding | Resolution in this list |
|---|---|---|
| F1 | A top-level package named `mcp/` collides fatally with the `mcp` SDK (PyPI `mcp`, imported as `mcp.server.fastmcp`) — they cannot coexist in one environment, and `fastmcp` doesn't dodge it since it imports `mcp` itself. | Track B keeps the `mcp/` **directory** (AGENTS.md/constitution/compose all unchanged); the importable package inside is `mcp/gateway/`. All paths below reflect this. **T001, T003** |
| F2 | `docker-compose.yml`'s `mcp-gateway` still has the Phase 0 placeholder `context: ./mcp` and no `dockerfile:` — that context excludes repo-root `observability/`, which ADR-016 requires. No `mcp/Dockerfile` was planned at all. | Dockerfile added; compose corrected to `context: .` + `dockerfile: mcp/Dockerfile`, matching the fix Phase 1 already had to make for the sims. **T005, T006** |
| F3 | FR-030 / SC-014 / the `completed_sub_calls` field have **no producer**: every composite in the roster is read-only, and a multi-*mutation* composite is inherently cross-domain, i.e. saga territory (Phase 4–5). | The envelope-building mechanism is implemented and unit-tested against a synthetic two-slot mutating composite, so the code is real and SC-014 is measurable; no contrived production tool is invented, and the gap is recorded honestly. **T024, T063, T070** |
| F4 | `contracts/tool-registry.md`'s entitlement totals are wrong (dining is 3/3/2 not 2/3/3; stay is 2/4/1 not 2/3/2; totals 13/11/7 not 12/10/9), and "10 scope strings declared" should be 13. | Corrected, and a test asserts the table matches the live registry so it can't drift again. **T007, T035** |
| F5 | No vendor-auth design existed for five deliberately divergent schemes — OAuth2 client-credentials with token caching/refresh (pms, ticketing), HMAC signing (ticketing), Basic, static key, Bearer — nor any rule for classifying *token-endpoint* failures. | `_shared/vendor_auth.py` added as foundational work, with token-endpoint failures classified explicitly. **T019, T020** |
| F6 | The webhook path was unresolved: `registerWebhook` needs `callback_url` + `secret`, so the gateway would need an ingress endpoint, inbound HMAC verification, and correlation state — contradicting the "no durable state" claim. | Poll-only this phase. `get_order_status` polls `GET /orders/{id}` (Phase 1's own documented fallback); no startup registration, no ingress, no correlation cache. Webhook consumption lands with the phase that owns durable state. **T041, T070** |
| F7 | `check_dining_status` / `get_order_status` were specified to dispatch on "identifier shape", but the frozen contracts type every id as a bare `string` with no pattern or format. | Both take an explicit discriminated `kind` argument. Legitimate here — both branches are the same `read` entitlement, so this is a typed union, not the risk-level mode flag the spec's Assumptions reject. **T031, T041** |

Three smaller accuracy fixes (plan.md's "zero cross-cutting files" claim, its self-contradiction
about sims being unchanged, and the imprecise "stateless" wording) are handled in **T066–T069**.

### Applied during `/speckit-analyze` remediation (2026-09-11)

A second review pass — `/speckit-analyze`, run after this list was generated — found that the
doc-correction tasks sat in Phase 11, *after* the phases that would have read the stale text.
They were therefore applied immediately rather than promoted, and are checked off above:
**T007, T066, T067, T068, T069**. The same pass applied the corresponding upstream edits that
were not covered by any task at all:

- `plan.md` — the `mcp/gateway/` layout and its rationale, the corrected source tree with
  `mcp/Dockerfile`, the real cross-cutting-file list, the `read`-only-vs-durable storage
  wording, the gaming import constraint, and three Constitution Check rows (C1, C2, H1, M2, M3).
- `research.md` D1 — the package-collision and build-context findings, recorded as blocking
  consequences of the SDK choice (C1).
- `spec.md` — FR-005 now carries an explicit carve-out for out-of-band test controls, which is
  what makes T071 lawful instead of a contradiction (C2); a new Assumption records that FR-030
  and SC-014 have no shipped producer this phase (H2).
- `contracts/tool-registry.md` — the `kind` discriminator on both status tools, poll-only
  fulfillment replacing the webhook-registration note, and the corrected totals (H4, F4).
- `quickstart.md` — the `gateway._shared.identity` module path and a pytest-first Scenario 1,
  since `make eval` is the CI gate and Node is not in the toolchain (M1).
- `docs/adr/015` — an honest qualification that the "we already know when it applies" rationale
  holds for `effect_status` but not for `completed_sub_calls` (H2).
- `data-model.md` — the garbled `hold_ledger` column reference (F13).

One new task was added: **T072**, the self-exclusion → `POLICY_BLOCKED` integration test. It
was missing entirely, despite being the most regulator-relevant path in the feature and despite
Phase 1 having seeded `self_excluded` guests specifically so it could be tested end-to-end.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Make the package importable, buildable, and runnable before any tool exists.

- [x] T001 [ECONOMY] Create the package skeleton under `mcp/gateway/` — `__init__.py`, `_shared/__init__.py`, and one directory per domain (`dining/`, `entertainment/`, `stay/`, `wellness/`, `gaming/`), plus `mcp/tests/{contract,integration,unit,fixtures}/`. `mcp/` itself stays a plain directory with **no** `__init__.py` (F1).
- [x] T002 [ECONOMY] Add runtime dependencies to `pyproject.toml`: `mcp`, `pyjwt`, `tenacity`, `opentelemetry-api`, `opentelemetry-sdk` (`httpx`, `pydantic` already present).
- [x] T003 [ECONOMY] Extend `pyproject.toml` packaging for the two-root layout: `[tool.setuptools.packages.find] where = [".", "mcp"]` with `include = [..., "gateway*"]`; `[tool.mypy] files = [..., "mcp/gateway"]` plus `mypy_path = "mcp"`; exclude `mcp/tests/` from strict mypy per this project's existing test-code convention.
- [x] T004 [P] [ECONOMY] Add `mcp/gateway/settings.py` — the five sim base URLs, vendor credentials, and `MCP_DEV_SIGNING_KEY`, read from environment with `.env.example`-documented defaults.
- [x] T005 [P] [ECONOMY] Create `mcp/Dockerfile` following `sims/dining/Dockerfile`'s pattern: `COPY observability /app/observability`, `COPY mcp/gateway /app/gateway`, `ENV PYTHONPATH=/app`, `EXPOSE 9100`, `CMD uvicorn gateway.main:app --host 0.0.0.0 --port 9100` (F2).
- [x] T006 [ECONOMY] Correct `docker-compose.yml`'s `mcp-gateway` service to `build: {context: ., dockerfile: mcp/Dockerfile}` and add the five sim base URLs as environment variables (F2).
- [x] T007 [P] [ECONOMY] **Applied during `/speckit-analyze` remediation.** Fixed the entitlement totals table in `specs/003-mcp-tool-layer/contracts/tool-registry.md` to 3/3/2 (dining), 2/4/1 (stay), 13/11/7 (totals), and correct "10 scope strings" to 13 here and in `plan.md`'s Scale/Scope (F4).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The governed boundary itself. Every tool in every story depends on all of it.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [x] T008 [FRONTIER] [CRIT] Implement `CallerIdentity` and token verification in `mcp/gateway/_shared/identity.py` per `contracts/caller-identity-contract.md` — signature, expiry, required-claim and `tenant_id ∈ {MGV, MHB}` checks, all five refusal conditions producing one indistinguishable outcome. *(CRIT: tenant filter construction.)*
- [x] T009 [FRONTIER] [CRIT] Implement the dev-only issuer `issue_token(sub, tenant_id, scopes, ttl_seconds=3600)` plus a `python -m gateway._shared.identity issue …` CLI in the same file, so `quickstart.md`'s scenarios are runnable. *(CRIT: same tenancy path.)*
- [x] T010 [FRONTIER] [CRIT] Implement the ASGI authentication middleware in `mcp/gateway/_shared/middleware.py` — verifies the bearer token once, ahead of all five mounted sub-apps, and publishes `CallerIdentity` on a context variable. This is the single chokepoint SC-003/SC-004 are proven against. *(CRIT: tenant filter construction.)*
- [x] T011 [FRONTIER] [CRIT] Implement server-side tenant injection in `mcp/gateway/_shared/tenancy.py`: every upstream request gets `X-Tenant-Id` from `CallerIdentity` only, and any tool input model carrying a tenant field is rejected at schema level (FR-009). *(CRIT: tenant filter construction.)*
- [x] T012 [FRONTIER] [CRIT] Implement the four-class classifier in `mcp/gateway/_shared/errors.py`: the published-code table and status-only table from `specs/001-contracts-foundation/data-model.md`, the `5xx→RETRYABLE`/`4xx→TERMINAL` unknown-code fallback (FR-033), and total classification with no pass-through. *(CRIT: error taxonomy and classification.)*
- [x] T013 [FRONTIER] [CRIT] Extend `errors.py` with response-body classification: JSON decode failure → `RETRYABLE` (FR-034), Pydantic `ValidationError` → `TERMINAL` (FR-035), and no field ever extracted from a failing body (FR-036). *(CRIT: same.)*
- [x] T014 [FRONTIER] [CRIT] Implement `NormalizedFailure` construction in `errors.py` against the amended `docs/contracts/schemas/tool-error.schema.json` — preserved vendor diagnostics, `trace_id`, `idempotency_key` on retryables, and `effect_status="unresolved"` when FR-032/FR-035-on-a-mutating-call applies (FR-037). *(CRIT: error taxonomy; feeds hold-ledger recovery.)*
- [x] T015 [FRONTIER] [CRIT] Implement idempotency-key handling in `mcp/gateway/_shared/idempotency.py`: presence/shape validation of the caller-supplied key, verbatim forwarding as the vendor `Idempotency-Key` header, and `sub_call_key(key, slot_name)` derivation for composites. *(CRIT: idempotency key derivation.)*
- [x] T016 [P] [MID] Implement retry-with-jitter in `mcp/gateway/_shared/resilience.py` using `tenacity` — 3 attempts, `wait_exponential_jitter` base 0.25s cap 4s, honouring vendor `retry_after_seconds`, always reusing the original idempotency key (FR-018/023).
- [x] T017 [MID] Implement the per-upstream circuit breaker in the same file — `CLOSED → OPEN` at 5 consecutive failures, 30s cooldown, single `HALF_OPEN` probe; blocking is uniform with no idempotency-key exemption (FR-031); schema-invalid and unparseable bodies increment the failure counter identically to a `5xx` (FR-038).
- [x] T018 [MID] Implement the shared upstream HTTP client in `mcp/gateway/_shared/upstream.py` — one `httpx.AsyncClient` per vendor, wired through tenancy injection, retry, breaker, and response validation so no domain module ever calls `httpx` directly.
- [x] T019 [MID] Implement the five vendor auth schemes in `mcp/gateway/_shared/vendor_auth.py` as `httpx.Auth` subclasses: static `X-Dining-Key`, HTTP Basic (amenities), Bearer (casino), OAuth2 client-credentials with an in-process token cache and refresh-on-401 (pms, ticketing), and ticketing's HMAC `X-TM-Signature` (F5).
- [x] T020 [FRONTIER] [CRIT] Define and implement token-endpoint failure classification in `vendor_auth.py` — an OAuth2 token call that fails classifies like any other upstream failure and counts toward that vendor's breaker, never surfacing as an unhandled exception or an unclassified outcome (F5). *(CRIT: error taxonomy and classification.)*
- [x] T021 [MID] Implement the bare tracer in `mcp/gateway/_shared/tracing.py` per ADR-016 — one span per tool call, attribute names imported **only** from `observability/attributes.py`, in-memory processor for tests and console exporter for local dev; no OTLP, no dual-backend wiring.
- [x] T022 [FRONTIER] [CRIT] Implement the entitlement check in `mcp/gateway/_shared/entitlements.py` — reads each tool's declared `read`/`hold`/`commit` action, checks it against `CallerIdentity.scopes` before the tool body runs, and never infers from the tool's name; `hold` never implies `commit` (FR-012/013/014). *(CRIT: adjacent to tenancy/policy enforcement; a silent failure here is a licensing incident.)*
- [x] T023 [MID] Implement the tool registration decorator in `mcp/gateway/_shared/registry.py` — binds name-regex validation, declared entitlement, idempotency requirement, tracing, and error normalization to every tool in one place, so a tool cannot be registered while skipping any of them.
- [x] T024 [FRONTIER] [CRIT] Implement composite-tool fan-out support in `registry.py`: `SubCallSpec` slots, `required_for_success` handling, and `completed_sub_calls` population on partial failure (FR-030). No shipped tool in this phase declares a mutating composite (F3) — the mechanism is built here and proven by T063. *(CRIT: adjacent to saga compensation; this is the data a future reconciler depends on.)*
- [x] T025 [MID] Implement `mcp/gateway/main.py` — build five `FastMCP` instances, mount them as ASGI sub-apps at `/dining`, `/entertainment`, `/stay`, `/wellness`, `/gaming` behind the shared auth + tracing middleware chain, served as one uvicorn app on `:9100` (ADR-011).

**Checkpoint**: The boundary exists and is enforced. Tools can now be added domain by domain.

> **T063 runs here, not in Phase 11.** It is the only proof of T024's composite partial-failure
> mechanism, so this phase is not complete without it. Its ID is out of numeric order because it
> was relocated during `/speckit-analyze` remediation; IDs are stable identifiers and were not
> renumbered, since several are cross-referenced.

---

## Phase 3: User Story 1 — Reach every vendor through one governed surface (P1) 🎯 MVP

**Goal**: All 31 tools callable through one uniform surface, returning typed results, with no vendor credential or vendor field shape reaching the caller.

**Independent Test**: Invoke at least one read tool in each of the five domains against the running seeded sims; each returns a schema-conformant typed result and the caller holds no vendor credential.

### Tests for User Story 1

- [x] T026 [P] [US1] [MID] Registry-enumeration contract test in `mcp/tests/contract/test_registry_schemas.py` — an MCP **client** (not npx) connects to each running mount path and asserts tool counts 8/7/7/6/3, valid input/output schemas, and name-regex conformance. This is the CI-gating proof; the inspector stays a human-facing demo (F, `quickstart.md` scenario 1).
- [x] T027 [P] [US1] [MID] Integration test in `mcp/tests/integration/test_surface_uniformity.py` — one read per domain returns typed results; no vendor envelope key (`error`, `fault`, `RESPONSE`) and no vendor credential appears anywhere in a response.

### Implementation for User Story 1

- [x] T028 [P] [US1] [ECONOMY] Pydantic response models for dining in `mcp/gateway/dining/models.py`, mirroring `docs/contracts/openapi/dining-sim.openapi.yaml` exactly (ADR-014).
- [x] T029 [P] [US1] [ECONOMY] Pydantic response models for entertainment in `mcp/gateway/entertainment/models.py` from `ticketing-sim.openapi.yaml`.
- [x] T030 [P] [US1] [ECONOMY] Pydantic response models for stay, wellness and gaming in `mcp/gateway/{stay,wellness,gaming}/models.py` from their frozen specs.
- [x] T031 [US1] [MID] Implement the 8 dining tools in `mcp/gateway/dining/server.py` per `contracts/tool-registry.md`, including `list_venues` as a read composite (venues + venue-events enrichment, `required_for_success=False`) and `check_dining_status` taking an explicit `kind: Literal["hold","reservation"]` discriminator (F7).
- [x] T032 [P] [US1] [MID] Implement the 7 stay tools in `mcp/gateway/stay/server.py`, including `get_stay_details` as a read composite over `getStay` + `getFolio` + `getUpgradeOffers`.
- [x] T033 [P] [US1] [MID] Implement the 6 wellness tools in `mcp/gateway/wellness/server.py`.
- [x] T034 [P] [US1] [MID] Implement the 3 gaming tools in `mcp/gateway/gaming/server.py` — read-only, composites for `get_loyalty_status` and `list_casino_floor`; this module must not import `_shared.idempotency` at all (asserted by T055).
- [x] T035 [US1] [ECONOMY] Add a registry-vs-contract test in `mcp/tests/contract/test_registry_matches_doc.py` asserting the live registry matches `contracts/tool-registry.md`'s table row for row, so the doc cannot drift from the code again (F4).

**Checkpoint**: The governed surface is real and enumerable. This is the MVP.

---

## Phase 4: User Story 2 — Operate on exactly one tenant, with no way to choose (P1)

**Goal**: Tenant crossing impossible by construction, proved by test rather than asserted.

**Independent Test**: With a token for one tenant, every available means of reaching the other tenant's data fails or returns nothing.

- [x] T036 [US2] [FRONTIER] [CRIT] Adversarial isolation suite in `mcp/tests/integration/test_tenant_isolation.py` covering both tenants × all five domains: normal call scoped correctly; explicit `tenant_id` argument refused outright (not ignored); a resource id belonging to the other tenant refused or not-found; no other-tenant value in any response or failure detail (SC-003). *(CRIT: tenant filter construction; mandatory test category.)*
- [x] T037 [P] [US2] [FRONTIER] [CRIT] Credential-refusal suite in `mcp/tests/integration/test_identity_refusal.py` — absent, expired, tampered-signature, malformed-claim, and unknown-tenant tokens are each refused before any upstream call is made (SC-004), asserted by a spy on the upstream client. *(CRIT: same.)*
- [x] T038 [US2] [MID] Wire the tenancy assertions into every domain server's tool inputs so no tool schema can accept a tenant field, and add a registry-wide test asserting that property across all 31 tools.

**Checkpoint**: The phase's headline exit criterion is proven, not claimed.

---

## Phase 5: User Story 3 — Receive every failure as one of four known classes (P1)

**Goal**: Total, deterministic classification; no vendor envelope ever reaches a caller.

**Independent Test**: Drive every fault mode each sim supports and confirm every outcome carries exactly one of the four classes.

- [x] T039 [P] [US3] [FRONTIER] [CRIT] Classification unit tests in `mcp/tests/unit/test_error_classification.py` — every published code from `specs/001-contracts-foundation/data-model.md`, both status-only tables, the casino `403 → POLICY_BLOCKED` case, and the unknown-code fallback in both directions (SC-007). *(CRIT: error taxonomy.)*
- [x] T040 [US3] [FRONTIER] [CRIT] Fault-injection integration suite in `mcp/tests/integration/test_error_taxonomy.py` — every `X-Sim-Fault` mode against all five vendors; every `error_500` surfaces `RETRYABLE`; zero vendor envelopes escape (SC-006). *(CRIT: same.)*
- [x] T041 [US3] [MID] Implement the 7 entertainment tools in `mcp/gateway/entertainment/server.py` — poll-only fulfillment via `get_order_status` (no `registerWebhook`, no ingress endpoint, F6), with the same explicit `kind` discriminator as dining (F7).
- [x] T072 [US3] [FRONTIER] [CRIT] Self-exclusion integration test in `mcp/tests/integration/test_policy_blocked.py` — `resortflow.gaming.get_loyalty_status` for a **seeded `self_excluded` guest** (Phase 1 seeded ≥1 per tenant) surfaces `POLICY_BLOCKED` in both tenants and returns no offers payload, proven against the live casino-sim rather than a mocked 403. *(CRIT: policy / responsible gaming / self-exclusion — the most regulator-relevant path in this feature.)*
- [x] T042 [P] [US3] [MID] Redaction test in `mcp/tests/unit/test_failure_redaction.py` asserting no guest-identifying value can appear in a `detail` string (FR-022, SC-013).

**Checkpoint**: A caller can branch on one field and never see a vendor's vocabulary.

---

## Phase 6: User Story 4 — Retry a mutation without causing a second one (P1)

**Goal**: Idempotent mutations across every retry permutation.

**Independent Test**: Repeat an identical mutating call; exactly one mutation exists and the retry returns the original outcome.

- [x] T043 [P] [US4] [FRONTIER] [CRIT] Idempotency unit tests in `mcp/tests/unit/test_idempotency.py` — key presence enforcement, verbatim forwarding, `sub_call_key` determinism across repeated composite retries, and attempt-component semantics (same key on timeout-retry, new key only after a terminal failure). *(CRIT: idempotency key derivation.)*
- [x] T044 [US4] [FRONTIER] [CRIT] Retry-permutation integration suite in `mcp/tests/integration/test_idempotent_retry.py` against live sims — identical repeat, timeout-then-retry, and automatic platform retry all yield zero duplicate mutations (SC-008). *(CRIT: same.)*
- [x] T045 [P] [US4] [MID] Schema test in `mcp/tests/contract/test_idempotency_schemas.py` — 100% of mutating tools reject a call with no idempotency key, 0% of read tools declare one (SC-009).

**Checkpoint**: Two-phase mutation is safe under retry, which is what makes it a safety mechanism at all.

---

## Phase 7: User Story 5 — Let a failing vendor degrade without taking the platform with it (P2)

**Goal**: Per-vendor fast-fail and automatic recovery, with an honest classification for indeterminate commits.

**Independent Test**: Hold one sim failing; its calls fail fast while the other four keep working; it recovers with no operator action.

- [x] T046 [P] [US5] [MID] Breaker state-machine unit tests in `mcp/tests/unit/test_circuit_breaker.py` — threshold, cooldown, half-open probe in both directions, and uniform blocking including known idempotency keys (FR-031).
- [x] T047 [US5] [MID] Resilience integration suite in `mcp/tests/integration/test_resilience.py` — one vendor forced failing opens only its own breaker, the other four domains keep succeeding, recovery is automatic after the cooldown (SC-010).
- [x] T048 [US5] [FRONTIER] [CRIT] Indeterminate-commit test in `mcp/tests/integration/test_indeterminate_commit.py` — a `commit`-scoped call whose retries exhaust while the breaker is open classifies `REQUIRES_HUMAN`, never `RETRYABLE`, and carries `effect_status="unresolved"` (FR-032, SC-015). *(CRIT: hold-ledger recovery path — this is the signal a future reconciler depends on.)*
- [x] T049 [P] [US5] [MID] Retry-behaviour test in `mcp/tests/unit/test_retry_policy.py` — bounded attempts, non-fixed spacing, vendor `retry_after_seconds` honoured.

**Checkpoint**: One sick vendor is contained.

---

## Phase 8: User Story 6 — Invoke only what the caller is entitled to invoke (P2)

**Goal**: The reversible/irreversible boundary is a structural grant, not a convention.

**Independent Test**: A `read`-only token can't hold; a `read`+`hold` token still can't commit.

- [x] T050 [US6] [FRONTIER] [CRIT] Entitlement matrix test in `mcp/tests/integration/test_entitlements.py` — every declared scope string × every tool: a caller lacking a tool's entitlement is refused before any upstream call, and `read`+`hold` is refused on 100% of commit tools in every domain (SC-005). *(CRIT: adjacent to policy enforcement.)*
- [x] T051 [P] [US6] [MID] Declaration test in `mcp/tests/contract/test_entitlement_declarations.py` — all 31 tools declare exactly one of `read`/`hold`/`commit`, and enforcement reads the declaration rather than the name (FR-014), proven by a deliberately misnamed fixture tool.
- [x] T052 [P] [US6] [ECONOMY] Zero-entitlement test in the same file — a token that verifies but carries no scopes is refused by every tool uniformly.

**Checkpoint**: An agent granted holds cannot cross into commits on its own.

---

## Phase 9: User Story 7 — Confirm the gaming domain cannot mutate anything (P2)

**Goal**: The read-only guarantee is provable by enumeration, not by reading a prompt.

**Independent Test**: Enumerate the gaming registry; every tool is a read, and no create/confirm/cancel tool exists to invoke.

- [x] T053 [P] [US7] [MID] Registry-absence test in `mcp/tests/contract/test_gaming_readonly.py` — the live gaming registry contains only `check_*`/`get_*`/`list_*` tools, zero `hold`/`commit` entitlements, and zero `create_*`/`confirm_*`/`cancel_*` names (SC-011).
- [x] T054 [P] [US7] [MID] Unknown-tool test in the same file — invoking a plausible gaming mutation by name fails as an unknown tool, not as a denied one.
- [x] T055 [US7] [MID] Static-import test in `mcp/tests/contract/test_gaming_imports.py` — AST-inspect `mcp/gateway/gaming/server.py` and assert it does not import `_shared.idempotency`, mirroring Phase 1's read-only casino sim. This is the precise structural analogue; gaming *does* import `_shared.entitlements`, since `gaming:read` still has to be enforced (F11 correction to plan.md's Constraints).

**Checkpoint**: A regulator-facing claim backed by a listing, not a policy document.

---

## Phase 10: User Story 8 — Trace any tool call after the fact (P3)

**Goal**: Exactly one correctly-shaped span per call, drawn from the frozen vocabulary.

**Independent Test**: Invoke a tool; exactly one span is emitted, every attribute name comes from `observability/attributes.py`, and a failure's `trace_id` joins to it.

- [x] T056 [P] [US8] [MID] Span-emission test in `mcp/tests/unit/test_tracing.py` using an in-memory exporter — exactly one span per invocation, success and failure, carrying tool, domain, tenant, session, idempotency key where applicable, and outcome (SC-012).
- [x] T057 [P] [US8] [MID] Vocabulary-closure test in the same file — no attribute-name string literal appears anywhere under `mcp/gateway/` outside imports from `observability/attributes.py` (FR-028), enforced by source inspection.
- [x] T058 [US8] [MID] Correlation test in `mcp/tests/integration/test_trace_correlation.py` — a failed call's `NormalizedFailure.trace_id` matches the emitted span's trace id (FR-021).
- [x] T059 [P] [US8] [ECONOMY] PII-absence test asserting no guest-identifying value reaches an emitted span (FR-029, SC-013).

**Checkpoint**: Every vendor interaction left a queryable record, ready for Feature 007 to export.

---

## Phase 11: Polish & Cross-Cutting Concerns

- [x] T060 [P] [ECONOMY] Add `mcp/gateway/README.md` describing the mount paths, the token issuer CLI, and how to run the gateway locally.
- [x] T061 [P] [ECONOMY] Update `.env.example` with the five sim base URLs, the five vendor credential variables, and `MCP_DEV_SIGNING_KEY` — no real-looking hostnames (the Phase 1 convergence finding).
- [x] T062 [ECONOMY] Wire `make up` / add a `make mcp` target for the gateway, and generate `MCP_DEV_SIGNING_KEY` in `make seed` if absent, matching `quickstart.md`'s prerequisites.
- [x] T063 [FRONTIER] [CRIT] Composite partial-failure unit test in `mcp/tests/unit/test_composite_failure.py` — a synthetic two-slot **mutating** composite fixture where slot 1 succeeds and slot 2 fails produces a failure carrying every completed sub-call's `slot_name`, `vendor_resource_id` and derived `idempotency_key`, with no compensation attempted (SC-014, F3). *(CRIT: adjacent to saga compensation.)*
- [x] T064 [MID] Run the full `quickstart.md` scenario set against the live Dockerised gateway and seeded sims — not just the in-process test client, per this feature's clarified runnable-deliverable scope.
- [x] T065 [P] [MID] Run `ruff check mcp/` and `mypy mcp/gateway` clean; record any disclosed relaxation in `pyproject.toml` comments rather than silently.
- [x] T066 [P] [ECONOMY] **Applied during `/speckit-analyze` remediation.** Corrected `plan.md`'s Structure Decision: this feature touches `docker-compose.yml`, `pyproject.toml`, `Makefile`, `.env.example`, `sims/_shared/faults.py` (deviation 2) and two `docs/contracts/` files — not "zero cross-cutting files" (F8).
- [x] T067 [P] [ECONOMY] **Applied during `/speckit-analyze` remediation.** Resolved `plan.md`'s self-contradiction: the Contract Stability row claims Phase 1's sims are unchanged while Recorded Deviation 2 adds a fault mode to them — state the exception in the row (F9).
- [x] T068 [P] [ECONOMY] **Applied during `/speckit-analyze` remediation.** Reworded `plan.md`'s "Storage: None / stateless" to "no durable state; in-process ephemeral state only (breaker counters, OAuth token cache)", since Principle V's verdict leans on that word (F10).
- [x] T069 [P] [ECONOMY] **Applied during `/speckit-analyze` remediation.** Fixed `data-model.md`'s `CallerIdentity` note, which garbles `hold_ledger`'s columns (`tenant_id` and `domain` are separate columns) (F13).
- [x] T070 [ECONOMY] Write the Phase 2 handoff recording what was built, interfaces published, and the deviations this list already names — the F3 gap (no mutating composite exists in-domain; cross-domain composites are Phase 4–5 saga work), the F6 decision (poll-only fulfillment), and the Track A `schema_drift` dependency.
- [x] T071 [MID] Raise the `schema_drift` fault mode with Track A as an integration-commit item (`sims/_shared/faults.py`, `specs/002-simulation-ecosystem/contracts/sim-control-plane.md`) and, once added, enable the currently-skipped schema-invalid cases in `mcp/tests/integration/test_error_taxonomy.py` (SC-016).

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: no dependencies. T001 and T003 gate everything (the package must be importable at all).
- **Foundational (Phase 2)**: depends on Setup. **Blocks every user story.** Within it: T008–T011 (identity/tenancy) → T022 (entitlements) → T023/T024 (registration) → T025 (assembly); T012–T014 (classification) gate T018; T019 gates T018; T016/T017 gate T018.
- **US1 (Phase 3)**: depends on Phase 2 complete. All other stories depend on US1's tools existing, since they test behaviours *of* those tools.
- **US2–US8 (Phases 4–10)**: depend on Phase 2 + US1. Independent of each other and parallelisable.
- **Polish (Phase 11)**: T063 depends on T024 only and can run early; the rest depend on the stories they document.

### Story dependencies

US1 is the true MVP and a hard prerequisite for the rest — unlike a typical feature where stories are peers, US2–US8 verify cross-cutting properties of the surface US1 builds. They are independently *testable* and independently *deliverable* once US1 exists, but not independently *implementable* before it.

### Parallel opportunities

- T004, T005, T007 in Setup.
- T016, T019, T021 in Foundational (different files, no shared dependencies).
- T028, T029, T030 (response models, three files) then T032, T033, T034 (three domain servers).
- Once US1 lands: US2, US3, US4, US5, US6, US7, US8 can proceed concurrently.
- T060, T061, T065–T069 across Polish.

---

## Parallel Example: User Story 1

```bash
# Response models — three files, no shared state:
Task: "Pydantic response models for dining in mcp/gateway/dining/models.py"
Task: "Pydantic response models for entertainment in mcp/gateway/entertainment/models.py"
Task: "Pydantic response models for stay, wellness and gaming in mcp/gateway/*/models.py"

# Then the domain servers that depend on them:
Task: "Implement the 7 stay tools in mcp/gateway/stay/server.py"
Task: "Implement the 6 wellness tools in mcp/gateway/wellness/server.py"
Task: "Implement the 3 gaming tools in mcp/gateway/gaming/server.py"
```

---

## Implementation Strategy

### MVP first

1. Phase 1 Setup — package importable, image buildable, compose corrected.
2. Phase 2 Foundational — the governed boundary. Longest phase; nothing ships without it.
3. Phase 3 US1 — 31 tools enumerable with valid schemas against running servers.
4. **STOP and VALIDATE**: `quickstart.md` scenario 1 passes. That alone satisfies the phase's first exit criterion.

### Incremental delivery

US2 (tenant isolation) next — it is the phase's headline exit criterion and the one whose
failure would invalidate the platform. Then US3 (taxonomy) and US4 (idempotency), which the
saga work in Phase 4–5 binds to directly. US5–US7 harden. US8 closes the observability loop.

---

## Model tier summary *(mandatory — constitution Principle VI)*

| Tier | Tasks | `[CRIT]` | Notes |
|---|---|---|---|
| FRONTIER | 21 | 21 | Every FRONTIER row is `[CRIT]` by the `docs/models.md` table, and no `[CRIT]` row sits below FRONTIER: tenant filter construction (T008–T011, T036, T037, T050), error taxonomy and classification (T012–T014, T020, T039, T040), idempotency key derivation (T015, T043, T044), entitlement enforcement at the reversibility boundary (T022), hold-ledger recovery path (T048), saga-compensation-adjacent data (T024, T063), and policy / responsible gaming / self-exclusion (T072). |
| MID | 30 | 0 | MCP tool adapters, orchestration wiring and non-trivial test design — all three named explicitly in `docs/models.md`'s MID list. Real judgment required, but errors surface fast (a broken adapter fails its own test). |
| ECONOMY | 21 | 0 | Pydantic models from a frozen contract (T028–T030), `Dockerfile`/`docker-compose.yml`/`Makefile`/`.env.example` edits (T005, T006, T061, T062), docs corrections (T007, T066–T070), and test cases derived from an explicit criteria table (T035, T052, T059) — every one an enumerated ECONOMY row in `docs/models.md`, each fully determined by an artifact that already exists. |

**Total: 72 tasks** (71 generated + T072 added during `/speckit-analyze` remediation), of which
5 are already complete. No ADR tasks appear (ADRs 011–016 were written during `/speckit-plan`
and are `[CRIT]`/FRONTIER by that table; none is outstanding).

### Verification

Run against `/speckit-analyze`:

```
Flag any task marked ECONOMY whose acceptance criteria require a judgment
not fully specified in plan.md. Flag any [CRIT] task not on FRONTIER.
```

---

## Notes

- `[P]` tasks touch different files with no incomplete dependencies.
- Every task names an exact file path; every task carries a tier.
- Commit after each task or logical group; stop at any checkpoint to validate a story alone.
- The seven plan corrections at the top of this file are not optional cleanup — F1 and F2
  block the first `import` and the first `docker compose build` respectively.
- Tier drift is the failure mode to watch: when this phase runs long, the temptation is to
  bump the FRONTIER rows down. Those sixteen rows are the ones where being wrong is silent.
