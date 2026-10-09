---

description: "Task list template for feature implementation"
---

# Tasks: Evaluation & Observability Gate

**Input**: Design documents from `/specs/007-eval-observability-gate/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md
(all present)

**Tests**: Not explicitly requested as a separate TDD phase in spec.md, but this phase's
own exit criteria are "measured, not asserted" by design (SC-001–SC-017 are all
mechanically verifiable), so test tasks are included inline within each story — omitting
them would leave the evaluator itself unverified, which is the one failure mode this
entire feature exists to prevent.

**Organization**: Tasks are grouped by user story (US1-US4, spec.md's own priorities:
US1/US2 = P1, US3 = P2, US4 = P3) to enable independent implementation and testing of
each story.

**Revision note (2026-09-14)**: This file was fully renumbered after `/speckit-analyze`
found nine issues (one CRITICAL — a self-contradictory cloud-agnostic requirement) and
the user approved remediation. Changes from the original T001-T044 version: T010's
judge client is now provider-agnostic rather than Azure-SDK-specific (I1); two new
contract-test tasks were inserted in Foundational (G4); one new task extends gaming
into trajectory scoring (G2); one new task implements nightly baseline-drift reporting
(G1); T016→T018 (report assembly) now also depends on the judge client (U1) and applies
PII masking (G3); the Model tier summary's CRIT count is corrected (I2); the budget
config file's location is now documented in plan.md (I3); and User Story 1's
Independent Test now states which tier a trajectory regression requires (U2).

## Format: `[ID] [P?] [Story] [TIER] [CRIT?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- **[TIER]**: Build-time model tier — `FRONTIER` / `MID` / `ECONOMY`. See `docs/models.md`.
- **[CRIT]**: Present when the task falls in the never-economy table in `docs/models.md`.
- Include exact file paths in descriptions

## Model tier assignment *(mandatory — ResortFlow project override)*

Required by constitution Principle VI (Declared Model Tier). A task with no tier is a
defect, not a default.

**The rule**, from `docs/models.md`:

> ECONOMY only where the spec fully determines the output. If executing the task requires a
> judgment call that isn't written down, it is not ECONOMY.

Its corollary is the reason this column earns its place: **if a task cannot be executed by
the cheap model, the spec wasn't specific enough.** Every forced bump to MID is a spec
defect found before implementation rather than during it.

### Rules

1. **`[CRIT]` forces `FRONTIER`**, regardless of how mechanical the task looks.
2. **Every `FRONTIER` task carries a one-line justification** in its description or an
   adjacent note.
3. **Whole-phase frontier overrides the column.** Phases 0, 5, 6 and 7 run entirely on
   FRONTIER per `docs/models.md`; in those features every row reads FRONTIER and the
   summary states the phase-level reason rather than repeating it per task.
4. **A task list with no `ECONOMY` rows MUST say why.**

**This task list's answer**: every task below reads `FRONTIER` because Phase 7 is a
declared whole-phase-frontier phase (`docs/models.md`: "Phases 0, 5, 6 and 7 run entirely
on FRONTIER. Don't tier them... The saving isn't worth the risk on concurrency, saga,
policy and eval code.") — not because each task was individually evaluated and happened
to need it. `[CRIT]` is additionally marked on tasks that fall in the named never-economy
category **"Eval metric implementation + judge prompts"** (`docs/models.md`: "A broken
evaluator makes every other metric decorative") — this has no tier consequence in this
phase (everything is FRONTIER regardless) but is recorded for audit precision, since
`[CRIT]` status persists as a fact about the task independent of which phase happens to
also be frontier-wide.

### Verification

```
Flag any task marked ECONOMY whose acceptance criteria require a judgment
not fully specified in plan.md. Flag any [CRIT] task not on FRONTIER.
```

## Path Conventions

Single project, existing track layout (`AGENTS.md`) — `eval/` (Track E, first real code),
`observability/`, `agents/`, `graph/`, `mcp/gateway/`, `.github/workflows/`,
`pyproject.toml` at repository root. No web/mobile path convention applies.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Stand up the empty `eval/` package and the new dependency every story needs.

- [X] T001 FRONTIER Create `eval/` package skeleton: `__init__.py` plus empty `judge.py`,
      `retrieval_scorer.py`, `trajectory_scorer.py`, `saga_suite.py`, `cost_latency.py`,
      `coverage.py`, `baseline.py`, `gate.py`, `drift.py`, `report.py`, `runner.py`,
      `cli.py`, and `eval/tests/`, `eval/data/`, `eval/data/reports/` directories
      (Track E's first code since feature 001; plan.md Project Structure)
- [X] T002 [P] FRONTIER Add `opentelemetry-exporter-otlp` to `pyproject.toml`; add a
      comment recording that `ragas`/`deepeval` are deliberately not added (ADR-043,
      research.md R4)
- [X] T003 [P] FRONTIER Create `.github/workflows/` directory — this repository's first CI
      (ADR-046); actual workflow files are added in later story tasks

**Checkpoint**: Package skeleton and dependency exist — Foundational work can begin.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared telemetry, judge-model, gate-computation, and report machinery
every user story's scoring depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T004 FRONTIER Implement `observability/telemetry.py` — `start_agent_span()`/
      `set_payload()`, dual `gen_ai.*`/`langfuse.*` namespace emission, PII masking via
      the existing `observability/redaction.py`, exporter target selected from
      `OTEL_EXPORTER_OTLP_ENDPOINT` (standard OTel env var, no vendor SDK import) per
      `contracts/telemetry-module-contract.md` (ADR-041)
- [X] T005 [P] FRONTIER Convert `agents/dining/tracing.py` into a thin wrapper calling
      `observability/telemetry.py` (ADR-041) — depends on T004
- [X] T006 [P] FRONTIER Convert `mcp/gateway/_shared/tracing.py` into a thin wrapper
      calling `observability/telemetry.py` (ADR-041) — depends on T004
- [X] T007 [P] FRONTIER Add first-ever span emission to `agents/entertainment/`,
      `agents/stay/`, `agents/wellness/`, `agents/gaming/` node wrappers via
      `observability/telemetry.py`, closing the constitution's previously-unmet "every
      node emits" requirement (ADR-041) — depends on T004
- [X] T008 FRONTIER Add first-ever supervisor-level span emission to
      `graph/supervisor.py` via `observability/telemetry.py` (ADR-041) — depends on T004
- [X] T009 FRONTIER Implement the Conversation Record projection (data-model.md) —
      built from the existing checkpointer plus T004-T008's span data, as a read-oriented
      view, not a second mutable state store (Constitution Principle V, ADR-045) —
      depends on T004-T008
- [X] T010 [P] FRONTIER Add contract-conformance tests for `observability/telemetry.py`
      in `eval/tests/test_telemetry.py`, asserting `contracts/telemetry-module-contract.md`'s
      guarantees: both namespaces always emitted together, PII masking applied before any
      attribute is set, and `gen_ai.system` is never a hardcoded literal — depends on T004
- [X] T011 FRONTIER [CRIT] Implement `eval/judge.py` — a minimal, **provider-agnostic**
      LLM client (an OpenAI-compatible chat-completions interface; provider, endpoint,
      authentication, and the pinned `MODEL_JUDGE` deployment all selected via
      configuration; never the Azure OpenAI SDK called directly). Azure OpenAI is the
      only provider actually configured in this build, but switching providers later
      MUST require no code change (FR-009, FR-013a, ADR-044 amended — closes ADR-039's
      deferred "LLM-provider abstraction" Tier-1 finding for this one client) — Eval
      metric implementation
- [X] T012 FRONTIER Create `eval/data/` initial state: empty `baseline.json`, empty
      `exemptions.yaml` per `contracts/coverage-exemption-schema.md`'s shape, empty
      `reports/` (ADR-045)
- [X] T013 FRONTIER [CRIT] Implement `eval/coverage.py`'s detection logic: decorator/
      regex scan of the diff for `@register_tool(..., entitlement_action="commit"|
      "hold")`, path glob for `content/corpus/{tenant}/*.yaml`, and path/function-boundary
      heuristics for `graph/supervisor.py` (Domain enum, `_SAGA_COMMIT_ORDER`,
      `DEFAULT_TOOLS_FACTORY`, `recovery()`) and `policy/{gaming,dietary,injection,
      distress,jurisdiction}.py` (FR-009a, ADR-040) — Eval metric implementation
- [X] T014 FRONTIER [CRIT] Extend `eval/coverage.py` with the Coverage Exemption
      lifecycle: tiered expiry windows (7d/14d/30d), early-clear only on coverage gate
      pass (never on issue close), void-on-expiry with no grace period, 3rd-request
      `adr_ref` requirement (FR-025-029, `contracts/coverage-exemption-schema.md`) —
      Eval metric implementation — depends on T013
- [X] T015 [P] FRONTIER Add contract-conformance tests for `eval/coverage.py`'s exemption
      rules in `eval/tests/test_coverage_exemptions.py`: expiry-window math per tier, a
      stale `active` entry past `expires_at` treated as `void` at read time regardless of
      the stored value, and a 3rd request without `adr_ref` rejected — depends on T014
- [X] T016 FRONTIER Implement `eval/baseline.py` — load/store, COMPLETE-only eligibility,
      per-metric mean and stdev persistence, `BASELINE_INVALID` on judge-model mismatch
      (FR-007c, ADR-042, ADR-045) — depends on T011, T012
- [X] T017 FRONTIER [CRIT] Implement `eval/gate.py` — the binary/statistical gate
      combinator: 3×-repeat variance-adjusted (mean − 1σ) absolute-floor check kept
      statistically separate from the raw-mean baseline-delta check with
      `max(fixed %, 2 × σ_baseline)` margin, and the full Run Verdict computation per
      `contracts/eval-report-schema.md`'s algorithm (FR-007/007a/007b/007c, ADR-042) —
      Eval metric implementation, "a broken evaluator makes every other metric
      decorative" — depends on T016
- [X] T018 FRONTIER Implement `eval/report.py` — consolidated report assembly matching
      `contracts/eval-report-schema.md` exactly (all fields, including the top-level
      `judge_model` identity, `coverage`, `active_exemptions`, `not_evaluated_cases`,
      `baseline_status`), applying `observability/redaction.py` masking to any
      conversation-derived text before assembly (FR-018) — depends on T011, T013, T014,
      T017
- [X] T019 FRONTIER Implement `eval/runner.py`'s tier-dispatch skeleton —
      `python -m eval.runner --tier pr|merge_queue|nightly`, wiring judge/coverage/
      baseline/gate/report together as the one command both CI and laptops call
      (FR-006/019) — depends on T011, T013, T016, T017, T018

**Checkpoint**: Foundation ready — user story implementation can now begin.

---

## Phase 3: User Story 1 - Automated regression gate blocks a bad merge (Priority: P1) 🎯 MVP

**Goal**: A fast PR-tier check and a fuller merge-queue-tier check, both wired into CI,
that block a merge when retrieval quality, agent trajectory, or transactional
correctness measurably regresses.

**Independent Test**: Deliberately degrade a prompt or retrieval setting on a branch,
run the suite, and confirm the merge gate fails with the regressed metric and its judge
model named in the report. A trajectory-specific regression requires running the
merge-queue tier, not the PR tier, since the PR tier does not score agent trajectory
(spec.md Assumptions).

### Implementation for User Story 1

- [X] T020 [P] [US1] FRONTIER [CRIT] Implement `eval/retrieval_scorer.py` — hand-rolled
      judge prompts for context recall, context precision, faithfulness, and answer
      relevance, called through `eval/judge.py` (FR-001, ADR-043) — Eval metric
      implementation + judge prompts — depends on T011
- [X] T021 [P] [US1] FRONTIER [CRIT] Implement `eval/trajectory_scorer.py` — deterministic
      tool-selection and unnecessary-step-rate scoring via sequence diffing against a
      reference trace; the judge model (via `eval/judge.py`) is used only for "plan
      quality" cases with more than one valid reference path (FR-002, ADR-043) — Eval
      metric implementation + judge prompts — depends on T011
- [X] T022 [US1] FRONTIER Extend the existing `agents/{dining,entertainment,stay,
      wellness}/eval/runner.py` domain runners to additionally feed
      `eval/trajectory_scorer.py` rather than only their current local boolean
      assertions (research.md R1) — depends on T021
- [X] T023 [US1] FRONTIER [CRIT] Extend `agents/gaming/eval/adversarial/runner.py` to
      additionally feed `eval/trajectory_scorer.py` for tool-selection/unnecessary-step
      scoring on its existing turn set — gaming has no `eval/runner.py` of the other
      four domains' shape, but this closes SC-001's "all five agent domains" requirement
      without inventing a parallel runner (FR-002, spec.md Assumptions) — Eval metric
      implementation — depends on T021
- [X] T024 [US1] FRONTIER Wire the PR-tier scope into `eval/runner.py`: all binary gates,
      a stratified single-run retrieval-quality subset, saga permutations scoped only to
      domains the change's coverage requirement touches, and a fixed representative
      cost/latency trace set (FR-019 PR tier) — depends on T013, T017, T020, T022, T023
- [X] T025 [US1] FRONTIER Wire the merge-queue-tier scope into `eval/runner.py`: the full
      retrieval-quality eval set at 3×-repeated variance-adjusted threshold, the full
      agent-trajectory eval set, and the full multi-turn adversarial script (FR-019
      merge-queue tier) — depends on T024; **the full saga-permutation sweep component
      of this same tier depends on US2's T031 (`eval/saga_suite.py`)** — sequence US2's
      saga suite before finalizing this task's saga-sweep wiring
- [X] T026 [US1] FRONTIER [CRIT] Implement dependency-unavailability handling in
      `eval/runner.py`/`eval/gate.py`: bounded retry (2 additional attempts, backoff,
      total added time capped against the PR tier's budget), `NOT_EVALUATED` case
      marking, `INCOMPLETE` run verdict (never `PASS`), and the fault-injection-vs-
      genuine-outage discriminator using the existing `X-Sim-Fault` envelope-vs-
      transport-exception distinction (`sims/_shared/faults.py`) (FR-021-024) — Eval
      metric implementation (a broken discriminator makes the whole verdict
      untrustworthy) — depends on T019
- [X] T027 [US1] FRONTIER Implement per-turn latency optimizations in `eval/runner.py`:
      parallelize independent probes, fixed-seed cached simulator responses,
      pre-computed retrieval contexts where retrieval isn't under test, batched
      judge-model calls (FR-020) — depends on T024, T025
- [X] T028 [US1] FRONTIER Create `.github/workflows/pr-gate.yml` — `pull_request` trigger
      → `python -m eval.runner --tier pr`, `EVAL_INCOMPLETE_BLOCKS` unset per
      `contracts/ci-tier-triggers-contract.md` (ADR-046) — depends on T024
- [X] T029 [US1] FRONTIER Create `.github/workflows/merge-queue-gate.yml` —
      `merge_group` trigger → `python -m eval.runner --tier merge_queue`,
      `EVAL_INCOMPLETE_BLOCKS=true`, automated commit updating `eval/data/baseline.json`
      only on a COMPLETE verdict against `main` (FR-007c, ADR-045, ADR-046) — depends on
      T025, T016
- [X] T030 [P] [US1] FRONTIER Add unit tests for `eval/gate.py`'s verdict-computation
      logic in `eval/tests/test_gate.py`, exercising every branch of the
      `eval-report-schema.md` verdict algorithm with synthetic scores (binary fail,
      floor fail, delta fail, `BASELINE_INVALID`, `INCOMPLETE`, clean pass) — depends on
      T017

**Checkpoint**: A deliberately degraded change fails both the PR and merge-queue tiers,
with the regressed metric and its judge model named — User Story 1 is independently
testable via `quickstart.md` scenarios 1, 2, 6, 7, 10, 11, 12.

---

## Phase 4: User Story 2 - Saga correctness is proven, not assumed (Priority: P1)

**Goal**: Every hold/confirm/compensate failure permutation across dining, stay, and
entertainment is exercised and individually reported, not collapsed into one pass/fail
line.

**Independent Test**: Run the saga suite alone against the three transactional domains
and confirm every defined failure-and-recovery permutation is exercised and reported
individually.

### Implementation for User Story 2

- [X] T031 [US2] FRONTIER [CRIT] Implement `eval/saga_suite.py` — a named Saga Failure
      Case registry (domain, failing step, expected outcome, urgency tier per
      data-model.md) mapping each case to the existing pytest test(s) that already
      exercise it (`agents/tests/test_partial_failure_recovery*.py`,
      `test_commit_verification.py`, `test_conflict_detection.py`, and per-domain
      `test_graph.py` files) rather than rewriting them (research.md R9) — Eval metric
      implementation
- [X] T032 [US2] FRONTIER Audit the registry built in T031 against the full domain ×
      failing-step matrix (dining/stay/entertainment × hold/confirm/compensate/none) and
      add any missing case + backing test the audit surfaces — depends on T031
- [X] T033 [US2] FRONTIER [CRIT] Wire `eval/saga_suite.py`'s results into
      `eval/report.py`'s `saga_case_results` field, one entry per permutation, never
      collapsed (SC-003) — Eval metric implementation — depends on T031, T018
- [X] T034 [US2] FRONTIER [CRIT] Add saga correctness to `eval/gate.py`'s binary-gate set
      — any single Saga Failure Case failure blocks the merge outright, zero tolerance
      (FR-007 binary gate) — Eval metric implementation — depends on T031, T017
- [X] T035 [P] [US2] FRONTIER Add unit tests for `eval/saga_suite.py`'s registry-to-test
      mapping in `eval/tests/test_saga_suite.py`, confirming every registered case
      actually maps to a passing backing test — depends on T032

**Checkpoint**: Every saga permutation across the three transactional domains is
individually visible in the report and any single failure blocks merge — User Story 2 is
independently testable via `quickstart.md` scenario 3, without needing US1's CI wiring.

---

## Phase 5: User Story 3 - An engineer diagnoses a single guest conversation end-to-end (Priority: P2)

**Goal**: A command-line view of any conversation's full record — inputs, decisions,
tool calls, retrieved context, output, cost, latency — in chronological order, sourced
from the same record the eval suite scores against.

**Independent Test**: Take a known conversation, retrieve its end-to-end record via the
CLI, and confirm every turn is present and in order — without running the evaluation
suite at all.

### Implementation for User Story 3

- [X] T036 [US3] FRONTIER Implement `eval/cli.py`'s `show-conversation --id <id>` command,
      rendering the Conversation Record (T009) chronologically (FR-011, quickstart.md
      scenario 4) — depends on T009
- [X] T037 [US3] FRONTIER Apply `observability/redaction.py`'s existing
      `redact_for_trace`/masking at the CLI read path before any conversation record is
      printed — never denied outright, never unmasked (FR-018) — depends on T036
- [X] T038 [P] [US3] FRONTIER Add an integration test in
      `eval/tests/test_conversation_record.py` confirming the CLI's record and the eval
      suite's scored record (T009, consumed by T020/T021) are the same underlying object,
      not a separately maintained log (FR-012) — depends on T036

**Checkpoint**: Any conversation's full record is inspectable via one CLI command,
proven to be the same record the gate scores against — User Story 3 is independently
testable via `quickstart.md` scenario 4, without running US1's or US2's suites.

---

## Phase 6: User Story 4 - Cost and latency stay within budget (Priority: P3)

**Goal**: Per-step cost and latency compared against an assigned budget, with every
exceedance individually flagged.

**Independent Test**: Run a conversation whose cost or latency deliberately exceeds a
defined budget for one step and confirm the report flags that step specifically,
independent of whether correctness scores pass.

### Implementation for User Story 4

- [X] T039 [US4] FRONTIER Implement `eval/cost_latency.py` — per-step cost/latency
      recording and comparison against assigned budgets (FR-004/005, data-model.md
      Cost/Latency Budget)
- [X] T040 [P] [US4] FRONTIER Add budget configuration fixtures at
      `eval/data/budgets.yaml` (planning-level threshold values per spec.md
      Assumptions — exact numbers are a planning parameter, not a spec-level decision;
      location now documented in plan.md's Project Structure)
- [X] T041 [US4] FRONTIER Wire `eval/cost_latency.py`'s results into
      `eval/report.py`'s `budget_flags` field and add cost/latency to `eval/gate.py`'s
      statistical-gate category (FR-007 statistical gate) — depends on T039, T018, T017
- [X] T042 [P] [US4] FRONTIER Add unit tests for budget-exceedance flagging in
      `eval/tests/test_cost_latency.py` — depends on T039

**Checkpoint**: A budget exceedance is flagged by step name; a compliant conversation
raises no flag — User Story 4 is independently testable via `quickstart.md` scenario 5.

---

## Phase 7: Polish & Cross-Cutting Concerns

**Purpose**: The non-blocking nightly tier (including its baseline-drift reporting
logic), documentation corrections research.md surfaced, and closing two items Phase 6
explicitly deferred to this phase.

- [X] T043 FRONTIER [CRIT] Implement `eval/drift.py` — multi-run baseline-drift
      detection across historical `eval/data/reports/`, the capability ADR-042's
      Consequences explicitly assigns to the nightly tier and that no per-run delta
      check (T017) attempts (FR-019 nightly bullet) — Eval metric implementation —
      depends on T016, T019
- [X] T044 [P] FRONTIER Create `.github/workflows/nightly.yml` — `schedule` trigger →
      `python -m eval.runner --tier nightly` (red-team/adversarial-style probing and
      T043's baseline-drift reporting, never blocking a merge) (FR-019 nightly tier,
      ADR-046) — depends on T019, T043
- [X] T045 FRONTIER Run every `quickstart.md` scenario (1-12) end-to-end and record
      results in the Phase 7 handoff
- [X] T046 [P] FRONTIER Correct `docs/ResortFlow-Observability-Tracing-Deep-Dive.md`'s stale
      `resortflow/observability/telemetry.py` path reference to the actual
      `observability/telemetry.py` (research.md R3 discrepancy)
- [X] T047 FRONTIER Confirm `docs/ResortFlow-Cloud-Portability-Review.md`'s Tier-1
      `gen_ai.system` finding is closed by T004's config-driven implementation (never a
      literal `"az.ai.openai"`), and additionally record that its "missing
      LLM-provider abstraction" Tier-1 finding is now closed **for the judge client
      specifically** by T011 (ADR-044 amended) — update that review document's Tier-1
      list accordingly — depends on T004, T011
- [ ] T048 FRONTIER Re-run the 40-turn adversarial script
      (`agents/gaming/eval/adversarial/`) end-to-end using the new `eval/` harness,
      closing the verification item Phase 6 explicitly deferred to Phase 7
      (the Phase 6 handoff) — confirm per-turn latency has collapsed from the
      previously observed 0.05s→186s+ blowup, per T027's optimizations — depends on
      T025, T027

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS every user story below.
- **User Stories (Phase 3-6)**: All depend on Foundational completion. US1 and US2 are
  both P1/MVP and have no dependency on each other for their own independent
  testability — they may proceed in parallel. The one real cross-story link is US1's
  T025 (merge-queue tier's full saga sweep), which needs US2's T031
  (`eval/saga_suite.py`) to exist — everything else in US1 (PR tier, retrieval/
  trajectory scoring including gaming, INCOMPLETE handling, CI wiring) does not depend
  on US2. US3 (P2) and US4 (P3) depend only on Foundational, not on US1/US2.
- **Polish (Phase 7)**: T043 depends on Foundational only (T016, T019); T044 depends on
  T043; T047/T048 depend on US1's telemetry (T004, T011) and CI/optimization work
  (T025, T027) respectively.

### Within Each User Story

- Scorer/registry module → wiring into `eval/gate.py`/`eval/report.py` → CI/CLI surface
  → tests confirming the wiring.

### Parallel Opportunities

- All Setup tasks marked [P] run in parallel.
- Within Foundational, T005/T006/T007 (the three tracer-conversion/first-span tasks) run
  in parallel once T004 lands; T010 (telemetry contract test) runs in parallel with
  T005-T009 once T004 lands; T013/T014's coverage work is independent of T011's judge
  client; T015 (coverage contract test) runs once T014 lands.
- US1 and US2 (both P1) can be staffed in parallel — `eval/retrieval_scorer.py`/
  `eval/trajectory_scorer.py` and `eval/saga_suite.py` touch disjoint files, aside from
  the single noted T025→T031 sequencing point.
- US3 and US4 (P2/P3) can each be staffed in parallel with US1/US2 and with each other —
  `eval/cli.py` and `eval/cost_latency.py` share no files.
- T020/T021 (US1) and T035 (US2, once T032 lands) marked [P] run in parallel.

---

## Parallel Example: User Story 1

```bash
# Once Foundational (T004-T019) lands, these run in parallel:
Task: "Implement eval/retrieval_scorer.py per FR-001/ADR-043"
Task: "Implement eval/trajectory_scorer.py per FR-002/ADR-043"

# Independently, once US2's saga suite (T031) exists:
Task: "Wire the merge-queue tier's full saga sweep into eval/runner.py (T025)"
```

---

## Implementation Strategy

### MVP First (User Stories 1 and 2)

1. Complete Phase 1: Setup.
2. Complete Phase 2: Foundational (CRITICAL — blocks everything below).
3. Complete Phase 3 (US1) and Phase 4 (US2) — both P1, together the MVP: a regressed
   change cannot merge, and a saga failure permutation cannot silently pass.
4. **STOP and VALIDATE**: run `quickstart.md` scenarios 1-3, 6, 7, 10, 11, 12
   independently.

### Incremental Delivery

1. Setup + Foundational → foundation ready.
2. US1 + US2 (parallel, with the one T025→T031 sequencing point) → MVP → validate
   independently → demo (`quickstart.md` 1-3).
3. US3 → validate independently → demo (`quickstart.md` 4).
4. US4 → validate independently → demo (`quickstart.md` 5).
5. Polish → nightly tier live (including drift reporting), deferred Phase 6
   verification items closed, `make eval` remains the CI gate per the constitution's
   existing Development Workflow standard.

### Parallel Team Strategy

With multiple developers: one pair takes US1 (retrieval/trajectory scoring including
gaming, CI wiring, INCOMPLETE handling — the largest story); a second takes US2 (saga
suite formalization); a third takes US3+US4 in sequence (both small, independent of
US1/US2's internals beyond Foundational).

---

## Model tier summary

| Tier | Tasks | `[CRIT]` | Notes |
|---|---|---|---|
| FRONTIER | 48 (all) | 12 (T011, T013, T014, T017, T020, T021, T023, T026, T031, T033, T034, T043) | Whole-phase override per `docs/models.md` ("Phases 0, 5, 6 and 7 run entirely on FRONTIER") — every task reads FRONTIER regardless of individual complexity. `[CRIT]` is marked separately, for audit precision, on tasks implementing the evaluator's actual scoring/gating/coverage/drift logic ("Eval metric implementation + judge prompts") — it carries no additional tier consequence here since the whole phase is already FRONTIER. |
| MID | 0 | — | N/A — whole-phase frontier override applies |
| ECONOMY | 0 | — | None — see the whole-phase override above; no task in this feature was individually evaluated as economy-eligible, because the phase-level rule makes that evaluation moot |
