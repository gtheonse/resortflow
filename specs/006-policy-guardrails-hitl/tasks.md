---

description: "Task list template for feature implementation"
---

# Tasks: Policy, Guardrails & HITL

**Input**: Design documents from `/specs/006-policy-guardrails-hitl/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md
(all present)

**Tests**: Not explicitly requested as a separate TDD phase in spec.md, but this phase's
own exit criteria are adversarial/property-based by nature (SC-001–SC-008), so test
tasks are included inline within each story rather than as a separate optional block —
omitting them would leave every safety-critical guarantee in this phase unverified.

**Organization**: Tasks are grouped by user story (US1-US6, spec.md's own priorities:
US1/US2 = P1, US3/US4/US5 = P2, US6 = P3) to enable independent implementation and
testing of each story.

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

**This task list's answer**: every task below reads `FRONTIER` because Phase 6 is a
declared whole-phase-frontier phase (`docs/models.md`: "Phases 0, 5, 6 and 7 run entirely
on FRONTIER. Don't tier them.") — not because each task was individually evaluated and
happened to need it. `[CRIT]` is additionally marked on tasks that fall in the
never-economy table by name (policy engine / responsible gaming / self-exclusion; allergen
fidelity check; state reducers) — this has no tier consequence in this phase (everything
is FRONTIER regardless) but is recorded for audit precision, since `[CRIT]` status persists
as a fact about the task independent of which phase happens to also be frontier-wide.

### Verification

```
Flag any task marked ECONOMY whose acceptance criteria require a judgment
not fully specified in plan.md. Flag any [CRIT] task not on FRONTIER.
```

## Path Conventions

Single project, existing track layout (`AGENTS.md`) — `policy/`, `graph/`, `agents/`,
`observability/`, `mcp/gateway/`, `content/`, `docker/`, `pyproject.toml` at repository
root. No web/mobile path convention applies.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Stand up the empty `policy/` package and the new dependencies every story
needs.

- [X] T001 FRONTIER Create `policy/` package skeleton: `__init__.py` plus empty
      `gaming.py`, `dietary.py`, `injection.py`, `distress.py`, `jurisdiction.py`,
      `types.py`, and a `policy/tests/` directory (Track G's first code since feature 001)
- [X] T002 [P] FRONTIER Add `presidio-analyzer`, `presidio-anonymizer`, `spacy`, and
      `hypothesis` (dev) to `pyproject.toml` (research.md, ADR-031, ADR-033)
- [BLOCKED] T003 [P] FRONTIER Vendor the `en_core_web_sm` spaCy model into the Docker
      image build in `docker/` (ADR-031) — **cannot be completed**: no root `Dockerfile`
      exists yet (`docker-compose.yml`'s `resortflow-orchestrator`/`hold-ledger-reaper` services
      reference `build: context: .` with no `Dockerfile` present at repo root — a
      pre-existing Track F gap, not created by this feature). Creating a root Dockerfile
      is a Track F decision (base image, multi-stage build conventions) outside this
      phase's scope to make unilaterally. For local dev, `python -m spacy download
      en_core_web_sm` was run directly (not vendored) so `policy/tests/` and manual
      testing work now. Flagged in the Phase 6 handoff as an open item for whoever
      builds the root Dockerfile: add `RUN python -m spacy download en_core_web_sm` (or
      vendor the wheel) at build time.

**Checkpoint**: `policy/` importable, new dependencies installed. Docker vendoring
deferred — see T003.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Shared state, node, and lookup machinery every user story below depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T004 FRONTIER [CRIT] Amend `graph/state.py`: `policy_flags` gains an `append_only`
      reducer — `Annotated[list[PolicyFlag], append_only]` (ADR-029,
      `contracts/state-schema-amendment-006.md`). CRIT: state reducer, per
      `docs/models.md`'s never-economy list.
- [X] T005 [P] FRONTIER Add `test_policy_flags_reducer` to `tests/test_reducers.py`,
      verifying the same three properties (accepts absent left, idempotent under replay,
      loses nothing from concurrent writers) already proven for
      `confirmations`/`failures`/`saga_log`
- [X] T006 [P] FRONTIER Implement `policy/types.py`: `GamingClearance`,
      `DietaryFidelityResult`, `InjectionScreeningResult`, `DistressCheck` (data-model.md)
- [X] T007 FRONTIER Implement `policy/jurisdiction.py`: `JURISDICTION_MESSAGES` static
      lookup table + `mandated_message_for(jurisdiction, trigger)` (ADR-034, FR-012)
- [X] T008 [P] FRONTIER Unit tests for `mandated_message_for` in
      `policy/tests/test_jurisdiction.py` — both tenants, both triggers, and the
      no-entry-configured case
- [X] T009 FRONTIER Give `graph/supervisor.py`'s `make_blocked_node` a block-reason-aware
      message (accepts the triggering `PolicyFlag`/`rule`, replacing today's one fixed
      string) — shared by US1 and US2 below. **Deviation**: reads `state["policy_flags"]`
      rather than an explicit parameter (LangGraph nodes take state, not extra args); in
      practice only `allergen_fidelity_mismatch`/`dependency_unavailable`/
      `injection_detected` reach this node — `self_exclusion_active` is handled inline by
      `agents/gaming/nodes/lookup.py` (it already returns `mandated_message` directly) and
      `distress_signal` routes to `HUMAN_HANDOFF`, never `BLOCKED`. See
      the Phase 6 handoff.
- [X] T010 FRONTIER Implement `make_policy_postflight_node` in `graph/supervisor.py`:
      applies `mandated_message_for` to any response about to reach the guest that
      contains gaming content (`contracts/graph-topology-v006.md`, SC-006). The `offer`
      trigger is applied by `OFFER_AGENT` itself (T043/US6), not here — this node has no
      view of an offer that hasn't been assembled yet.
- [X] T011 FRONTIER Mount `POLICY_POSTFLIGHT` into the supervisor graph's edges (reserved
      node name since feature 005 — no rename, no new name; `graph/nodes.py` unchanged).
      **Already done**: feature 005 already added the node and both edges
      (`RECOVERY`/`RELEASE` → `POLICY_POSTFLIGHT` → `RESPOND`) as part of its reserved
      no-op scaffolding — this task turned out to require no wiring change, only T010's
      real node body.

**Checkpoint**: Foundation ready — every user story below can now proceed.

---

## Phase 3: User Story 1 - Self-excluded guest never encounters gaming content or offers (Priority: P1) 🎯 MVP

**Goal**: Self-exclusion is checked before any gaming-domain action and before the Offer
Agent, every turn, regardless of topic; gambling-distress utterances route to a human
with no model judgment.

**Independent Test**: A 40-turn adversarial conversation against a self-excluded guest
profile shows zero gaming content, offers, or transactions at any point, while non-gaming
amenity requests in the same script still succeed (SC-001, SC-008).

### Implementation for User Story 1

- [X] T012 FRONTIER [CRIT] [US1] Implement `require_gaming_clear(tools, guest_id=...)` in
      `policy/gaming.py`, wrapping `GamingTools.check_rg_status` — returns
      `dependency_unavailable` on `GamingToolError` rather than raising
      (`contracts/policy-engine-contract.md`, ADR-030). CRIT: self-exclusion, never-economy.
- [X] T013 [P] [US1] FRONTIER [CRIT] `hypothesis` property tests for
      `require_gaming_clear` in `policy/tests/test_gaming.py` (for any
      `ResponsibleGamingStatus` with `self_excluded=True`, `permitted` is always `False`)
- [X] T014 [US1] FRONTIER [CRIT] Wire `require_gaming_clear` into
      `agents/gaming/nodes/lookup.py`'s dispatch, before all three intent branches — closes
      the concrete gap where `floor_games`/`loyalty_status` intents currently skip the
      check entirely
- [X] T015 [US1] FRONTIER [CRIT] On not-permitted, emit `PolicyFlag(rule=
      "self_exclusion_active", disposition="blocked")` (suppression case) or
      `PolicyFlag(rule="dependency_unavailable", disposition="blocked")` +
      `PolicyFlag(disposition="escalated")` (outage case, FR-021) from `lookup.py`
- [X] T016 [P] [US1] FRONTIER Extend `agents/gaming/tests/fakes.py`/`test_graph.py` fixture
      coverage so all three intents (not just `rg_status`) are tested under both
      `self_excluded` and `dependency_unavailable` conditions
- [X] T017 [US1] FRONTIER [CRIT] Implement `check_distress_signal(utterance)` in
      `policy/distress.py` — corpus/embedding-similarity match, ambiguous band included
      (ADR-036). CRIT: responsible-gaming policy engine, never-economy.
- [X] T018 [P] [US1] FRONTIER [CRIT] Corpus + `hypothesis` tests for
      `check_distress_signal` in `policy/tests/test_distress.py`, confirming the ambiguous
      band resolves to escalate, not "proceed normally" (FR-018)
- [X] T019 [US1] FRONTIER [CRIT] Wire `check_distress_signal` into
      `make_policy_preflight_node` (`graph/supervisor.py`), routing to `HUMAN_HANDOFF` on
      `flagged or ambiguous`, regardless of topic. **Deviation (significant)**: discovered
      during implementation that `POLICY_PREFLIGHT` was only reachable from the
      `SUPERVISOR` (multi-domain) branch — the `DETERMINISTIC` branch (~60% of traffic,
      Constitution Principle I) bypassed it entirely via `DETERMINISTIC -> END`. Moved
      `POLICY_PREFLIGHT` to sit between `ENTRY` and `IntentRouter`'s fork instead (runs
      once per turn, ahead of `CLARIFY`/`DETERMINISTIC`/`SUPERVISOR`), so distress
      screening actually applies "on every turn, regardless of topic" as FR-004 requires.
      `SUPERVISOR` now routes directly to `PLANNER`. See the Phase 6 handoff.
- [X] T020 [P] [US1] FRONTIER Integration test: a distress utterance mid-conversation (any
      topic, not just gaming) routes to `HUMAN_HANDOFF`; separately, call
      `require_gaming_clear` across two consecutive turns with a changed vendor status
      between them and confirm the second call reflects the change, not a cached
      first-call result (FR-016)
- [X] T021 [US1] FRONTIER Build the 40-turn adversarial eval script in
      `agents/gaming/eval/adversarial/` against `guest-self-excluded`, spanning dining,
      entertainment, floor-games, and loyalty topics (SC-001). **Built and correct
      through direct measurement (21/40 turns, zero assertion failures) — full-script
      completion not confirmed within this session.** A severe, pre-existing
      checkpointing-performance characteristic (found by this script, not caused by it —
      see the Phase 6 handoff's "Bugs found" #5) makes per-turn latency on one
      thread grow steeply with turn count (measured: 0.05s at turn 5, 37s at turn 20,
      105s at turn 21), independent of which domain a turn touches. Not wired into
      `make eval`'s default chain as a result (`make eval-adversarial` instead). This is
      the single most significant open item this phase leaves — flagged prominently,
      not silently accepted.
- [X] T022 [US1] FRONTIER Fault-injection test: `sims/casino`'s responsible-gaming status
      endpoint made unreachable mid-script — confirm gaming/offer capability fails closed
      and escalates while dining/spa requests in the same script still complete (SC-008).
      **Delivered as a focused integration test**
      (`agents/gaming/tests/test_graph.py::test_dependency_unavailable_fails_closed_for_every_intent`),
      not embedded in the 40-turn script itself — given #5 above, a focused test is the
      practical verification path for this specific guarantee.

**Checkpoint**: User Story 1 fully functional and independently testable.

---

## Phase 4: User Story 2 - A guest's dietary restriction is enforced, never just remembered (Priority: P1) 🎯 MVP

**Goal**: An allergen-fidelity mismatch blocks confirmation and is never approvable by a
human reviewer or the guest — it clears only by correcting the structured record.

**Independent Test**: State an allergy, submit a booking whose structured payload omits
it; confirm the block routes to `BLOCKED`, and that resuming with `{"approved": true}`
has no effect (SC-002).

### Implementation for User Story 2

- [X] T023 [US2] FRONTIER [CRIT] Implement `check_dietary_fidelity(proposal,
      conversation_restrictions)` in `policy/dietary.py` (ADR-029,
      `contracts/policy-engine-contract.md`). CRIT: allergen fidelity check, never-economy.
      **Refinement**: returns `missing: list[DietaryRequirement]`, not
      `list[DietaryConflict]` as data-model.md originally sketched —
      `DietaryConflict`'s `menu_item`/`chunk_id`/`source_uri` fields don't fit a
      payload-fidelity miss. See the Phase 6 handoff.
- [X] T024 [P] [US2] FRONTIER [CRIT] `hypothesis` property tests for
      `check_dietary_fidelity` in `policy/tests/test_dietary.py` (any restriction present
      in conversation but absent from the structured payload always yields
      `fidelity_ok=False`)
- [X] T025 [US2] FRONTIER [CRIT] Amend `agents/dining/nodes/allergen_check.py` to also call
      `check_dietary_fidelity`, alongside its existing, unchanged `DietaryConflict`
      menu-content matching. Scans every human message in the conversation (via
      `intent_parser._extract_dietary`), not just the latest turn's intent.
- [X] T026 [US2] FRONTIER [CRIT] Amend `agents/dining/edges/conditions.py`'s routing.
      **Deviation (significant)**: `BLOCKED` (the top-level supervisor node) is not
      reachable from dining's own compiled subgraph (a separately-compiled StateGraph for
      the DETERMINISTIC/standalone path) — there is no edge from within it to a top-level
      node. Added a new `DiningState`-local field, `policy_blocked` (`agents/dining/
      state.py`, always explicitly written, never left stale across a retry), which
      `route_after_hold_placement` now checks *before* `awaiting_approval` and routes
      straight to `END` — the dining-subgraph-local equivalent of "never reaches
      `human_approval`." Also discovered: `allergen_check` was the *only* prior setter of
      `awaiting_approval`, and both its blocking cases (existing menu-content conflict,
      new fidelity mismatch) are now non-overridable — `human_approval`/`HUMAN_APPROVAL`
      is consequently unreachable via the current graph topology (kept, not deleted, for
      a future overridable-block type; see T027's unit test). Also discovered: the
      multi-domain fanout path (`graph/supervisor.py`'s `_run_dining_propose_and_hold`)
      never calls `allergen_check` at all — out of scope for US2, flagged for US5/T040.
- [X] T027 [US2] FRONTIER [CRIT] Regression test in `agents/dining/tests/`: resume the
      graph with `{"approved": true}` on a blocking-fidelity case and confirm no
      confirmation occurs — the concrete bug this plan identified in the current code.
      Also added a synthetic-graph unit test preserving coverage of `human_approval`'s
      now-unreachable-via-allergen_check reject/release-hold logic.
- [X] T027a [P] [US2] FRONTIER Regression check: existing Phase-4 structured-dietary-
      capture tests (`agents/dining/nodes/intent_parser.py`) still pass unmodified after
      T025/T026's amendments to the same subgraph (FR-005) — confirmed via the full
      existing dining test suite (10/10 passing).
- [X] T028 [P] [US2] FRONTIER Structural test: for dining's standalone subgraph,
      `policy_blocked=True` always routes to `END`, never `HUMAN_APPROVAL`, regardless of
      `awaiting_approval`'s value — the dining-local equivalent of "BLOCKED is the only
      reachable node."

**Checkpoint**: User Stories 1 and 2 (both P1/MVP) both work independently.

---

## Phase 5: User Story 3 - Manipulated content can't hijack the conversation (Priority: P2)

**Goal**: Retrieved content carrying an embedded instruction cannot alter tool calls or
bypass an approval gate.

**Independent Test**: Seed a document with an embedded instruction; confirm the system's
tool-calling and approval behavior is unaffected, and a full red-team corpus sweep changes
zero tool-invocation decisions (SC-003).

### Implementation for User Story 3

- [X] T029 [US3] FRONTIER Implement `screen_retrieved_content(chunks, corpus=...)` in
      `policy/injection.py` — exact/fuzzy corpus match + `sentence-transformers` embedding
      similarity, no LLM call (ADR-032)
- [X] T030 [P] [US3] FRONTIER Author `content/redteam/injection_corpus.yaml`, sibling to
      `rag/eval/adversarial_set.yaml`'s existing format. 20 hand-curated patterns across
      6 categories (instruction override, role/persona hijack, tool manipulation,
      approval-gate bypass, data exfiltration, system-prompt leak, delimiter confusion).
- [X] T031 [US3] FRONTIER Wire `screen_retrieved_content` into each domain's retrieval call
      site. **Deviation (significant)**: `agents/dining/menu.py`'s `default_menu_query` is
      the *only* retrieval call site that exists anywhere in `agents/`/`graph/` today —
      there is no general property-knowledge-document Q&A node in any domain (the scenario
      spec.md's User Story 3 describes end-to-end). Wired into dining's menu query as the
      one real, concrete integration point; menu content there is only ever used for
      keyword matching (never an LLM generation context), so the practical risk is lower
      than the motivating scenario, but it is still real retrieval-sourced content and
      screening it is a reasonable defense-in-depth measure. Full end-to-end coverage of
      the "answer composed from a property document" scenario is blocked on a future
      feature building that Q&A node — flagged in the Phase 6 handoff, not something
      this phase can close.
- [X] T032 [P] [US3] FRONTIER Structural test
      `test_retrieved_content_never_feeds_tool_selection`, mirroring
      `agents/gaming/tests/test_graph.py`'s existing no-mutation-verb style — verifies no
      intent-parsing/tool-dispatch code path reads `Chunk` content, in any domain
- [X] T033 [US3] FRONTIER Injection-corpus sweep test: run the full corpus, confirm zero
      entries change any tool-invocation decision or bypass an approval gate (SC-003)

**Checkpoint**: User Stories 1-3 all independently functional.

---

## Phase 6: User Story 4 - Sensitive guest details never appear in diagnostic systems (Priority: P2)

**Goal**: Identifying details, payment details, and free-text health disclosures are
redacted from every diagnostic/trace record; the structured dietary restriction
type/severity survives.

**Independent Test**: A conversation with a name, phone number, and a free-text allergy
disclosure produces a trace record with the first two redacted and the structured
restriction intact (SC-004).

### Implementation for User Story 4

- [X] T034 [US4] FRONTIER Implement `redact_for_trace(payload)` in
      `observability/redaction.py` using Presidio analyzer + anonymizer with the local
      spaCy backend (ADR-031). **Refinement**: two treatments for free text, not one —
      `_WHOLESALE_REDACT_FIELDS` (currently `detail`, i.e. `DietaryRequirement.detail`)
      redact to a fixed placeholder wholesale (the spec's clarification asks for the
      disclosure itself gone); every other free-text field goes through Presidio's
      NER-based `redact_text`, explicitly scoped to `_REDACTED_ENTITIES` (FR-010's named
      categories only — Presidio's own default set additionally flags
      `DATE_TIME`/`URL`/`NRP`/`CRYPTO`, which would over-redact booking times). See
      the Phase 6 handoff.
- [X] T035 [P] [US4] FRONTIER Field-name allowlist enforcement
      (`DietaryRequirement.restriction`/`.severity`, `PolicyFlag.rule`/`.disposition`
      exempted by name, not by NLP miss) + unit tests
      (`contracts/redaction-contract.md`)
- [X] T036 [US4] FRONTIER Wire `redact_for_trace` into
      `mcp/gateway/_shared/errors.py:147`'s long-stubbed `redact()` hook — one
      implementation, not a parallel one. Fails closed on its own failure (returns a
      fixed placeholder string, never the raw input).
- [X] T037 [US4] FRONTIER Wire `redact_for_trace` into every `ToolFailure(detail=...)`
      construction site across domains. **Deviation**: no such call site exists beyond
      `errors.py`'s `redact()` (T036) — Phase 7 owns the actual Langfuse/OTel emission
      pipeline (`docs/ResortFlow-Observability-Tracing-Deep-Dive`), and no trace/log write path
      exists anywhere in `agents/`/`graph/` today to wire into. `redact_for_trace_or_suppress`
      is documented as the reference implementation Phase 7's emission callback must call.
- [X] T038 [US4] FRONTIER Fail-closed suppression: if `redact_for_trace` itself raises,
      suppress the record rather than write it unredacted (FR-022). Implemented as
      `redact_for_trace_or_suppress` (returns `None` on failure) since no live call site
      exists yet to wire the suppression *behavior* into directly (see T037).
- [X] T039 [P] [US4] FRONTIER `hypothesis`/property tests: a representative sample of
      records containing PII is never written unredacted; the structured restriction
      type/severity always survives (SC-004)

**Checkpoint**: User Stories 1-4 all independently functional.

---

## Phase 7: User Story 5 - A human can act on exactly what was blocked, not the whole conversation (Priority: P2)

**Goal**: A policy block on one part of a multi-part request is legible to a human
reviewer independent of unrelated, unblocked parts, which continue through the ordinary
approval path unaffected.

**Independent Test**: A dining leg blocked by an allergen-fidelity mismatch inside a
multi-domain itinerary is identified specifically; the entertainment leg in the same
itinerary proceeds through `HUMAN_APPROVAL` independently (SC-005).

### Implementation for User Story 5

- [X] T040 [US5] FRONTIER Verify/extend `graph/supervisor.py`'s
      `make_proposal_assembly_node` so a policy-blocked domain's proposal is excluded from
      the held set `approval_scope` is built from. **Deviation (significant, larger than
      "verify/extend")**: discovered the fanout runner (`_run_dining_propose_and_hold`)
      never called any allergen check at all — the multi-domain path had zero allergen
      enforcement prior to this task. Added `check_dietary_fidelity` immediately after
      `venue_discovery`, before any hold is placed for that leg; a block skips
      `hold_placement` entirely, so the proposal never reaches `HoldStatus.HELD` and
      `proposal_assembly`'s existing `held` filter excludes it for free — no change to
      `make_proposal_assembly_node` itself was needed. Menu-content matching (the *other*
      half of dining's allergen_check) is intentionally not added to the fanout path in
      this pass — flagged in the Phase 6 handoff.
- [X] T041 [P] [US5] FRONTIER Integration test: one leg of a multi-domain itinerary hits a
      policy block while another does not; confirm the blocked leg never places a hold and
      the clean leg proceeds through `HUMAN_APPROVAL`'s unchanged approve/decline/modify
      contract independently
- [X] T042 [US5] FRONTIER Confirm the guest-facing block message plus the `PolicyFlag`/
      `SagaEvent` pair together let a reviewer identify the specific item and reason
      without inspecting the full conversation (SC-005)
- [X] T042a [P] [US5] FRONTIER Integration test: an allergen-fidelity mismatch and a
      self-exclusion conflict both fire within the same guest interaction; assert both
      `PolicyFlag`s are present via `policy_flags`' `append_only` reducer (FR-017)

**Checkpoint**: User Stories 1-5 all independently functional.

---

## Phase 8: User Story 6 - The stubbed offer capability becomes safely usable (Priority: P3)

**Goal**: `OFFER_AGENT` is compiled, gated by `require_gaming_clear` on every turn, and
structurally cannot call a gaming-linked-offer tool because none exists in its registry.

**Independent Test**: Exercise the offer capability for both a self-excluded and a
non-excluded guest; confirm gaming-linked offers appear only for the latter (SC-007).

### Implementation for User Story 6

- [X] T043 [US6] FRONTIER [CRIT] Implement `make_offer_agent_node` in
      `graph/supervisor.py`: `require_gaming_clear` → (if clear) `GamingTools.
      get_loyalty_status` → `mandated_message_for` on any offer surfaced (ADR-030). CRIT:
      self-exclusion gate, never-economy.
- [X] T044 [US6] FRONTIER Mount `OFFER_AGENT` in the supervisor graph's topology — reserved
      node name since feature 005 (ADR-020), no rename, no new name
      (`contracts/graph-topology-v006.md`). **Deviation (significant)**: mounted only
      between `POLICY_POSTFLIGHT` and `RESPOND`, on the multi-domain `SUPERVISOR` path —
      NOT reachable from `DETERMINISTIC` (~60% of traffic), which routes straight to
      `END`. Extending it there was assessed and deliberately deferred: doing so would
      route `DETERMINISTIC` through `RESPOND`, which would re-summarize a booking
      `DETERMINISTIC`'s own inner subgraph already confirmed (double-messaging every
      single-domain confirmed booking). This is the most significant open gap this phase
      leaves — a Constitution Principle IV hard rule ("before the Offer Agent runs at
      all, on every turn") not yet fully satisfied. See the Phase 6 handoff.
- [X] T045 [P] [US6] FRONTIER Structural test `test_offer_agent_calls_no_gaming_tool`
      (`contracts/offer-agent-tool-registry-contract.md`, ADR-037) — asserts the compiled
      node's only vendor call is `get_loyalty_status`
- [X] T046 [US6] FRONTIER Integration test: `guest-good-standing` gets the offer path
      exercised (via the multi-domain path, resuming past the cross-domain
      `HUMAN_APPROVAL` interrupt); `guest-self-excluded` gets zero offers and
      `get_loyalty_status` is never called (SC-007)

**Checkpoint**: All six user stories independently functional.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Whole-feature validation and handoff.

- [X] T047 [P] FRONTIER Run all six `quickstart.md` scenarios end-to-end; fix any gaps
      found. Scenarios 2-6 verified via the test suite built for their respective user
      stories; Scenario 1 (40-turn script) verified through 21/40 turns directly (see
      T021). `quickstart.md` itself updated where behavior diverged from the original
      design (Scenario 2's routing target, Scenario 6's path limitation).
- [X] T048 [P] FRONTIER Write the Phase 6 handoff — what was built, deviations
      from spec, interfaces published (per this plan's contracts/), open questions
      resolved
- [X] T049 FRONTIER Run `mypy --strict` over `policy/`, `observability/redaction.py`, and
      every amended file; resolve any reported errors. All errors in this feature's own
      new/amended code resolved (3 in `observability/redaction.py`, 1 in
      `graph/supervisor.py`, 1 missing stub package). Pre-existing errors in
      `mcp/gateway/*` (Track B, untouched by this feature) left as found — confirmed via
      `git log` that none of those files were modified this session.
- [X] T050 FRONTIER `make eval` — confirmed passing: dining 25/25, entertainment 10/10,
      stay 10/10, wellness 10/10. **Found and fixed a real regression while doing this**:
      7 of dining's 25 cases had encoded the exact allergen-override bug this feature
      fixes as expected-passing behavior (`agents/dining/eval/test_cases.yaml`'s
      `conflict-01/02/03`, `resume-01/02/03/05`) — updated to assert the corrected
      behavior. **Deviation**: the adversarial 40-turn script is deliberately NOT wired
      into `make eval`'s default chain (new `make eval-adversarial` target instead) —
      see T021's note and
      the Phase 6 handoff's Bug 5.
- [X] T051 [P] FRONTIER Confirm `docs/models.md`/`docs/model-tiering-runtime.md` need no
      amendment (no new LLM-invoking node this feature) and record that finding in
      the Phase 6 handoff

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS every user story below.
- **User Stories (Phase 3-8)**: All depend on Foundational completion. US1 and US2 are
  both P1/MVP and have no dependency on each other — they may proceed in parallel. US3,
  US4, US5 (all P2) similarly have no inter-story dependency. US6 (P3) depends on
  `require_gaming_clear` (built in US1, T012) — it is the one story with a real
  cross-story dependency, since it reuses US1's gating function rather than duplicating
  it (ADR-030/037).
- **Polish (Phase 9)**: Depends on all six user stories being complete.

### Within Each User Story

- Policy-module function → its unit/property tests ([P] where the test file is
  independent) → wiring into the call site → integration/regression test → eval/
  adversarial coverage.

### Parallel Opportunities

- All Setup tasks marked [P] run in parallel.
- Within Foundational, T005/T006/T008 (tests and the new types module) run in parallel
  once their respective implementation task lands.
- US1 and US2 (both P1) can be staffed in parallel — no shared files between
  `agents/gaming/` and `agents/dining/`.
- US3, US4, US5 (all P2) can likewise be staffed in parallel — `policy/injection.py`,
  `observability/redaction.py`, and `graph/supervisor.py`'s proposal-assembly amendment
  touch disjoint files (the one `graph/supervisor.py` file is touched by both Foundational
  and US5/US6 tasks sequentially, not concurrently — sequence T009/T010/T011 before
  T040/T043/T044).
- US6 should start after US1's T012 (`require_gaming_clear`) lands, not before.

---

## Parallel Example: User Story 1

```bash
# Once T012 (require_gaming_clear) lands, these run in parallel:
Task: "hypothesis property tests for require_gaming_clear in policy/tests/test_gaming.py"
Task: "Extend agents/gaming/tests/fakes.py fixture coverage for all three intents"

# Independently, once T017 (check_distress_signal) lands:
Task: "Corpus + hypothesis tests for check_distress_signal in policy/tests/test_distress.py"
Task: "Integration test: distress utterance on any topic routes to HUMAN_HANDOFF"
```

---

## Implementation Strategy

### MVP First (User Stories 1 and 2)

1. Complete Phase 1: Setup.
2. Complete Phase 2: Foundational (CRITICAL — blocks everything below).
3. Complete Phase 3 (US1) and Phase 4 (US2) — both P1, both independently testable,
   together the MVP: a self-excluded guest is fully protected, and an allergen mismatch
   can never be approved past.
4. **STOP and VALIDATE**: run the 40-turn adversarial script (US1) and the allergen
   override-attempt regression test (US2) independently.

### Incremental Delivery

1. Setup + Foundational → foundation ready.
2. US1 + US2 (parallel) → MVP → validate independently → demo.
3. US3 + US4 + US5 (parallel) → validate independently → demo.
4. US6 (after US1's `require_gaming_clear` exists) → validate independently → demo.
5. Polish → `make eval` is the merge gate; phase handoff document closes the feature.

### Parallel Team Strategy

With multiple developers: one pair takes US1+US2 (both touch domain subgraphs, P1); a
second pair takes US3+US4 (both new `policy/`/`observability/` modules, no shared files,
P2); a third takes US5 (small, cross-domain routing verification, P2) then US6 once US1's
`require_gaming_clear` lands (P3, the one genuinely sequenced story).

---

## Model tier summary

| Tier | Tasks | `[CRIT]` | Notes |
|---|---|---|---|
| FRONTIER | 53 (all) | 15 (T004, T012-T015, T017-T019, T023-T027, T043) | Whole-phase override per `docs/models.md` ("Phases 0, 5, 6 and 7 run entirely on FRONTIER") — every task reads FRONTIER regardless of individual complexity. `[CRIT]` is marked separately, for audit precision, on tasks falling in the named never-economy categories (state reducers; policy engine/responsible gaming/self-exclusion; allergen fidelity check) — it carries no additional tier consequence here since the whole phase is already FRONTIER. |
| MID | 0 | — | N/A — whole-phase frontier override applies |
| ECONOMY | 0 | — | None — see the whole-phase override above; no task in this feature was individually evaluated as economy-eligible, because the phase-level rule makes that evaluation moot |

---

## Phase N: Convergence

Produced by `/speckit-converge`, run after `/speckit-implement` completed all 53 tasks
above. Assessed against `spec.md`, `plan.md`, and this file as the sole source of intent,
plus the constitution. All five items below were already disclosed in
the Phase 6 handoff during implementation — convergence formalizes them as
traceable, actionable tasks rather than surfacing anything new. FRONTIER (whole-phase
override, per the Model tier assignment rules above); `[CRIT]` marked per the same
never-economy criteria as the tasks above.

- [X] T052 FRONTIER [CRIT] Route `DETERMINISTIC` (single-domain, ~60% of traffic) through
      `OFFER_AGENT`/`POLICY_POSTFLIGHT` without `RESPOND` re-summarizing an
      already-confirmed booking per `graph/state.py` (missing). CRIT: self-exclusion
      gate, never-economy. Currently `DETERMINISTIC` routes straight to `END`, so the
      self-exclusion check before "the Offer Agent runs at all, on every turn"
      (Constitution Principle IV) is unmet on the majority of traffic — the single most
      significant gap this phase leaves. Needs a design decision (e.g. `RESPOND` learning
      to recognize a `DETERMINISTIC`-originated turn and skip its own summary, or a
      different mounting point for the two nodes) — not a mechanical wiring fix. Blocks
      full satisfaction of FR-001, FR-012, FR-015, US1 Acceptance Scenario 2, and US6.
- [X] T053 FRONTIER Add menu-content allergen matching to `_run_dining_propose_and_hold`
      (the multi-domain fanout path, `graph/supervisor.py`) per FR-006 (partial) —
      mirroring the standalone subgraph's existing check in
      `agents/dining/nodes/allergen_check.py`. The fidelity half already runs there
      (T040); the fanout path still has no check against what a venue's actual menu
      contains for a multi-domain itinerary's dining leg.
- [X] T054 FRONTIER Build a general property-knowledge-document Q&A retrieval node in
      `agents/`/`graph/` per FR-007/FR-008/FR-009 (missing) — the scenario spec.md's own
      User Story 3 names ("a property knowledge document... contains text designed to
      look like an instruction") has no code path to exercise end-to-end today; the only
      real retrieval call site (`agents/dining/menu.py`) is never fed to an LLM generation
      context. Likely warrants its own feature-sized scope, not a small addition —
      `policy.injection.screen_retrieved_content` is ready and tested; it has nowhere
      real to prove itself against the scenario that motivated it.
- [X] T055 FRONTIER Fix the checkpointing-growth defect in `graph/supervisor.py`'s
      `make_deterministic_node` per SC-001/SC-008 (partial). Root cause: on every
      single-domain turn, `sub_state = dict(state)` re-forwarded the FULL, ever-growing
      outer `messages` list as fresh input to each domain's own sub-thread, which
      `add_messages` then re-diffed and the checkpointer re-persisted at every internal
      super-step — cost scaling with total conversation length, not this domain's own
      turn count. Fixed by forwarding only the messages a given sub-thread has not
      already seen (diffed by id against that sub-thread's own last checkpoint via
      `aget_state`) — it already retains everything it has previously been given, so
      `add_messages` appends the delta exactly as if the full list had been resent, with
      the one-time exception of a sub-thread's first-ever call, which still needs the
      complete history to date (e.g. an allergy disclosed on an earlier turn must still
      reach `allergen_check`'s conversation-wide scan). Full regression suite (163
      passed, 2 skipped, 0 failed) confirms no correctness regression from this change,
      including the multi-turn tests that exercise resumed sub-threads. Deferred to
      Phase 7 (decided 2026-09-13): re-running the 40-turn adversarial
      script (`agents/gaming/eval/adversarial/`) end-to-end to confirm the 0.05s→186s+
      per-turn latency blowup is actually collapsed — that confirmation is pure
      performance verification, not a correctness gate, and the fix's correctness is
      already covered by the regression suite above. Once confirmed, re-enable
      `make eval-adversarial` as part of routine verification and reconsider folding it
      into the default `make eval` chain.
- [x] T056 FRONTIER — **UNBLOCKED AND CLOSED** by `specs/008-phase-7-5-gap-closure`
      (ADR-050): that feature wrote the repo's first root `Dockerfile` and its
      `graph/api.py` production entrypoint, closing exactly the two prerequisites this
      task was blocked on (no root `Dockerfile`, no orchestrator entrypoint module).
      The new `Dockerfile` includes `RUN python -m spacy download en_core_web_sm` per
      ADR-031, so the vendored model now ships in the image build, not only in the local
      dev venv. Presidio's `observability/redaction.py` `NlpEngineProvider` config is
      unchanged — this closes the Docker image build path specifically.

---

## Phase N+1: Convergence (Round 2)

Generated by a second `/speckit-converge` pass, run after T052-T056 above were
implemented, per explicit user instruction to re-check convergence before moving to
Phase 7. Assessed against `spec.md`, `plan.md`, and this file as the sole source of
intent, plus the constitution. One actionable finding — everything else T052-T056 were
meant to close (FR-001/FR-012/FR-015/US1 Acceptance Scenario 2/US6 via T052; FR-006 via
T053; FR-007/FR-008/FR-009/US3 via T054) is now satisfied and verified by a clean full
regression run (163 passed, 2 skipped, 0 failed). FRONTIER (whole-phase override, per
the Model tier assignment rules earlier in this file).

- [ ] T057 FRONTIER Confirm SC-001 by running the 40-turn adversarial script
      (`agents/gaming/eval/adversarial/`) to completion (partial) — T055 fixed
      `make_deterministic_node`'s checkpointing-growth defect that previously prevented
      this script from finishing within a practical runtime (correct through 22/40 turns
      in direct measurement, per-turn latency climbing past 186s and still rising, before
      the fix). The fix's *correctness* is covered by the full regression suite (163
      passed, 2 skipped, 0 failed, including tests that exercise a resumed sub-thread
      mid-conversation), but SC-001 itself — "zero gaming content... across a 40-turn
      adversarial test conversation, every time the scenario is run" — has not yet been
      confirmed end-to-end since the fix landed; a confirmation run had been started but had not completed by the time this task was appended. **Deferred to
      Phase 7 (decided 2026-09-13)**: this is acceptance-criterion
      confirmation of an already-applied fix, not new code, so it does not block Phase 7.
      Once confirmed, re-enable `make eval-adversarial` as part of routine verification
      per T055's own recorded follow-up, and consider folding it into the default
      `make eval` chain.
