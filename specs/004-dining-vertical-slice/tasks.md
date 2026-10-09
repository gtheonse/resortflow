# Phase 4 Tasks — Vertical Slice: Dining

## T045 – Local Scratch State (reuses frozen `ResortFlowState`/`graph.models`)
**Description:** DO NOT redefine state or dietary models — `graph.state.ResortFlowState` and
`graph.models.{Proposal, Hold, Confirmation, DietaryRequirement, DietaryRestriction,
DietarySeverity, Domain, SagaEvent, ToolFailure}` are frozen contracts from feature 001 and
must be imported, not duplicated. This task only adds the local scratch extension for
fields that don't belong in the shared 20-key state (e.g. raw LLM intent extraction before
it becomes a `Proposal`).
**Files:** `agents/dining/state.py`
**Acceptance:** A `TypedDict` scratch extension exists; imports confirm zero duplication of
frozen models

- [x] Define `IntentParsed` (scratch, local): venue_pref, party_size, date_time, cuisine, occasion
- [x] Import `DietaryRequirement`/`DietaryRestriction`/`DietarySeverity` from `graph.models` — do not redefine
- [x] Import `Proposal`, `Hold`, `Confirmation`, `SagaEvent`, `ToolFailure`, `Domain` from `graph.models`
- [x] Import `ResortFlowState` from `graph.state` as the graph's state type
- [x] Grep check: no new `class.*State` or `class.*Dietary` definitions outside this file's scratch types

---

## T046 – Intent Parser Node
**Description:** LLM node that parses free-text guest request into structured IntentParsed.
**Files:** `agents/dining/nodes/intent_parser.py`
**Acceptance:** Given sample requests, produces valid IntentParsed with correct fields

- [x] Implement intent_parser node: LLM call (gpt-4.1-mini) + structured output parsing into
      local scratch `IntentParsed`
- [x] Extract dietary statements into `list[graph.models.DietaryRequirement]` directly (not a
      separate free-text-only field) — this node is the one place free text becomes structured
- [x] Handle ambiguous requests (missing party size, date) → prompt for clarification
- [x] Unit test: 10 sample requests → correct `IntentParsed` + `DietaryRequirement` extraction

---

## T047 – Venue Discovery Node
**Description:** Query dining MCP list_venues tool, filter by intent constraints, emit `Proposal`.
**Files:** `agents/dining/nodes/venue_discovery.py`
**Acceptance:** Returns `list[graph.models.Proposal]` (domain=DINING) matching cuisine/occasion

- [x] Implement venue_discovery node: call MCP `list_venues` (or `list_venues_with_events`)
- [x] Filter results by scratch `IntentParsed.cuisine`, `occasion`
- [x] Construct `Proposal` objects (domain=`Domain.DINING`, tenant_id from identity,
      `dietary` populated from state) and write into `state["proposals"]`
- [x] Handle zero-match case: suggest broadening criteria
- [x] Test: verify correct MCP tool invocation with tenant_id from identity

---

## T048 – Availability Check Node
**Description:** Call MCP check_availability for the selected venue; refine the `Proposal`.
**Files:** `agents/dining/nodes/availability.py`
**Acceptance:** Updates the `Proposal`'s `starts_at`/`ends_at`, or signals no-availability

- [x] Implement availability node: call MCP `check_availability`
- [x] Handle multiple time slot options; select best match to intent
- [x] Handle no-availability: trigger waitlist path (join_waitlist tool)
- [x] Test: mock MCP response, verify correct parsing

---

## T049 – Allergen Fidelity Check Node (Policy-Critical)
**Description:** Cross-check the `Proposal.dietary` (`list[DietaryRequirement]`) against venue
menu content (Phase 3 RAG corpus). No separate "dietary capture" node — extraction already
happened in T046; this node is pure fidelity-checking logic.
**Files:** `agents/dining/nodes/allergen_check.py`
**Acceptance:** Detects conflicts; `ALLERGY_SEVERE`/`ALLERGY_MODERATE` blocks + sets
`awaiting_approval`; `PREFERENCE`/`INTOLERANCE` proceeds informationally

- [x] Query venue menu content from RAG corpus (Phase 3 dependency — stub `Chunk`/`Citation`
      lookups if Phase 3 not ready yet)
- [x] Scan menu items for keyword matches against each `DietaryRestriction` in
      `proposal.dietary`
- [x] On match with `severity in {ALLERGY_SEVERE, ALLERGY_MODERATE}`: set
      `state["awaiting_approval"] = True`, `approval_scope`, append `SagaEvent`
      (`kind="dietary_conflict_flagged"`)
- [x] Test: known allergen conflict (e.g., "nuts" in menu + guest `PEANUT`/`ALLERGY_SEVERE`) → blocked
- [x] Test: `PREFERENCE`/`INTOLERANCE` severity conflict → proceeds, informational only

---

## T050 – Hold Placement Node
**Description:** Call MCP hold_table tool with reservation details + dietary metadata; emit `Hold`.
**Files:** `agents/dining/nodes/hold_placement.py`
**Acceptance:** `Hold` (keyed by `idempotency_key`) written into `state["holds"]`; TTL respected

- [x] Implement hold_placement node: call MCP `hold_table`
- [x] Pass `proposal.dietary`, `party_size`, `starts_at` from the `Proposal`
- [x] Construct `graph.models.Hold` (status=`HoldStatus.HELD`, `expires_at` required) and
      write into `state["holds"]` — never invent a parallel `hold_id` field on state
