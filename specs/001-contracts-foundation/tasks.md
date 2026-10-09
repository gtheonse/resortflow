---

description: "Task list for feature: contracts-foundation"
---

# Tasks: Contracts Foundation

**Input**: Design documents from `/specs/001-contracts-foundation/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md — all present.

**Tests**: Included. `plan.md`'s exit criteria require executed `pytest`/`mypy` results, and
AGENTS.md's working agreement states "Tests before the next phase starts."

**Organization**: Grouped by user story so each of the three stories in `spec.md` stays
independently testable.

## Format: `[ID] [P?] [Story] [TIER] [CRIT?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: US1, US2, or US3 — omitted for Setup, Foundational, and Polish
- **[TIER]**: Build-time model tier — see the Model Tier Assignment section below
- **[CRIT]**: Falls in the never-economy table in `docs/models.md`

## Model Tier Assignment

**Every task in this feature reads `FRONTIER`.** This is not a degenerate case of the
tiering system — it is the whole-phase rule stated explicitly in `docs/models.md`:

> Phases 0, 5, 6 and 7 run entirely on FRONTIER. Don't tier them. The saving isn't worth the
> risk on concurrency, saga, policy and eval code. In Phase 0 specifically, reducers and the
> hold-ledger recovery paths are the two things in this project that fail silently, and both
> live there.

So per the override's rule 4 ("a task list with no ECONOMY rows MUST say why"): **there are
zero MID and zero ECONOMY tasks in this file, because this feature is Phase 0.** `[CRIT]` is
still marked per task, distinct from tier, to show *which* tasks would be non-negotiably
FRONTIER even outside a whole-phase override — 18 of 33 tasks meet that bar independently
(state reducers, error taxonomy/classification, ledger recovery, idempotency key derivation,
the frozen-registry contract tests, and all ADRs). The remaining 15 are FRONTIER only
because of the whole-phase rule; each carries a one-line reason in place of a `[CRIT]` tag.

See the Tier Summary at the end of this document.

## Path Conventions

Single Python package at the repository root — `graph/`, `observability/`, `tests/`,
`infra/sql/`, `docs/`. No `src/` tree; matches the existing track-ownership layout in
`AGENTS.md` and `plan.md`'s Structure Decision.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: project scaffolding. No design decisions — every value is already fixed by
`plan.md`.

- [X] T001 [P] [FRONTIER] *(whole-phase; mechanical scaffolding)* Create package skeletons
  `graph/__init__.py`, `observability/__init__.py`, `tests/__init__.py`
- [X] T002 [P] [FRONTIER] *(whole-phase)* Create `pyproject.toml` at repo root — `[project]`
  name `resortflow`, `requires-python>=3.11`, deps `langgraph`, `langgraph-checkpoint-postgres`,
  `pydantic>=2`; `[project.optional-dependencies].dev` = `mypy`, `pytest`, `ruff`,
  `openapi-spec-validator`, `pyyaml`; `[tool.mypy]` strict scoped to `graph`,
  `observability`; `[tool.ruff]` line-length 100 — per `plan.md` Technical Context
- [X] T003 [P] [FRONTIER] *(whole-phase)* Create `docker-compose.yml` at repo root —
  services only per `plan.md` §11 topology (qdrant, postgres, redis, langfuse+clickhouse,
  azurite, servicebus-emulator, jwt-issuer, edge-shim, five sims, mcp-gateway,
  resortflow-orchestrator); sims and app services behind `profiles: [sims, app]` so
  `docker compose config` validates against infrastructure that exists today
- [X] T004 [P] [FRONTIER] *(whole-phase)* Create `Makefile` at repo root — `up`, `seed`,
  `eval`, `demo` targets per `AGENTS.md` Commands; `seed`/`eval`/`demo` echo the phase that
  implements them
- [X] T005 [P] [FRONTIER] *(whole-phase; validates a frozen input, no code dependency)*
  Write `tests/test_openapi_lint.py` — `openapi_spec_validator` over all five files in
  `docs/contracts/openapi/*.yaml` — satisfies the "OpenAPI specs lint clean" exit criterion

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the types every user story imports. **No story work starts before this phase
completes.**

- [X] T006 [P] [FRONTIER] [CRIT] Implement `graph/errors.py` — `ErrorClass` enum
  (`RETRYABLE | TERMINAL | REQUIRES_HUMAN | POLICY_BLOCKED`) and the exception hierarchy
  (`ResortFlowError` → `RetryableError`, `TerminalError`, `RequiresHumanError`,
  `PolicyBlockedError`, each carrying `vendor_code`, `http_status`, `domain`, `detail`,
  `retry_after`). No internal imports. **`classify()` itself is implemented in T019 (US3)**,
  where its test lives — FR-013
- [X] T007 [P] [FRONTIER] *(whole-phase; names only, no behaviour — see plan.md deviation
  note)* Implement `graph/nodes.py` — the 21-entry graph node-name registry transcribed
  from `data-model.md`'s Graph Node Name Registry, as string constants — FR-024
- [X] T008 [P] [FRONTIER] *(whole-phase; names only)* Implement `observability/attributes.py`
  — the five identifiers, three namespace prefixes, and cardinality rules from
  `contracts/telemetry-attributes.md`, as constants — FR-022
- [X] T009 [FRONTIER] *(whole-phase; Pydantic models from a written contract — ECONOMY-shaped
  work, FRONTIER only because of the Phase 0 override)* Implement `graph/models.py` — the 11
  domain entities and enums (`DietaryRestriction`, `DietarySeverity`, `Domain`,
  `HoldStatus`) transcribed verbatim from `data-model.md` and the frozen OpenAPI specs;
  `extra="forbid"`; PII/HEALTH sensitivity via `Annotated` field metadata. Depends on T006
  for `ErrorClass` (used by `ToolFailure.error_class`) — FR-009, FR-010, FR-023

**Checkpoint**: `graph/errors.py`, `graph/nodes.py`, `observability/attributes.py`,
`graph/models.py` all import cleanly. User story work can begin.

---

## Phase 3: User Story 1 — Parallel domain results survive concurrent writes (Priority: P1) 🎯 MVP

**Goal**: `ResortFlowState`'s channels merge concurrent contributions without loss.

**Independent Test**: three simulated writers contribute to `proposals`/`holds` in one step,
with no agent, tool, or vendor present; every contribution is retrievable afterward.

### Tests for User Story 1

> Write these first; they fail until T012–T015 exist.

- [X] T010 [P] [US1] [FRONTIER] [CRIT] Write `tests/test_reducers.py` — concurrent-write
  scenario (3 writers, all 3 results retained, 100 repetitions per SC-001); `merge_holds`
  terminal-state stickiness (a confirmed hold is never regressed by a late `pending`/`held`
  write); replay-idempotence for all four reducers (apply an identical write twice, no
  change, per SC-002); first-write-on-empty acceptance — FR-002 through FR-008
- [X] T011 [P] [US1] [FRONTIER] [CRIT] Write `tests/test_state_contract.py` — asserts
  `ResortFlowState`'s 20-key frozen set literally, so a later feature editing the schema fails CI
  rather than drifting silently; also asserts `MAX_REPLAN_COUNT == 3` — FR-021, FR-012

### Implementation for User Story 1

*Sequential — all four tasks below edit `graph/state.py`, a frozen-contract file per the
working agreement's "contract changes go through a single integration commit" rule; also
[AGENTS.md](../../AGENTS.md) hard rule.*

- [X] T012 [US1] [FRONTIER] [CRIT] Implement `merge_proposals` in `graph/state.py` — merge
  by `proposal_id`, right wins on collision, new items appended preserving left order.
  Depends on T009 (models) — FR-002
- [X] T013 [US1] [FRONTIER] [CRIT] Implement `merge_holds` in `graph/state.py` — merge by
  `idempotency_key` (never `hold_id`, which is `None` until the vendor responds); terminal
  states (`confirmed`, `released`, `expired`, `orphan_released`) never regress to
  `pending`/`held`. Depends on T012 (same file) — FR-003, FR-004
- [X] T014 [US1] [FRONTIER] [CRIT] Implement `append_only` and `dedupe_by_id` in
  `graph/state.py` — concatenation for the former; first-occurrence-wins by `chunk_id`,
  preserving RRF rank order, for the latter. Depends on T013 (same file) — FR-005, FR-006
- [X] T015 [US1] [FRONTIER] [CRIT] Define the `ResortFlowState` `TypedDict` in `graph/state.py` —
  all 20 frozen keys bound via `Annotated[..., reducer]` per `contracts/state-schema.md`,
  including the `saga_id` deviation recorded in `plan.md`. **Also define
  `MAX_REPLAN_COUNT: Final[int] = 3`** alongside it per `research.md` item 7 — this closes
  the FR-012 gap the analyze pass found: the bound now exists as a frozen, importable value,
  not just a documented intention. The *enforcement* half (escalate on breach) has no caller
  yet — no planner exists until feature 005 — and is deferred, per the new Assumptions note
  in `spec.md`. Depends on T014 (same file) — FR-001, FR-007, FR-008, FR-011, FR-012

**Checkpoint**: T010 and T011 now pass. User Story 1 is independently functional.

---

## Phase 4: User Story 2 — External mutation intent survives a crash at any point (Priority: P2)

**Goal**: the write-ahead ledger records intent before an external call and outcome after,
joined by idempotency key, so a crash at any of the three windows in
`docs/ResortFlow-HITL-Durability-State.md` §3 is recoverable.

**Independent Test**: simulate an interruption at each of the three crash windows directly
against the DDL, with no external system involved.

- [X] T016 [P] [US2] [FRONTIER] [CRIT] Write `tests/test_ledger.py` — the `UNIQUE` constraint
  on `idempotency_key` rejects a duplicate; the `status` `CHECK` constraint rejects an
  invalid value; the `HELD`-requires-`expires_at` constraint holds; an orphan-candidate
  query (rows in `PENDING`/`HELD` with no active session) returns the expected set. Requires
  Postgres — skip cleanly (not fail) when unavailable, per `quickstart.md`'s Docker-optional
  note. Fails with "relation does not exist" until T017 lands — FR-017, FR-018, FR-019, SC-004
- [X] T017 [US2] [FRONTIER] [CRIT] Create `infra/sql/001_hold_ledger.sql` — the table,
  constraints, indexes, and `updated_at` trigger from
  `specs/001-contracts-foundation/contracts/hold-ledger.sql`, applied verbatim to its
  permanent home — FR-017, FR-018

**Checkpoint**: `docker compose up postgres`; apply the DDL; T016 passes. User Story 2 is
independently functional.

---

## Phase 5: User Story 3 — Every failure classifies into exactly one disposition (Priority: P3)

**Goal**: `classify()` resolves every enumerated failure to exactly one of the four
dispositions, totally and deterministically.

**Independent Test**: feed every code from `data-model.md`'s error-taxonomy mapping table and
the nine failure-matrix modes through `classify()`; assert totality and uniqueness.

- [X] T018 [P] [US3] [FRONTIER] [CRIT] Write `tests/test_errors.py` — every published vendor
  code (dining's 7, ticketing's 7) classifies correctly; every status-only case classifies
  correctly, **including the casino `403` responsible-gaming suppression → `POLICY_BLOCKED`,
  asserted never `RETRYABLE`**; the fallback rule (unknown+5xx→`RETRYABLE`,
  unknown+4xx→`TERMINAL`) holds; the nine system failure modes from
  `docs/ResortFlow-HITL-Durability-State.md` §11 classify correctly — FR-013, FR-014, SC-003
- [X] T019 [US3] [FRONTIER] [CRIT] Implement `classify(vendor_code, http_status,
  retryable_hint) -> ErrorClass` in `graph/errors.py` — the explicit mapping table from
  `data-model.md`'s "Error taxonomy mapping" section. Depends on T006 (same file, extends
  the skeleton) — FR-013, FR-014

