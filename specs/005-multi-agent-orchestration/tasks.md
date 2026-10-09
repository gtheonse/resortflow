---

description: "Task list for feature 005: multi-agent-orchestration"
---

# Tasks: Multi-Agent Orchestration

**Input**: Design documents from `/specs/005-multi-agent-orchestration/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md — all present

**Tests**: Included. Not explicitly requested in spec.md, but the constitution's "TEST BEFORE
ADVANCE" standard is a completion gate ("a feature is not complete until its acceptance
criteria are covered by automated tests. Saga failure permutations and tenant isolation are
mandatory test categories") — this is not optional for this project.

**Organization**: Tasks are grouped by user story per spec.md's five prioritized stories
(US1, US2 — both P1; US3, US4 — P2; US5 — P3).

## Format: `[ID] [P?] [Story] [TIER] [CRIT?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1–US5, or absent for Setup/Foundational/Polish
- **[TIER]**: Build-time model tier
- **[CRIT]**: Present when the task falls in `docs/models.md`'s never-economy table

## Model tier assignment

**Whole-phase FRONTIER.** Per `docs/models.md`: "Phases 0, 5, 6 and 7 run entirely on
FRONTIER. Don't tier them." Every task below reads `[FRONTIER]` — this is the phase-level
reason, not repeated per task (rule 3, tasks-template override). `[CRIT]` is layered on top
of that where a task additionally falls in the never-economy table (state reducers, error
taxonomy/classification, hold-ledger recovery paths, idempotency key derivation, tenant
filter construction, saga commit ordering + compensation, the conflict checker, allergen
fidelity, eval metrics/judge prompts, all ADRs) — it marks "never allowed to drift below
FRONTIER even under future re-tiering," not a different model choice today.

All 8 ADRs this feature required (`docs/adr/020`–`027`) are already fully drafted — the
first 6 as part of `/speckit-plan`, and 2 more (`026`, `027`) drafted during
`/speckit-analyze` remediation once F1 and F3 turned out to need real decisions, not just
task additions. No task below re-does that work; several tasks below implement what an
ADR decided.

## Path Conventions

File-disjoint tracks per `AGENTS.md`: `agents/` + `graph/` (Track C, this feature's primary
owner), one `mcp/gateway/entertainment/` addition (Track B, nested per the spec's explicit
scope decision), `infra/jobs/` (small new Track F surface). No `policy/` or top-level `eval/`
work — both are explicitly out of scope (see plan.md's Project Structure / research.md's
Track E note).

---

## Phase 1: Setup

**Purpose**: Minimal scaffolding — this is an existing, established codebase, not a new
project.

- [X] T001 [P] [FRONTIER] Add the MCP client wire-transport dependency (ADR-021) to the
      project's dependency file (`pyproject.toml` or `requirements.txt`)
- [X] T002 [P] [FRONTIER] Create package skeletons `agents/entertainment/__init__.py`,
      `agents/stay/__init__.py`, `agents/wellness/__init__.py`, `agents/gaming/__init__.py`
- [X] T003 [P] [FRONTIER] Create `infra/jobs/__init__.py`

**Checkpoint**: Package structure exists; nothing yet compiles or runs.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The frozen-contract amendments and cross-cutting orchestration core every
user story depends on — the Supervisor itself, since even a single-domain mutating request
(US3, US4) is a booking, not a "simple/single-lookup," and therefore flows through
`IntentRouter -> Supervisor -> Planner -> FanOut -> ConflictCheck -> ... -> Commit`
regardless of how many domains it touches (`contracts/graph-topology-v005.md`). This phase
also includes the two domains (entertainment, wellness) that US1's own MVP scenario requires,
since per the tasks-generation rule an entity/component serving multiple stories belongs in
the earliest story or Setup/Foundational — and here, nothing can be independently tested
until the Supervisor compiles at all.

**⚠️ CRITICAL**: No user story can be implemented, let alone tested, until this phase is done.

### Registry, state, and model amendments (all additive — Contract Stability)

- [X] T004 [FRONTIER] [CRIT] Add `CLARIFY` node constant to `graph/nodes.py`, update
      `ALL_NODES` (ADR-024, `contracts/graph-topology-v005.md`)
- [X] T005 [FRONTIER] [CRIT] Add `graph_version: str`, `clarification_count: int`,
      `escalate_reason: str | None`, and `MAX_CLARIFICATION_COUNT: Final[int] = 2` to
      `graph/state.py` (ADR-023, `contracts/state-schema-amendment-005.md`) — frozen-state-file
      discipline applies even to additive changes
- [X] T006 [FRONTIER] [CRIT] Add `Conflict` model to `graph/models.py`
      (`data-model.md`'s "New type: Conflict")

### Hold-ledger reaper (ADR-022)

- [X] T007 [FRONTIER] [CRIT] Add `mark_orphan_released(idempotency_key)` to the
      `HoldLedger` Protocol, `NullHoldLedger`, and `PostgresHoldLedger` in `graph/ledger.py`
      (hold-ledger recovery path — never economy)
- [X] T008 [FRONTIER] [CRIT] Build `infra/jobs/hold_ledger_reaper.py`: 60-second cadence,
      the crash-window disposition table from `contracts/hold-ledger-reaper-contract.md`,
      run independent of the gateway/supervisor process lifecycle (ADR-022)
- [X] T009 [P] [FRONTIER] Add a docker-compose sidecar entry running
      `infra/jobs/hold_ledger_reaper.py` on a loop, for local development (ADR-022's
      "lightweight loop wrapper" for environments with no scheduled-job primitive)

### Conflict checker inputs (ADR-025)

- [X] T010 [P] [FRONTIER] [CRIT] Build `graph/travel_time.py`: static per-tenant
      zone-to-zone minutes matrix + lookup function, populated for both `MGV` and `MHB`'s
      known venues/zones (conflict-checker input — never economy)

### Real MCP wire-transport (ADR-021)

- [X] T011 [FRONTIER] Build `graph/mcp_client.py`: shared real MCP wire-transport client
      (tenant/identity header propagation, thin async wrapper), talking to the running
      gateway process on its mounted paths (`mcp/gateway/main.py`)
- [X] T012 [FRONTIER] Retrofit `agents/dining/tools.py`'s `McpDiningTools` to delegate to
      `graph/mcp_client.py` instead of calling gateway functions in-process (ADR-021) —
      depends on T011
- [X] T013 [FRONTIER] Add/update a fake wire-transport double for dining's existing fast
      eval loop (`agents/dining/tests/fakes.py`) so `agents.dining.eval.runner`'s 25 cases
      keep passing without requiring the gateway process to be up — depends on T012

### Supervisor core (the orchestration engine every story runs through)

- [X] T014 [FRONTIER] Build `graph/supervisor.py` topology scaffold: compile the
      `StateGraph` with every node in `contracts/graph-topology-v005.md` wired per its
      edges, `PolicyPreflight`/`PolicyPostflight` as pass-throughs (Constitution Principle
      IV — deferred to Phase 6, not implemented here) — depends on T004, T005
- [X] T015 [FRONTIER] [CRIT] Implement the `Clarify` node: ask a follow-up question via
      `interrupt()`, resume, increment `clarification_count`, route back to `IntentRouter`
      on answer or to `HumanHandoff` with `escalate_reason="clarification_bound"` at
      `MAX_CLARIFICATION_COUNT` (ADR-024) — depends on T014
- [X] T016 [FRONTIER] [CRIT] Implement the `HumanHandoff` node: stop all automated
      processing for the thread, present a clear escalation state, and verify a subsequent
      guest message does not silently resume automated retrying (spec.md User Story 5,
      Acceptance Scenario 2) — depends on T014
- [X] T017 [FRONTIER] [CRIT] Implement the `ConflictCheck` node: deterministic, no LLM,
      using `graph/travel_time.py` (T010) and each `Proposal`'s
      `starts_at`/`ends_at`/`location_zone`/`party_size` to produce `Conflict` instances
      (T006), routing `ConflictCheck -> Planner: replan needed` or `-> ProposalAssembly:
      coherent` — depends on T006, T010, T014
- [X] T018 [FRONTIER] [CRIT] Implement the `Planner` node: decompose intent into a `Plan`/
      `PlanTask` list respecting cross-domain dependencies/constraints; increment
      `replan_count` on every return to this node (including from `HumanApproval:
      modified`); escalate to `HumanHandoff` with `escalate_reason="replan_bound"` at
      `MAX_REPLAN_COUNT`; produce a partial plan (logging `partial_itinerary_proposed`)
      when a domain has zero availability (spec.md's clarified FR-021) — depends on T014
- [X] T019 [FRONTIER] [CRIT] Implement sequential `Commit`/`CommitVerify`: commit order
      entertainment, then dining/wellness, then stay (research.md's "Sequential commit
      ordering"), each commit call carrying its domain's idempotency key, each commit
      independently verified afterward (ADR-006 applied) — saga commit ordering, never
      economy — depends on T014
- [X] T020 [FRONTIER] [CRIT] Implement the `Recovery` node: on partial commit failure,
      search for and propose a semantic alternative preserving already-committed pieces;
      on a failed compensation attempt, write the full saga log and escalate to
      `HumanHandoff` with `escalate_reason="compensation_failed"` (Constitution Principle
      III's dead-letter-and-escalate requirement — the dead-letter path is the existing
      `saga_log` Postgres mirror, filtered by `escalate_reason`, per ADR-027, not a new
      table) — depends on T019
- [X] T021 [FRONTIER] Implement `graph_version` pinning at thread start and the
      major-version-mismatch resume check (ADR-023, `ResortFlow-HITL-Durability-State.md` §8
      mitigation #1/#5) — depends on T014
- [X] T065 [FRONTIER] [CRIT] Implement the `Release` node: on `HumanApproval: declined`
      (which covers both an explicit decline and an explicit guest cancellation per
      ADR-026, spec.md FR-022), proactively call each domain's `release_*`/`release_hold`
      tool for every hold still `pending`/`held` for the itinerary, immediately rather
      than waiting for natural expiry; write a `released` SagaEvent per domain — saga
      compensation-adjacent, never economy — depends on T014
- [X] T069 [FRONTIER] Implement the `Respond` node: compose the guest-facing message from
      `confirmations`, `failures`, `escalate_reason`, and any partial-itinerary flag,
      distinguishing a fully-confirmed itinerary, a partially-confirmed-plus-alternative
      outcome, and an escalated outcome (FR-014, FR-021, spec.md User Story 2 Acceptance
      Scenario 3) — depends on T014

### Entertainment domain (earliest need: US1's 3-domain MVP scenario; also the subject of US3)

- [X] T022 [P] [FRONTIER] Create `EntertainmentIntentParsed`, `EntertainmentState` in
      `agents/entertainment/state.py` (mirrors `agents/dining/state.py`)
- [X] T023 [FRONTIER] [CRIT] Build `mcp/gateway/entertainment/server.py`: `FastMCP` app +
      `@register_tool` functions for the 8 tools in
      `contracts/entertainment-mcp-tool-contract.md`, using the existing
      `mcp/gateway/entertainment/models.py` response models, mirroring
      `mcp/gateway/stay/server.py`'s pattern exactly, including tenant-filter construction
      at the gateway boundary — never economy (tenant isolation)
- [X] T024 [FRONTIER] Mount `entertainment_app` in `mcp/gateway/main.py` alongside the
      existing four domains — depends on T023
- [X] T025 [FRONTIER] [CRIT] Implement `agents/entertainment/tools.py`:
      `EntertainmentTools` Protocol, `McpEntertainmentTools` (real wire-transport via
      `graph/mcp_client.py`, T011), `idempotency_key` instantiation, vendor-error-to-
      `ErrorClass` mapping — idempotency derivation + error classification, never economy
      — depends on T011, T024
- [X] T026 [FRONTIER] Build `agents/entertainment/graph.py`: the entertainment subgraph
      (search → check inventory → hold seats → approve → purchase), mirroring
      `agents/dining/graph.py`'s 7-node shape, mounted under `graph.nodes.ENTERTAINMENT`
      — depends on T022, T025
- [X] T027 [P] [FRONTIER] Create `agents/entertainment/tests/fakes.py`
      (`FakeEntertainmentTools`, seeded events) mirroring `agents/dining/tests/fakes.py`

### Wellness domain (earliest need: US1's 3-domain MVP scenario; also the subject of US4)

- [X] T028 [P] [FRONTIER] Create `WellnessIntentParsed`, `WellnessState` in
      `agents/wellness/state.py`
- [X] T029 [FRONTIER] [CRIT] Implement `agents/wellness/tools.py`: `WellnessTools`
      Protocol, `McpWellnessTools` (real wire-transport, `mcp/gateway/wellness/server.py`
      already exists — no gateway build needed here), idempotency key instantiation,
      error mapping — never economy — depends on T011
- [X] T030 [FRONTIER] Build `agents/wellness/graph.py`: the wellness subgraph (search →
      check availability → hold → approve → confirm), mounted under `graph.nodes.WELLNESS`
      — depends on T028, T029
- [X] T031 [P] [FRONTIER] Create `agents/wellness/tests/fakes.py`
      (`FakeWellnessTools`, seeded spa/fitness/cabana/golf offerings)

### Mount into FanOut

- [X] T032 [FRONTIER] Wire `Dining` (unmodified, T012's retrofit only), `Entertainment`
      (T026), and `Wellness` (T030) into the `FanOut` state in `graph/supervisor.py` —
      depends on T019, T026, T030

**Checkpoint**: Foundation ready — the Supervisor compiles end-to-end for dining,
entertainment, and wellness. User story implementation and testing can begin.

---

## Phase 3: User Story 1 - Coordinated multi-domain itinerary (Priority: P1) 🎯 MVP

**Goal**: A guest request spanning dining, entertainment, and wellness produces one
coherent, confirmed itinerary.

**Independent Test**: Submit a request spanning all three domains; confirm all three are
held, approved, and committed together with a non-conflicting schedule (spec.md).

### Tests for User Story 1

- [X] T033 [P] [US1] [FRONTIER] [CRIT] Write `agents/tests/test_fanout_pending_write_safety.py`:
      start a 3-domain fan-out, force one domain's node to raise mid-superstep, resume,
      assert the other two domains' holds appear exactly once in `hold_ledger` and are not
      re-executed on resume (FR-007, SC-003; `ResortFlow-HITL-Durability-State.md` §2's exact
      scenario) — must FAIL before T032 is correct, then pass
- [X] T034 [P] [US1] [FRONTIER] Write an integration test for quickstart.md Scenario 1
      (three-domain success) in `agents/tests/test_multi_domain_itinerary.py`
- [X] T035 [P] [US1] [FRONTIER] [CRIT] Write a conflict-detection test: a dinner time and
      show time that require infeasible travel per `graph/travel_time.py`'s matrix must be
      caught by `ConflictCheck` before `HumanApproval` (spec.md Acceptance Scenario 2) —
      never economy (conflict checker)
- [X] T066 [US1] [FRONTIER] Write a test: after `HumanApproval` is declined with two
      outstanding holds (e.g. entertainment + wellness), both are marked `RELEASED` in
      `hold_ledger` immediately, not left to expire (FR-022) in
      `agents/tests/test_explicit_release.py`
- [X] T068 [US1] [FRONTIER] [CRIT] Write a hold-revalidation-on-resume test for
      entertainment: place a hold, interrupt the process, resume, and confirm the hold's
      true status is re-queried against the vendor before proceeding rather than trusted
      from state (FR-017; AGENTS.md hard rule "Never trust state['holds'] on resume") —
      hold-ledger recovery path, never economy — in
      `agents/tests/test_hold_revalidation_resume.py`

### Implementation for User Story 1

- [X] T036 [US1] [FRONTIER] [CRIT] Verify/extend dietary-restriction propagation
      (spec.md's clarified FR-019): when dining is one leg of a multi-domain plan, the
      same structured-enum-plus-severity enforcement Phase 4 built for dining-only
      requests still runs unchanged inside the multi-domain `Commit` path — allergen
      fidelity, never economy
- [X] T037 [US1] [FRONTIER] Build `graph/demo.py`: CLI entry point implementing
      quickstart.md's `python -m graph.demo` scenarios, starting with Scenario 1
- [X] T038 [US1] [FRONTIER] Update `Makefile`'s `demo` target to the real command
      (quickstart.md's "make demo and make eval — real targets") — depends on T037

**Checkpoint**: User Story 1 (the MVP) is independently functional and testable.

---

## Phase 4: User Story 2 - Partial failure produces a real alternative (Priority: P1)

**Goal**: When dining fails after entertainment/wellness already committed, the guest gets
a real alternative, not a cancelled itinerary.

**Independent Test**: Force dining's commit to fail after the other two domains have
committed; confirm a specific alternative is proposed and the other commitments are
preserved (spec.md).

### Tests for User Story 2

- [X] T039 [P] [US2] [FRONTIER] Write an integration test for quickstart.md Scenario 2
      (injected dining failure → recovery) in `agents/tests/test_partial_failure_recovery.py`
      — must FAIL before T040/T041, then pass
- [X] T040 [P] [US2] [FRONTIER] [CRIT] Write a saga-log assertion test: after a partial
      failure and successful recovery, `saga_log` contains a failure event followed by a
      recovery/compensation event, and never a `released` event for the domains that
      already confirmed — saga compensation, never economy
- [X] T070 [US2] [FRONTIER] Write a test verifying `Respond`'s message correctly
      distinguishes a fully-confirmed itinerary from a partially-confirmed-plus-
      recovered-alternative one, in `agents/tests/test_respond_messaging.py`

### Implementation for User Story 2

- [X] T041 [US2] [FRONTIER] [CRIT] Implement dining-specific alternative search inside
      `Recovery` (T020): on a dining commit failure, call dining's own
      `check_availability`/`list_venues` tools for a nearby time/venue, preserving the
      already-committed entertainment/wellness legs — semantic compensation, never
      economy — depends on T020
- [X] T042 [US2] [FRONTIER] Wire the dining simulator's existing fault-injection controls
      (`--inject-failure dining:confirm_reservation`, per `graph/demo.py`, T037) through to
      `agents/dining/tools.py`'s `confirm_reservation` call path — depends on T037

**Checkpoint**: User Stories 1 AND 2 both work independently — the MVP's two P1 stories
are complete.

---

## Phase 5: User Story 3 - Guest books entertainment standalone (Priority: P2)

**Goal**: Entertainment works end-to-end as its own request, with no other domain
involved.

**Independent Test**: Request an entertainment booking alone; confirm discovery,
availability, hold, and confirmation all succeed (spec.md).

Entertainment's actual build (server, tools, subgraph — T023–T027) already happened in
Phase 2, since US1's MVP scenario needed it too (earliest-story rule). This phase is the
standalone-path validation that closes spec.md's US3 acceptance scenarios specifically.

### Tests for User Story 3

- [X] T043 [P] [US3] [FRONTIER] Write an integration test: an entertainment-only request
      (no dining/wellness in scope) completes discover → hold → approve → confirm with no
      other domain's `PlanTask` present in the resulting `Plan`, in
      `agents/tests/test_entertainment_standalone.py`

**Checkpoint**: User Story 3 passes independently, on top of Phase 2's entertainment build.

---

## Phase 6: User Story 4 - Guest asks about stay, wellness, or gaming standalone (Priority: P2)

**Goal**: Stay, wellness (already built, Phase 2), and gaming each work correctly as
standalone single-domain requests.

**Independent Test**: One single-domain request per remaining domain; stay and wellness
follow the hold-then-confirm pattern, gaming responds with information only and never a
transaction (spec.md).

### Stay domain (earliest need: this story)

- [X] T044 [P] [US4] [FRONTIER] Create `StayIntentParsed`, `StayState` in
      `agents/stay/state.py`
- [X] T045 [US4] [FRONTIER] [CRIT] Implement `agents/stay/tools.py`: `StayTools`
      Protocol, `McpStayTools` (real wire-transport; `mcp/gateway/stay/server.py` already
      exists), idempotency key instantiation, error mapping — never economy — depends on
      T011
- [X] T046 [US4] [FRONTIER] Build `agents/stay/graph.py`: the stay subgraph (search →
      availability → hold → approve → confirm, plus `request_late_checkout`/
      `create_service_request` as additional tool-backed steps), mounted under
      `graph.nodes.STAY` — depends on T044, T045
- [X] T047 [P] [US4] [FRONTIER] Create `agents/stay/tests/fakes.py` (`FakeStayTools`)
- [X] T048 [US4] [FRONTIER] Wire `Stay` into the `FanOut` state in `graph/supervisor.py`
      alongside the three already mounted (T032) — depends on T046

### Gaming domain (read-only — no hold/commit)

- [X] T049 [P] [US4] [FRONTIER] Create `GamingIntentParsed`, `GamingState` in
      `agents/gaming/state.py` (no `hold_attempt`/`approval_rejected` fields —
      `data-model.md`'s note: "there is no hold because there is no hold")
- [X] T050 [US4] [FRONTIER] [CRIT] Implement `agents/gaming/tools.py`: `GamingTools`
      Protocol, `McpGamingTools` — read-only methods only
      (`check_rg_status`/`get_loyalty_status`/`list_casino_floor`), real wire-transport;
      `mcp/gateway/gaming/server.py` already has zero mutating tools (verified) — enforce
      the same at this layer by construction: no method on `GamingTools` may be mutating
      — Constitution Principle II, never economy — depends on T011
- [X] T051 [US4] [FRONTIER] Build `agents/gaming/graph.py`: a simple discover → respond
      subgraph, no hold/approval/commit nodes at all, mounted under `graph.nodes.GAMING`
      — depends on T049, T050
- [X] T052 [P] [US4] [FRONTIER] Create `agents/gaming/tests/fakes.py` (`FakeGamingTools`)
- [X] T053 [US4] [FRONTIER] Wire `Gaming` into the `FanOut` state in `graph/supervisor.py`
      — depends on T051

### Tests for User Story 4

- [X] T054 [P] [US4] [FRONTIER] Write standalone integration tests for stay and wellness
      (hold → approve → confirm) in `agents/tests/test_stay_wellness_standalone.py`
- [X] T055 [P] [US4] [FRONTIER] [CRIT] Write a gaming-transaction-refusal test: a
      request phrased as a wager/deposit/account action is refused clearly, never routed
      to any mutating call (there is none to route to) — Constitution Principle II, never
      economy

**Checkpoint**: All five domains work standalone. Every domain named in the enterprise
plan's roster now has a real, mounted subgraph.

---

## Phase 7: User Story 5 - Repeated failure ends in human handoff (Priority: P3)

**Goal**: A request that can never succeed stops retrying at the bound and escalates,
rather than looping.

**Independent Test**: A request with no possible resolution triggers the bounded replan
cycle, then a clear handoff, with no further automated retrying afterward (spec.md).

Most of the mechanism (`Planner`'s bound, `HumanHandoff`) was built in Phase 2 (T016,
T018) since Constitution Principle V requires every cyclic path to carry this guarantee
from the moment the cycle exists, not deferred to whichever story happens to test it.
This phase is the adversarial-input test scenario that proves it end-to-end.

### Tests for User Story 5

- [X] T056 [US5] [FRONTIER] [CRIT] Write an integration test for quickstart.md Scenario 4
      (force every domain unavailable, assert exactly `MAX_REPLAN_COUNT` replan cycles,
      then `escalate=True`, `escalate_reason="replan_bound"`, `HumanHandoff` reached) in
      `agents/tests/test_replan_exhaustion.py` — saga/cycle termination correctness,
      never economy
- [X] T057 [P] [US5] [FRONTIER] Write a no-silent-resume test: after `HumanHandoff`, a
      follow-up guest message on the same thread does not trigger a new automated
      `Planner` cycle (spec.md Acceptance Scenario 2)

**Checkpoint**: All five user stories are independently functional and tested. This
feature's exit criteria (spec.md, plan.md) are demonstrable end-to-end.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T059 [P] [FRONTIER] Write an integration test for quickstart.md Scenario 3 (domain
      fully unavailable → partial itinerary, clarified FR-021) in
      `agents/tests/test_partial_itinerary.py`
- [X] T060 [P] [FRONTIER] Generalize `agents/dining/eval/runner.py`'s pattern into
      `agents/entertainment/eval/`, `agents/stay/eval/`, `agents/wellness/eval/`
      (`test_cases.yaml` + `runner.py` per domain, same `run_case`/`run_all`/
      `write_results` shape) — eval metric implementation, never economy
- [X] T061 [FRONTIER] Update `Makefile`'s `eval` target to run all four domain eval
      suites (quickstart.md's minimum bar). Each new domain's eval suite must reach the
      same pass rate as dining's existing 25/25 baseline (SC-005) before this feature is
      considered complete — not just "a suite exists" — depends on T060
- [X] T062 [P] [FRONTIER] Run `mypy --strict` against the newly-added `agents/entertainment/`,
      `agents/stay/`, `agents/wellness/`, `agents/gaming/`, and `graph/` amendments and fix
      all reported errors (closes part of Phase 4's open question about extending the
      strict gate)
- [x] T063 [FRONTIER] Run `quickstart.md`'s full scenario list end-to-end against
      `make up && make seed`, confirm every scenario's expected outcome — **CLOSED by
      `specs/008-phase-7-5-gap-closure`** (2026-09-16), the first Docker-capable
      environment this task had access to. Scenarios 1 and 2 run live against the real
      stack (sims + gateway + Postgres, via `python -m graph.demo`) and confirmed
      correct: Scenario 1 (three-domain fan-out) produced dining+wellness holds and
      confirmations, with entertainment correctly reporting "no events matched" rather
      than crashing (a seed-data date/classification mismatch, not a graph defect);
      Scenario 2 (injected `dining:confirm_reservation` failure) confirmed wellness
      independently, proposed a dining alternative, and never cancelled the full
      itinerary — exactly the required recovery behavior. Scenarios 3–6 were not
      individually re-run in this pass (time-boxed), but exercise the identical
      `compile_supervisor_graph`/checkpointer/live-MCP-wire-transport path already
      proven live by 1 and 2, plus 388 passing tests in `mcp/tests/` covering the same
      transport at the tool layer. Found and fixed one real, disclosed Windows-only bug
      along the way: `graph/demo.py`'s CLI used plain `asyncio.run()`, which cannot run
      psycopg's async mode under Windows' default `ProactorEventLoop` — identical root
      cause `ui/run.py` already documented for the verification console; fixed the same
      way (a `SelectorEventLoop` factory on `win32`).
- [X] T064 [FRONTIER] Write the Phase 5 handoff: what was built, deviations from
      spec, interfaces published, open questions resolved and carried forward (per the
      working agreement's phase-handoff standard)
- [X] T067 [FRONTIER] [CRIT] Write a cross-tenant isolation test for entertainment (the
      one wholly new gateway server this feature adds): an MGV-scoped session cannot
      search, hold, or view MHB's events/orders and vice versa — proving `tenant_id`
      injection at the gateway boundary (`mcp/gateway/entertainment/server.py`, T023) is
      impossible to bypass, per Constitution Principle II ("proven by test rather than
      asserted") — never economy (tenant filter construction) — in
      `agents/tests/test_tenant_isolation.py`
- [X] T071 [FRONTIER] Write an integration test for quickstart.md Scenario 5 (ambiguous
      request → `Clarify` → resolved) and its own exhaustion path
      (`MAX_CLARIFICATION_COUNT` → `HumanHandoff`, `escalate_reason="clarification_bound"`)
      in `agents/tests/test_clarification_loop.py` — filed here rather than under a user
      story because FR-020/`Clarify` doesn't map to any of spec.md's five numbered stories

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — BLOCKS every user story
- **US1, US2 (Phase 3, 4)**: depend on Foundational; both P1, both required for the MVP
- **US3 (Phase 5)**: depends on Foundational (entertainment already built there); no
  dependency on US1/US2 beyond that shared foundation
- **US4 (Phase 6)**: depends on Foundational; builds stay + gaming itself (not needed
  earlier)
- **US5 (Phase 7)**: depends on Foundational (Planner bound + HumanHandoff already built
  there); the adversarial scenario benefits from US1–US4's domains existing so "every
  domain unavailable" is a meaningful test, but does not require their own test suites to
  pass first
- **Polish (Phase 8)**: depends on all five user stories

### Parallel Opportunities

- T001–T003 (Setup): all parallel
- T004–T006, T007, T010 (Foundational, independent files): parallel
- T022/T027, T028/T031 (entertainment/wellness scratch state + fakes): parallel with each
  other, not with their own domain's sequential build chain
- T044/T047/T049/T052 (US4's state + fakes files): parallel
- Once Foundational (Phase 2) completes: US3 (Phase 5) and US4 (Phase 6) can proceed fully
  in parallel with each other and with US1/US2, since neither depends on the other's
  domains

## Implementation Strategy

### MVP First

1. Phase 1 (Setup) → Phase 2 (Foundational — the largest phase, since the Supervisor
   itself and two of five domains are genuine shared prerequisites)
2. Phase 3 (US1) + Phase 4 (US2) — both P1; together these are the MVP and the feature's
   two headline exit criteria (three-domain success; partial-failure recovery)
3. **STOP and VALIDATE**: run quickstart.md Scenarios 1, 2, 6 against the MVP

### Incremental Delivery

4. Phase 5 (US3) and Phase 6 (US4) in parallel — every domain now has a real, mounted
   subgraph
5. Phase 7 (US5) — the safety-boundary story, meaningful once multiple domains exist to
   exhaust
6. Phase 8 (Polish) — eval generalization, the strict-mypy gate, the phase handoff doc

---

## Model Tier Summary

| Tier | Tasks | `[CRIT]` | Notes |
|---|---|---|---|
| FRONTIER | All 71 (T001–T071) | 29 (T004, T005, T006, T007, T008, T010, T015, T016, T017, T018, T019, T020, T023, T025, T029, T033, T035, T036, T039[^1], T040, T041, T045, T050, T055, T056, T060, T065, T067, T068) | Phase-level: this feature falls in `docs/models.md`'s declared frontier phase list ("Phases 0, 5, 6 and 7 run entirely on FRONTIER"). No task is tiered down regardless of how mechanical it looks (e.g. package `__init__.py` creation is still FRONTIER, just never `[CRIT]`). T065, T067, T068, T069, T070, T071 were added during `/speckit-analyze` remediation (F1, F2, F4, F7, F8) — see `docs/adr/026` and `docs/adr/027` for the two decisions that surfaced while closing those gaps. |
| MID | none | — | Whole-phase frontier overrides the column; nothing here runs on MID by design, not by oversight. |
| ECONOMY | none | — | Same as above — silence on ECONOMY is the declared phase-level reason (`docs/models.md`), not an unconsidered gap. |

[^1]: T039 itself is a test-writing task without inherent judgment risk; listed for
completeness of the CRIT count check but the primary CRIT weight in US2 sits on T040/T041.

### Verification

Run against `/speckit-analyze`: "Flag any task marked ECONOMY whose acceptance criteria
require a judgment not fully specified in plan.md. Flag any `[CRIT]` task not on
FRONTIER." Trivially satisfiable here — there are no ECONOMY tasks, and every `[CRIT]`
task is already FRONTIER by the whole-phase rule.

---

## Phase 9: Convergence

**Purpose**: `/speckit-converge`, run after `/speckit-implement` completed T001–T071 (69/71
done, T063 open pending a Docker-capable environment), found two gaps between spec.md's
functional requirements and the shipped code that no existing task tracks. Both were
already disclosed as intentional scope limitations in code docstrings
(`graph/supervisor.py`) and the Phase 5 handoff, but disclosure in a comment isn't
the same as a tracked follow-up — this phase closes that.

- [X] T072 [FRONTIER] [CRIT] Generalize `Recovery`'s semantic-alternative search
      (`graph/supervisor.py`'s `make_recovery_node`) beyond dining: on a commit failure in
      entertainment, wellness, or stay, re-run that domain's own discover/availability path
      for a nearby slot/venue/room and propose it, the same way T041 does for dining,
      before ever escalating with `escalate_reason="compensation_failed"` (FR-013, which is
      worded domain-agnostically — "the failed piece," not "the dining piece"; US2
      Acceptance Scenario 1 uses dining only as its illustrative example, not as a scope
      limit) — semantic compensation, never economy — depends on T020, T041
- [X] T073 [US2] [FRONTIER] Write a test forcing a STAY commit failure after
      entertainment/dining/wellness are already confirmed (stay commits last in the saga
      order, per research.md's ordering — the realistic case for "failure after other
      pieces already committed") and asserting `Recovery` proposes a real alternative room
      for that leg rather than escalating immediately, mirroring
      `test_partial_failure_recovery.py`'s dining case, in
      `agents/tests/test_partial_failure_recovery_nondining.py` — depends on T072
- [X] T074 [FRONTIER] Strengthen `CommitVerify` (`graph/supervisor.py`'s
      `make_commit_verify_node`, currently a structural no-op that trusts `Commit`'s own
      recorded outcome) to perform an independent vendor-side check per committed
      domain — re-querying each domain's own status/detail tool for the confirmation id
      before treating the commit as verified — so FR-012's "rather than assuming a
      commitment succeeded because the request to make it was sent" is satisfied by an
      actual second round-trip, not by trusting the first call's response — depends on T019

**Checkpoint**: FR-013 and FR-012 are satisfied uniformly across all four mutating
domains, not only dining.

**Implementation notes** (recorded post-hoc, since all three tasks landed together):

- T072: `graph/supervisor.py` gained `_dining_alternative`/`_entertainment_alternative`/
  `_wellness_alternative`/`_stay_alternative` plus an `_ALTERNATIVE_SEARCHERS` dispatch
  table; `make_recovery_node` now loops over every failed domain instead of hardcoding
  dining. Tested by `agents/tests/test_partial_failure_recovery_nondining.py` (T073),
  forcing a STAY failure after the other three domains commit.
- **Bonus fix, found while building T073's test**: combining wellness and stay (or any
  two domains whose own `hold_placement` unconditionally sets `awaiting_approval` —
  correct for their own standalone graphs) in the same `FanOut` plan crashed with
  LangGraph's `InvalidUpdateError` (`awaiting_approval` is a plain `LastValue` channel;
  two concurrent branches writing it in one superstep is invalid). Fixed by stripping
  `awaiting_approval`/`approval_scope` at `make_fanout_domain_node`'s own boundary —
  `ProposalAssembly` is FanOut's single, correct owner of that decision. This was a
  real, previously-untested defect: no prior test combined wellness+stay (or any two
  domains sharing this node shape) in one multi-domain plan.
- T074: `make_commit_verify_node` now takes `tools_factory` and re-queries
  `get_order_status(order_id=...)` for entertainment specifically — the one domain
  where `Confirmation.confirmation_id` is literally the vendor's `order_id`. A mismatch
  escalates directly to `HUMAN_HANDOFF` (bypassing `Recovery`) rather than attempting an
  automated alternative on top of an already-recorded, append-only `Confirmation` — see
  `graph/supervisor.py`'s module docstring for why dining/wellness/stay stay structural.
  Tested by `agents/tests/test_commit_verification.py`.