- [x] Test: verify hold_table called with correct tenant_id, idempotency key

---

## T051 – Approval Interrupt (HITL)
**Description:** LangGraph interrupt() for dietary conflicts or high-value bookings.
**Files:** `agents/dining/edges/conditions.py`
**Acceptance:** Graph pauses on conflict; resumes correctly after human input

- [x] Implement conditional edge: `state["awaiting_approval"]` True → `interrupt()`
- [x] Implement conditional edge: no conflicts → proceed directly to confirm
- [x] Test: simulate interrupt + resume with approval/rejection
- [x] Test: rejection path releases hold (calls release_hold, `Hold.status` → `RELEASED`)

---

## T052 – Confirm Reservation Node
**Description:** Call MCP confirm_reservation to finalize booking; emit `Confirmation`.
**Files:** `agents/dining/nodes/confirm.py`
**Acceptance:** `Confirmation` appended to `state["confirmations"]`

- [x] Implement confirm node: call MCP `confirm_reservation` with `hold.hold_id`
- [x] Handle confirm failure (hold expired): classify via `graph.errors.classify()`;
      `HOLD_EXPIRED` → `REQUIRES_HUMAN` (retry hold_placement needs a fresh hold, not a
      silent retry)
- [x] Construct `graph.models.Confirmation` (idempotency_key → the promoted `Hold`) and
      append to `state["confirmations"]`
- [x] Test: end-to-end happy path produces valid `Confirmation`

---

## T053 – LangGraph StateGraph Assembly
**Description:** Wire all nodes into a StateGraph over `ResortFlowState` with conditional edges.
**Files:** `agents/dining/graph.py`
**Acceptance:** Full graph compiles; executes end-to-end for happy path

- [x] Define StateGraph with all 6 internal nodes (intent_parser, venue_discovery,
      availability, allergen_check, hold_placement, confirm) — internal names are free
      choice but must stay stable once chosen (Phase 5 mounts this subgraph unchanged)
- [x] Wire conditional edges (allergen conflict → interrupt, no availability → waitlist)
- [x] Add START/END nodes
- [x] Compile graph; verify structure (no orphan nodes, no cycles except intended)
- [x] Expose the compiled graph so feature 005's supervisor can mount it under the frozen
      `graph.nodes.DINING` name without modification

---

## T054 – Postgres Checkpointer Integration
**Description:** Wire Postgres checkpointer; verify state persisted after each node.
**Files:** `agents/dining/graph.py` (extend)
**Acceptance:** Checkpoints visible in Postgres table; resumable across process restart

- [x] Configure `langgraph-checkpoint-postgres` checkpointer (Postgres already in
      docker-compose.yml — core infra profile)
- [x] Wire checkpointer into `graph.compile(checkpointer=...)`
- [x] Apply `EncryptedSerializer` per AGENTS.md tech stack (checkpoints may carry
      `Sensitivity.PII`/`Sensitivity.HEALTH`-marked fields via `DietaryRequirement`)
- [x] Test: run graph partially, kill process, restart, verify resume from last checkpoint

---

## T055 – Resume-After-Restart Proof
**Description:** Explicit test proving cold-kill + resume correctness (Phase 4 exit criterion).
**Files:** `agents/dining/eval/test_resume.py`
**Acceptance:** Process killed mid-flow; restarted; completes correctly from checkpoint

- [x] Test scenario: kill process after hold_placement, before confirm
- [x] Restart: verify graph resumes at confirm node (not from START)
- [x] Verify: no duplicate hold created — `merge_holds` + idempotency_key dedup preserved
- [x] Verify: final `Confirmation` matches expected state
- [x] Per AGENTS.md hard rule: never trust `state["holds"]` on resume at face value —
      revalidate the held reservation against the vendor before confirming

---

## T056 – Langfuse Tracing Integration
**Description:** Wire Langfuse tracing for every node in the dining graph.
**Files:** `agents/dining/graph.py` (extend)
**Acceptance:** Full trace visible in Langfuse UI; each node has span with I/O

- [x] Add Langfuse callback handler to graph invocation (Langfuse already in docker-compose)
- [x] Verify: each node produces a span with inputs/outputs
- [x] Verify: LLM calls (intent parser) show token usage/cost
- [x] Test: run one full booking; confirm trace completeness in Langfuse

---

## T057 – 25-Case Eval Set
**Description:** Build eval set covering happy path, edge cases, failures.
**Files:** `agents/dining/eval/test_cases.yaml`, `agents/dining/eval/runner.py`
**Acceptance:** All 25 cases pass; results stored in results.json

- [x] Happy path (5 cases): straightforward booking, various venues/times
- [x] Dietary conflict cases (5 cases): `ALLERGY_SEVERE`/`ALLERGY_MODERATE` block,
      `PREFERENCE`/`INTOLERANCE` proceed informationally
- [x] Availability failure cases (5 cases): no slots → waitlist
- [x] Resume/interrupt cases (5 cases): kill/resume, approval/rejection
- [x] Edge cases (5 cases): ambiguous intent, malformed dietary input, hold expiry
- [x] Implement eval runner: execute all 25, assert expected outcomes
- [x] Store results in `results.json`

---

## Phase 4 Exit Criteria

- [x] Booking completes from cold start
- [x] Kill process mid-flow; resume correctly (Postgres checkpointer)
- [x] Allergen mismatch blocked (`ALLERGY_SEVERE`/`ALLERGY_MODERATE` conflict detection)
- [x] Langfuse tracing end-to-end
- [x] **25-case eval set passes**