**Checkpoint**: T018 passes. All three user stories are independently functional.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: work that spans stories, plus the ADR and handoff obligations.

- [X] T020 [P] [FRONTIER] *(whole-phase; spans all three stories' output)* Write
  `tests/test_serialization.py` — every entity from `graph/models.py` and the assembled
  `ResortFlowState` round-trips through `JsonPlusSerializer` with no pickle-fallback type tag,
  and the values survive the round-trip unchanged (not just the absence of a pickle tag).
  **Also asserts PII/HEALTH field markers**: iterate the known-sensitive fields
  (`Guest.display_name`/`.phone`/`.email`, `DietaryRequirement.restriction`/`.severity`/
  `.detail`, `Hold.saga_id`-adjacent identifiers per `data-model.md`'s sensitivity table)
  and assert each carries its `Annotated` marker — closes the FR-023/SC-011 gap the analyze
  pass found (zero prior test proved 100% of sensitive fields were marked). Depends on T009,
  T015
- [X] T021 [P] [FRONTIER] *(whole-phase; a document, not a decision)* Create
  `docs/contracts/mcp-tool-contract.md` from
  `specs/001-contracts-foundation/contracts/mcp-tool-contract.md` at its permanent home —
  FR-015, FR-016
- [X] T022 [P] [FRONTIER] *(whole-phase)* Create `docs/contracts/schemas/tool-error.schema.json`
  from `specs/001-contracts-foundation/contracts/tool-error.schema.json` at its permanent
  home — FR-015
- [X] T023 [P] [FRONTIER] [CRIT] Write `docs/adr/001-supervisor-domain-subgraphs.md` —
  supervisor + domain subgraphs over a single ReAct agent
- [X] T024 [P] [FRONTIER] [CRIT] Write `docs/adr/002-langgraph-orchestration.md` — LangGraph
  over AutoGen / CrewAI / Semantic Kernel
- [X] T025 [P] [FRONTIER] [CRIT] Write `docs/adr/003-qdrant-vs-azure-ai-search.md` — Qdrant
  for build, Azure AI Search as the documented production target
- [X] T026 [P] [FRONTIER] [CRIT] Write `docs/adr/004-postgres-checkpointer.md` — Postgres
  checkpointer over Redis/in-memory in both environments (no Cosmos, per the decision taken
  earlier in this feature)
- [X] T027 [P] [FRONTIER] [CRIT] Write `docs/adr/005-mcp-sole-integration-surface.md` — MCP
  as the sole integration surface
- [X] T028 [P] [FRONTIER] [CRIT] Write `docs/adr/006-hold-then-confirm-saga.md` —
  hold-then-confirm over direct booking
- [X] T029 [P] [FRONTIER] [CRIT] Write `docs/adr/019-write-ahead-hold-ledger.md` — the
  write-ahead ledger outside the checkpointer (not in the original
  §15 register; added because it is Phase 0's most consequential decision and had none)
- [X] T030 [P] [FRONTIER] [CRIT] Write `tests/test_contract_registries.py` — asserts
  `graph/nodes.py`'s 21-entry node-name registry literally (analogous to T011's state-key
  guard, extended to nodes), including that the two reserved-but-unresolved names
  (`offer_agent`, `human_handoff`) are present; and asserts `observability/attributes.py`
  contains the five identifiers and three namespace prefixes specified in
  `contracts/telemetry-attributes.md`. Closes two gaps the analyze pass found: FR-024/
  FR-021/SC-012 had no frozen-set test (the state-key pattern in T011 was never extended to
  nodes), and FR-022/SC-010 had no content test for the telemetry module. Depends on T007,
  T008
- [X] T031 [P] [FRONTIER] *(whole-phase; one-line table edit)* Add the `observability/` row
  to the track ownership table in `AGENTS.md` — outstanding since the node-registry
  correction earlier in this feature
- [X] T032 [FRONTIER] *(whole-phase)* Write the Phase 0 handoff — what was built,
  deviations from spec (`saga_id`, `messages` windowing deferred, ADR-019 outside the
  register, FR-024 node registry, model-tiering scope added post-review, the analyze-pass
  remediations recorded below), interfaces published (module paths, the frozen `ResortFlowState`
  key set, the node registry), open questions resolved. Depends on T001–T031
- [X] T033 [FRONTIER] *(whole-phase)* Run the full verification suite from `quickstart.md` —
  `mypy --strict` on `graph`/`observability`, `pytest -q`, `ruff check`,
  `openapi-spec-validator`, `docker compose config -q`, the ledger DDL apply-and-inspect —
  and record actual results in the Phase 0 handoff. Depends on T032

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks all three user stories**
- **User Stories (Phases 3–5)**: all depend on Foundational; independent of each other —
  US1, US2, US3 can proceed in parallel if staffed, or in priority order P1→P2→P3
- **Polish (Phase 6)**: T020 depends on US1's and Foundational's models; T023–T029 (ADRs)
  are dependency-free text; T030 depends on T007, T008; T032 depends on everything; T033
  depends on T032

### Within Each Story

- US1: tests (T010, T011) before implementation; T012→T013→T014→T015 sequential (same file)
- US2: test (T016) before DDL (T017), though the DDL is what makes the test pass
- US3: test (T018) before the classifier body (T019)

### Parallel Opportunities

- All of Phase 1 ([T001–T005])
- T006, T007, T008 in Phase 2 (T009 depends on T006)
- T010, T011 (different test files)
- T016 alongside US1/US3 work (different story, no shared files)
- T018 alongside US1/US2 work
- T020–T030 in Phase 6 (ten independent files)

---

## Parallel Example: Foundational Phase

```bash
# Launch together — three different files, no dependencies among them:
Task: "Implement graph/errors.py — ErrorClass + exception hierarchy"
Task: "Implement graph/nodes.py — node-name registry"
Task: "Implement observability/attributes.py — telemetry constants"
# T009 (graph/models.py) waits on T006 only.
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 (Setup) → Phase 2 (Foundational) → Phase 3 (US1)
2. **Stop and validate**: `pytest tests/test_reducers.py tests/test_state_contract.py -v`
3. This alone proves the contract every downstream track needs most: concurrent writes don't
   lose data.

### Incremental Delivery

Setup + Foundational → US1 (MVP) → US2 → US3 → Polish. Each story adds value without
breaking the previous one; all three are needed before `/speckit-converge` can report this
feature Converged.

---

## Notes

- `[P]` tasks touch different files with no dependencies on incomplete tasks.
- `[Story]` maps a task to its user story for traceability; Setup/Foundational/Polish carry
  none by template rule.
- Every task in this file is `FRONTIER` — Phase 0 is a declared whole-phase-frontier phase
  per `docs/models.md`. `[CRIT]` is marked independently to show which tasks would demand
  FRONTIER even without that override.
- Run `/speckit-analyze`'s tier gate before implementation:
  `Flag any task marked ECONOMY whose acceptance criteria require a judgment not fully specified in plan.md. Flag any [CRIT] task not on FRONTIER.`
  — trivially satisfied here (zero ECONOMY tasks, all CRIT tasks are FRONTIER), but run it
  anyway; that is what makes the assignment checked rather than trusted.
- Commit after each phase checkpoint.
- Avoid: tier drift under delivery pressure — not applicable this feature (nothing to drift
  from), but the discipline starts now for features 002 onward.

---

## Tier Summary *(required by the tasks-template override)*

| Tier | Tasks | `[CRIT]` | Notes |
|---|---|---|---|
| FRONTIER | 33 (all) | 18 (T006, T010–T017, T019, T023–T030) | Whole-phase override — `docs/models.md`: "Phases 0, 5, 6 and 7 run entirely on FRONTIER." Reducers and hold-ledger recovery are this project's two silent-failure risks, and both are in this feature. |
| MID | 0 | — | None — see above. |
| ECONOMY | 0 | — | **Stated per rule 4**: zero, because this is a declared frontier phase, not because tiering wasn't considered. Several tasks here (T009 models-from-contract, T021/T022 doc placement, T003/T004 infra scaffolding) are ECONOMY-*shaped* work under `docs/models.md`'s own categories and will be tiered normally starting feature 002. |

### Remediation log (post-`/speckit-analyze`, 2026-09-09)

Eight findings from the first analyze pass; six required task changes, recorded here rather
than silently folded in:

- **I1** (HIGH, inconsistency) — `plan.md`/`quickstart.md` claimed five test modules;
  `test_ledger.py` (T016) made it six. Both docs corrected.
- **C1** (HIGH, coverage) — no frozen-set test existed for `graph/nodes.py`, unlike
  `ResortFlowState`'s T011. Closed by new **T030**.
- **C3** (MEDIUM, coverage) — no content test for `observability/attributes.py`. Folded into
  **T030** alongside C1, since both are "does the frozen module contain what the design doc
  says" checks.
- **C2** (MEDIUM, coverage) — no test proved PII/HEALTH markers were actually present.
  Folded into **T020**.
- **C4** (MEDIUM, coverage) — `replan_count`'s bound of 3 was documented intent with no
  artifact. `MAX_REPLAN_COUNT` added to **T015**, asserted in **T011**; the enforcement half
  is explicitly deferred in `spec.md` Assumptions rather than left silent.
- **C5, G1, T1** — spec-only fixes (Assumptions additions, one terminology rename); no task
  changes needed. See `spec.md` Assumptions and the renamed "Saga Event" entity.

Task count: 32 → 33. `[CRIT]` count: 17 → 18.
