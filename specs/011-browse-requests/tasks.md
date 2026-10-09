---

description: "Task list for feature 011 — Browse Requests"
---

# Tasks: Browse Requests (What's Available)

**Input**: Design documents from `/specs/011-browse-requests/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/ (6 files), quickstart.md

**Tests**: Requested by the spec (every story names an Independent Test) and the constitution ("TEST BEFORE ADVANCE"). Test tasks come **before** their implementation and MUST fail first. Run pytest with `-p no:schemathesis` on machines where Application Control blocks the `jsonschema_rs` DLL.

**Organization**: Grouped by user story. Ground truth first (feature 010's D1 lesson): the labelled browse and near-miss cases are authored before, and independently of, the vocabulary.

## Format: `[ID] [P?] [Story] [TIER] [CRIT?] Description`

- **[P]**: Different top-level area (`graph/`, `agents/`, `eval/`, `prompts/`, `observability/`, `docs/`, `specs/`) and no dependency on an incomplete task.
- **[TIER]**: Build-time model tier (`docs/models.md`): `FRONTIER` / `MID` / `ECONOMY`. **[CRIT]** forces FRONTIER. Every FRONTIER row carries a one-line justification.
- Paths are relative to the repository root `resortflow/`.

## Model tier assignment *(mandatory — ResortFlow project override)*

- **`[CRIT]` → FRONTIER**: both ADRs; the browse ground-truth cases and their data test; eval scorer/gate/calibration; the tier-2 prompt revision (policy surface); the read-only structural test (Principle III); the responsible-gaming gate wiring and its tests (policy/self-exclusion); the serializer allowlist (Principle V).
- **FRONTIER without `[CRIT]`** (silent-failure bar): the `RoutingDecision` amendment and invariants — a wrong invariant silently misroutes or breaks resume.
- **MID**: cue/filter vocabulary content, `kinds.py` precedence and carry-over, the date-range parser, the `BROWSE` node and per-service handlers, stay-period resolution, non-trivial tests.
- **ECONOMY**: constants, enum member, template strings fully specified by `contracts/browse-replies.md`, contract-amendment text, mechanical checks.

---

## Phase 1: Setup and the single integration commit (IC-011)

**Purpose**: T001 skeleton, then **IC-011**: T002–T007 land together as one integration commit carrying every cross-track interface (AGENTS.md working agreement). After IC-011 each task commits inside one track.

- [X] T001 [ECONOMY] Create `graph/routing/browse.py` (docstring referencing `contracts/browse-router-api.md`) with empty function signatures `extract_filters`, `resolve_window`, `wants_stay_period`, and `graph/routing/tests/test_browse_rules.py` as an empty module.

**IC-011 — one commit: T002–T007 plus T009** (T009, the enum member, is a prerequisite of T005/T006: the classifier's allowed kinds are injected from `RequestKind`)

- [X] T002 [P] [FRONTIER] [CRIT] (IC-011, Track E) Write `eval/tests/test_browse_cases_data.py`: ≥ 4 cases per category per tenant across the 12 categories in `contracts/browse-eval-gate.md`; every phrase quoted in spec.md's Context appears verbatim with its label; `near_miss_*` categories present for both tenants; every case has `split ∈ {dev, holdout}`, holdout ≥ 2 per category per tenant. — CRIT: eval metric implementation.
- [X] T003 [P] [FRONTIER] [CRIT] (IC-011, Track E) Author the browse and near-miss cases in `eval/data/routing_cases.yaml` **before T020 and without reading any vocabulary draft**, including the near-miss FAQ/book/lookup cases that must NOT route to browse. Makes T002 pass. — CRIT: eval ground truth.
- [X] T004 [P] [ECONOMY] (IC-011, specs) Append "Amendment — Feature 011" to `specs/001-contracts-foundation/contracts/telemetry-attributes.md`: `resortflow.routing.kind` gains the bounded value `browse`; no new attribute; no guest text.
- [X] T009 [ECONOMY] (IC-011, Track C) Add `RequestKind.BROWSE = "browse"` to `graph/models.py`; add `BROWSE = "browse"` to `graph/nodes.py` and `ALL_NODES`. Must land before T005/T006 can pass.
- [X] T005 [P] [FRONTIER] [CRIT] (IC-011, Prompts) Extend `prompts/tests/test_routing_prompt.py` per `contracts/browse-classifier-prompt-delta.md`: rendered prompt lists `browse` among injected kinds, contains the in-scope rule, still fails closed without `allowed_kinds`; failure matrix gains a valid `browse` and a `browse` with an unknown service. Must fail before T006 and can pass only once T009 exists. — CRIT: policy enforcement (tier-2 output validation).
- [X] T006 [FRONTIER] [CRIT] (IC-011, Prompts) Revise `prompts/templates/intent_routing_classification.yaml`: add the `browse` definition line and the "enumeration requests are never out_of_scope" rule; run `python -m prompts.sync validate`. Makes T005 pass (after T009). — CRIT: prompt/policy surface.
- [X] T007 [P] [ECONOMY] (IC-011, docs) Add a note row to `docs/model-tiering-runtime.md`: classifier prompt revised for feature 011, tier unchanged (economy, static).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the model amendment, node/version bump, date-range parser, stay period, browse helpers, the recognition rules, the node skeleton and its guarantees, and the two ADRs.

**⚠️ CRITICAL**: No user story starts until this phase is complete.

- [X] T008 [FRONTIER] [CRIT] Write `graph/tests/test_state_amendment_011.py` per `contracts/state-schema-amendment-011.md`: a 5.1 checkpoint fixture resumes with `browse_filters`/`window` `None`; populated decision round-trips through `EncryptedSerializer` and `plain_serde()`, no pickle; every added invariant holds and is rejected when violated. Must fail before T010–T011. — CRIT: Principle V / resume safety.
- [X] T010 [FRONTIER] Add `BrowseFilters` and `DateWindow` models and the two `RoutingDecision` fields, with the validators T008 exercises (fields only when `kind == BROWSE`; gaming only alone; `secondary_kind` BOOK allowed under LOOKUP or BROWSE; `window.end >= start`), to `graph/models.py`. — justification: a wrong invariant silently misroutes or breaks resume.
- [X] T011 [FRONTIER] [CRIT] Register `BrowseFilters`, `DateWindow` in `agents/dining/serde.py::_ALLOWED_MSGPACK_TYPES`; bump `GRAPH_VERSION` `"5.1"` → `"5.2"` in `graph/supervisor.py`; update the frozen registries in `tests/test_contract_registries.py` (node list). Makes T008 pass. — CRIT: serializer allowlist (a missing entry breaks resume).
- [X] T012 [MID] Write `graph/routing/tests/test_date_range.py`: `next 5 days`, `for 3 nights`, `this weekend` (incl. when today is Saturday/Sunday), `from oct 5 to oct 8`, `between oct 5 and oct 8`, `tonight`/`tomorrow`, year-less dates resolve to the next occurrence, `parse_stay_length` (`next 5 days`, `5-night`, `a week`); Hypothesis property: never returns `end < start`, never raises.
- [X] T013 [MID] Implement `parse_date_range` and `parse_stay_length` in `graph/routing/temporal.py` per `contracts/browse-router-api.md`. Makes T012 pass.
- [X] T014 [MID] Write `graph/tests/test_stay_period.py`: `stay_period` returns arrival/departure of the latest `confirmed`/`held` stay record, `None` for released/expired/none, ignores non-stay records, dedupes duplicated confirmations (feature 010's key).
- [X] T015 [MID] Add `ends_at` to `BookingRecord` (from `Proposal.ends_at`) and implement `stay_period(records)` in `graph/bookings.py`. Makes T014 pass.
- [X] T016 [MID] Write `graph/routing/tests/test_browse_filters.py`: cuisine, price band ("cheap", "$$"), dietary (enum only), category, party size; `wants_stay_period` true for "my stay", "during my stay", "while I'm here"; `resolve_window` returns `needs_dates` for "my stay" with no dates.
- [X] T016a [MID] Write `graph/routing/tests/test_tenant_profile.py`: `PROPERTY_NAMES` maps `MGV` → "Meridian Grand", `MHB` → "Meridian Harbor"; `detect_other_property("dining in Boston", "MGV")` → "Meridian Harbor"; "dining in Nevada" from MGV → `None`; "las vegas" from MHB → "Meridian Grand"; a message naming both → `None` (the guest's own wins); "ma"/"nv" only match as whole tokens (never inside "dinner", "manager", …); text never changes the tenant used for the read.
- [X] T017 [MID] Implement `extract_filters`, `resolve_window`, `wants_stay_period` in `graph/routing/browse.py`. Makes T016 pass.
- [X] T017a [MID] Implement `TenantProfile`/`PROPERTY_NAMES`/`detect_other_property` in `graph/routing/browse.py` per `data-model.md`. Makes T016a pass.
- [X] T018 [MID] Write `graph/routing/tests/test_browse_rules.py` from `contracts/browse-router-api.md`: cue+plural-noun table, strong phrases, every veto (book cues; attribute questions), lookup-phrase precedence ("list my bookings" → lookup), FAQ near-misses ("what time is checkout", "what's the room rate", "do you have connecting rooms", "what time do the shows start"), noun→service mapping (`list venues` → [dining], `what casino games do you have` → [gaming] not entertainment, `what games are on tonight` → [entertainment], `list the resort experiences`/`what options` → [] overview), `{browse, book}` clause pair, carry-over ("only japanese" after a browse; not after an intervening turn; `needs_dates` follow-up), `stay safe`/`stay tuned` still non-requests; and a latency case: median tier 0–1 time over the browse cases (dev split) is < 5 ms (SC-007).
- [X] T019 [FRONTIER] [CRIT] Draft `docs/adr/059-browse-requests.md` (Status: Proposed; Context, Options Considered, Decision, and Consequences): deterministic read-only browse, precedence over FAQ and possessive lookup, why not knowledge base or model, gaming stays behind the existing gate, bounded lists, honest failure (FR-029). — CRIT: all ADRs.
- [X] T020 [MID] Author the `kinds.browse` block and `filters` vocabularies in `graph/routing/vocabulary.yaml` (cues, plural catalogue nouns **with the noun → service map from `contracts/browse-router-api.md`**, strong phrases, vetoes, narrowing cues, cuisine/price/dietary/category words). Check against the **`dev` split only**; never open holdout cases while tuning. Makes the vocabulary half of T018 pass.
- [X] T021 [MID] Implement browse recognition in `graph/routing/kinds.py`: precedence slot, veto handling, filter/window population, `PreviousTurn` gains `filters`/`window`, `_try_carry_over` browse branch, `{BROWSE, BOOK}` clause pair. Makes T018 pass.
- [X] T022 [MID] In `graph/supervisor.py`: `_compute_previous_turn` reads filters/window from the previous decision; `INTENT_ROUTER` writes `clarification_count: 0` when the decision is `BROWSE` (R-10); `_route_from_intent_router` gains the two browse branches per `contracts/graph-topology-v011.md` (gaming-only → `DETERMINISTIC`, otherwise → `BROWSE`).
- [X] T023 [FRONTIER] [CRIT] Write `agents/tests/test_browse_readonly_structural.py`: the `BROWSE` node's module code references only read tool methods (allow-list; fails on `hold_*`, `confirm_*`, `purchase_*`, `cancel_*`, `release_*`, `join_waitlist`); it never reads `state["retrieved"]`; a browse turn leaves `holds`, `proposals`, `confirmations` and the ledger unchanged. Must fail before T024. — CRIT: Principle III / SC-009.
- [X] T024 [MID] Implement the `BROWSE` node skeleton — composer in the new `graph/browse_node.py` (so T023's structural test is precise), thin `make_browse_node(tools_factory)` wrapper in `graph/supervisor.py`: window resolution incl. stay period (T015), per-service handler registry, provider-failure sections, browse+book continuation, `_apply_policy_postflight`/`_apply_offer_agent`; register via `_wrap(...)` in `build_supervisor_graph` with its edges. Per-service handlers are stubs returning "not yet". Makes T023 pass.
- [X] T025 [FRONTIER] [CRIT] Draft `docs/adr/060-routing-decision-amendment-011.md` (Status: Proposed): `browse` kind + optional `browse_filters`/`window`, no new state key, minor `GRAPH_VERSION`, relaxed `secondary_kind`, one-turn refinement carry-over, allowlist entries. — CRIT: all ADRs.

**Checkpoint**: state amended and tested; recognition and routing wired; node skeleton read-only by test; ADRs drafted (Proposed).

---

## Phase 3: User Story 1 — Dining venues before booking (Priority: P1) 🎯 MVP

**Goal**: "What are the dining venues" returns the property's real venues, filterable, with the dietary line; refinements narrow the last browse.

**Independent Test**: Ask for dining venues for each tenant; only that tenant's venues appear, no knowledge-base text.

- [X] T026 [US1] [MID] Write `agents/tests/test_browse_dining.py`: verbatim "what are the dining venues available in nevada" lists MGV venues (name, cuisine, price) and offers to book; MHB gets MHB's venues only; "Japanese"/"$$"/"vegetarian" filters narrow; a named dietary need adds the "state it when you book" line and never a safety claim; "dining in Boston" from MGV shows MGV with the property statement; zero matches → empty template with widen hint; provider error → "couldn't retrieve" line, no KB text; list capped at 10 with "and N more".
- [X] T027 [US1] [FRONTIER] [CRIT] Add the dietary-note constant and the forbidden-content checker (`confirmed`/`booked`/`held`/`all set`, safety claims, `state["retrieved"]` text) to `graph/replies.py` with a test; the constant follows `contracts/browse-replies.md` exactly. — CRIT: allergen handling / policy wording.
- [X] T028 [US1] [ECONOMY] Add the dining list, empty, unavailable, next-step and property-statement templates to `graph/replies.py` from `contracts/browse-replies.md`.
- [X] T029 [US1] [MID] Implement the dining handler in the `BROWSE` node: `list_venues(cuisine, price_band, supports_dietary)` from `browse_filters`, cap and "and N more"; the `DietaryRestriction` enum value is passed as its string value and a test asserts every enum value is accepted by the fake dining tool; the property statement uses `detect_other_property(text, state["tenant_id"])` (T017a) and `PROPERTY_NAMES[tenant_id]`. Makes T026 pass.
- [X] T030 [P] [US1] [MID] Write `agents/tests/test_browse_refinement.py`: "only japanese" right after a dining browse narrows it; "cheaper ones" after an events browse applies to events; after an intervening smalltalk/faq turn the follow-up is a fresh message (FR-005a, SC-011).

**Checkpoint**: dining browse works end to end, including refinements.

---

## Phase 4: User Story 2 — Overview and clarify (Priority: P1)

**Goal**: "List the resort experiences" returns the overview; a clarify answer that is a question gets it without burning the clarification budget.

**Independent Test**: Send it as a first message and as the answer to the clarifying question; both return the overview.

- [X] T031 [US2] [MID] Write `agents/tests/test_browse_clarify.py`: "list the resort experiences" (first message) → overview node order `… intent_router → browse`; the same as a clarify answer → overview and `clarification_count == 0`; two consecutive browse answers never escalate; the clarify question names the options; after the overview a bare "dining" continues into the dining flow (US2 scenario 3).
- [X] T032 [US2] [ECONOMY] Add the overview templates (five bullets; restricted variant without casino text) to `graph/replies.py` from `contracts/browse-replies.md`.
- [X] T032a [US2] [FRONTIER] [CRIT] Write `agents/tests/test_browse_overview_gate.py` **before T033**: cleared guest → overview includes the casino line; self-excluded → no casino text (loyalty-only line); status unavailable (`GamingToolError`) → no casino text, fail closed; the gate is called on every overview turn (call count, never cached across turns). Must fail before T033. — CRIT: policy / responsible gaming / self-exclusion.
- [X] T033 [US2] [FRONTIER] [CRIT] Implement the overview handler: call `require_gaming_clear` fresh on the turn to decide the gaming line; restricted, self-excluded or unavailable → omit it (fail closed). Makes T031 and T032a pass. — CRIT: policy / responsible gaming / self-exclusion.

**Checkpoint**: the overview is never a decline; the gaming line is gated.

---

## Phase 5: User Story 3 — Events for a window, including my stay (Priority: P1)

**Goal**: events within "next N days" / "this weekend" / a stay period, honestly bounded.

**Independent Test**: Book a room for known dates, ask about events "during my stay"; only events in those dates appear; with no stay the assistant asks for dates.

- [X] T034 [US3] [MID] Write `agents/tests/test_browse_events.py`: "what events are happening in the next 5 days" filters to the window, soonest first, states the dates used; default 7 days is stated; "what all events are happening in my stay period" with a held stay uses the stay dates, with none asks for dates and reads nothing; sold-out/cancelled omitted; nothing in window → nearest upcoming offered; a booking that contains "stay" does not make it a lookup; provider error handled; capped at 10.
- [X] T035 [US3] [ECONOMY] Add the events list/empty/needs-dates templates and the window phrases (`(I used the next 7 days)`, `(your stay: …)`) to `graph/replies.py`.
- [X] T036 [US3] [MID] Implement the events handler: one `search_events()` (≤ 3 pages), local window filter, status filter, sort, cap; stay-period substitution via `stay_period(booking_view(state))`. Makes T034 pass.

**Checkpoint**: both reported event defects reproduce as fixed.

---

## Phase 6: User Story 4 — Stay length and rooms (Priority: P2)

**Goal**: "book stay for next 5 days" asks only for arrival; "what rooms are available Oct 5 to Oct 8" lists rooms.

**Independent Test**: Run both phrases against fake stay tools.

- [X] T037 [US4] [MID] Write `agents/tests/test_browse_stay.py`: rooms listed with nightly rate and `AVAILABLE_UNITS > 0` only; dates missing → asks for dates; assumed 2 adults is stated; and (booking side) "book stay for next 5 days" yields a 5-night intent asking only for arrival.
- [X] T038 [US4] [MID] Extend `agents/stay/nodes/intent_parser.py`'s `_extract_nights` to use `parse_stay_length` (`next N days`, `N-night`, `for N nights`), keeping the existing default of 1.
- [X] T039 [US4] [ECONOMY] Add the rooms list/needs-dates templates to `graph/replies.py`.
- [X] T040 [US4] [MID] Implement the rooms handler: `search_rooms(arrival, departure, adults)` from the window/stay length. Makes T037 pass.

**Checkpoint**: stay length is understood; rooms are browsable.

---

## Phase 7: User Story 5 — Spa and wellness (Priority: P2)

- [X] T041 [US5] [MID] Write `agents/tests/test_browse_wellness.py`: services listed with duration and price; category filter narrows; no KB text; provider error handled.
- [X] T042 [US5] [ECONOMY] Add the wellness list template to `graph/replies.py`.
- [X] T043 [US5] [MID] Implement the wellness handler: `list_services(category)`. Makes T041 pass.

---

## Phase 8: User Story 6 — Casino floor games, gated (Priority: P3)

- [X] T044 [US6] [FRONTIER] [CRIT] Write `agents/tests/test_browse_gaming_gate.py`: a cleared guest asking for the casino games gets the list (through the existing gated gaming graph, node order `… intent_router → deterministic`); a self-excluded guest gets only the mandated message and no list; status-unavailable withholds and says so; the overview gaming line is covered by T032a; the gate is re-run every turn (never cached); "bet"/"play"/"book a table" still declines. — CRIT: policy / responsible gaming / self-exclusion.
- [X] T045 [US6] [MID] Verify the gaming-only browse branch in `_route_from_intent_router` (T022) reaches the gated graph and add any wiring T044 shows missing. Makes T044 pass.

---

## Phase 9: User Story 7 — Browse quality is measured and gated (Priority: P2)

- [X] T046 [US7] [FRONTIER] [CRIT] Write `eval/tests/test_browse_gate_degraded.py`: with the browse vocabulary removed, holdout `browse_accuracy` falls below its floor and the verdict is FAIL. — CRIT: eval metric implementation.
- [X] T047 [US7] [FRONTIER] [CRIT] Extend `eval/routing_scorer.py` with `browse_accuracy`, `browse_catalogue_misroute`, `near_miss_browse_leak`, `stay_length_accuracy`, `browse_llm_tier_share`, tenant-stratified, blocking from holdout, dev reported. — CRIT: eval metric implementation.
- [X] T048 [US7] [FRONTIER] [CRIT] Wire the new metrics into `eval/runner.py` (`_METRIC_FLOORS`, direction, `browse_llm_tier_share` report-only until T049). — CRIT: gate logic.
- [X] T049 [US7] [FRONTIER] [CRIT] With live credentials, calibrate the `browse_accuracy` floor and measure the tier-2 `UNAVAILABLE` rate on the browse cases (R-08); record in research.md R-13; flip `browse_llm_tier_share` to blocking only if the measurement supports it; record the timeout decision. Measure **dev and holdout**. — CRIT: sets a blocking threshold.

---

## Phase 10: Polish & Cross-Cutting Concerns

- [X] T050 [ECONOMY] Repo-wide checks: no `graph/routing/*.py` imports a tool or `openai`; no browse reply path reads `state["retrieved"]`; no real client names; `grep -rn "Which resort experience would you like"` finds nothing outside tests.
- [X] T050a [MID] Make the rolling ticketing seed (quick-fix `1904d8f`) part of the running stack: rebuild only the `ticketing-sim` image with the new `seed.py` (tag the existing image first, e.g. `resortflow-ticketing-sim:pre-011`; no image is removed), recreate that container, reset it, and confirm events for a "next 7 days" window for both tenants (SC-008). Until then the container carries a `docker cp` patch that a recreate would lose.
- [X] T051 [MID] Run `ruff`, `mypy --strict` (scope per AGENTS.md) and the full `pytest -p no:schemathesis` over `graph/ agents/ eval/ prompts/ observability/ ui/ tests/`; fix findings in touched files.
- [X] T052 [MID] Run the quickstart (Scenarios 1–10) against the live stack; record pass/fail per scenario, including the five reported phrases.
- [X] T053 [MID] Write the Phase 7.8 handoff: what was built, deviations (S-1…S-3 and analyze revisions), interfaces published, calibrated values, open questions.
- [X] T054 [MID] Move ADR-059/060 to Accepted once T052 passes; re-check `checklists/requirements.md` against the final spec; re-run `/speckit-analyze` and resolve every CRITICAL/HIGH finding.
- [X] T055 [MID] Run `/speckit-converge`; the feature is done only when it reports **Converged**.

---

## Dependencies & Execution Order

- **Phase 1**: T001; then IC-011 (T002–T007 and T009) as **one commit**; T009 → T005 → T006, T002 → T003.
- **Phase 2**: depends on Phase 1; blocks all stories. T008 → T010 → T011 (T009 already landed in IC-011); T012 → T013; T014 → T015; T016 → T017; T016a → T017a; T003 (cases) **must finish before T020** (ground truth first); T018 → T020 → T021 → T022; T023 → T024; T019 and T025 independent of code.
- **US1** (T026–T030): depends on Phase 2. **US2** depends on Phase 2 and T029's template style (may run parallel to US1 in different files after T024). **US3** depends on Phase 2 and T015 (stay period). **US4** depends on Phase 2. **US5** independent after Phase 2. **US6** depends on T022, T033. **US7** depends on T003 and T021.
- **Polish**: last; T054 → T055.

### Sequential edits to the same function

`make_browse_node`'s handler registry is extended in order: T024 (skeleton) → T029 (dining) → T033 (overview) → T036 (events) → T040 (rooms) → T043 (wellness). `graph/replies.py` templates are added in the same order.

### Parallel opportunities (different top-level area, no pending dependency)

- IC-011: T002 (`eval/`) ‖ T004 (`specs/`) ‖ T005 (`prompts/`) ‖ T007 (`docs/`).
- Foundational: T019 (`docs/`) alongside the `graph/` chain; T025 alongside T021.
- US1: T030 (`agents/`) alongside T029 (`graph/`).
- US7: T046/T047 (`eval/`) alongside any `graph/` story work.

---

## Implementation Strategy

**MVP first (US1)**: Phase 1 → Phase 2 → Phase 3. **Stop and validate**: the reported dining defect is fixed live before anything else. **Then**: US2 (overview, removes the "declined" dead end), US3 (events and stay period), US4, US5, US6, US7 (gate), Polish. Any browse for a service whose handler has not landed returns the "not yet" stub line, never a crash or KB text.

### Notes

- Placeholders to confirm at calibration (R-11/T049): `browse_accuracy` floor 0.95, `browse_llm_tier_share` ceiling 0.10, tier-2 timeout.
- Deferred by decision (research R-12): bare-"and" clause splitting; past-tense narrative vs request; recommendations; cross-property browse; Option A of the recovery work.
- **Applied lesson from feature 010**: dev **and** holdout are measured and reported from the first gate run; near-miss cases guard FAQ/booking from the new rule.

---

## Requirement Coverage (traceability)

| Requirements | Tasks |
|---|---|
| FR-001–FR-004 | T016–T018, T020, T021 |
| FR-005, FR-005a | T022, T030, T031 |
| FR-006, FR-007, FR-008, FR-009 | T016a, T017a, T024, T026–T029 |
| FR-010, FR-018, FR-019 | T012–T017, T034–T036 |
| FR-011 | T041–T043 |
| FR-012, FR-020, FR-021 | T037–T040 |
| FR-013, FR-014, FR-016, FR-017 | T027, T028, T031–T033, T032a, T035 |
| FR-015, FR-015a | T023, T033, T044, T045 |
| FR-022 | T005, T006 |
| FR-023 | T031 |
| FR-024 | T008–T011 |
| FR-025 | T004 |
| FR-026–FR-028 | T002, T003, T046–T049 (FR-028: quick-fix `1904d8f`, image rebuild T050a, verified T052) |
| FR-029 | T019, T025 |
| SC-001 | T026, T031, T034, T037, T052 |
| SC-002, SC-003 | T002, T003, T047 |
| SC-004, SC-009 | T023, T027 |
| SC-005 | T022, T047 |
| SC-006 | T026, T052 (narrowed: a booking that names the venue proceeds without re-asking) |
| SC-007 | T018 (browse latency case) |
| SC-008 | T050a, T052 |
| SC-010 | T032a, T033, T044 |
| SC-011 | T030 |

## Model tier summary *(required)*

| Tier | Tasks | `[CRIT]` | Notes |
|---|---|---|---|
| FRONTIER | 18 | 17 | `[CRIT]` (17): ground-truth data test and cases (T002, T003), the classifier prompt test and template (T005, T006), the state-amendment test and serializer allowlist (T008, T011), both ADRs (T019, T025), the read-only structural test (T023), the dietary-note/forbidden-content checker (T027), the overview gaming gate and its test (T032a, T033), the gaming gate test (T044), and the eval gate work (T046–T049). Without CRIT (1): T010 — the `RoutingDecision` amendment, justified in its row. |
| MID | 31 | 0 | Recognition rules, vocabulary, `kinds.py`, date-range parser, stay period, filters, the `BROWSE` node and handlers, refinement/clarify/dining/events/stay/wellness tests, stay-length parsing, and polish runs. |
| ECONOMY | 10 | 0 | Skeleton (T001), telemetry amendment text (T004), model-tiering note (T007), enum/node constants (T009), template strings from `contracts/browse-replies.md` (T028, T032, T035, T039, T042), repo greps (T050). Total 59 tasks. |

---

## Phase 11: Convergence

- [X] T056 Make a stay request that names a length but no arrival ("book stay for next 5 days") either ask only for the arrival date or state the arrival it assumed (today it silently defaults to tomorrow and the approval text shows no dates); needs a product decision because the existing stay flow never asks, then a graph-level test per US4/AC1, FR-020, FR-018 (partial)
- [X] T057 Run quickstart scenario 10 (Langfuse span `resortflow.routing.kind = browse`) and `python -m eval.runner --tier pr` against the live stack and record the result in the Phase 7.8 handoff per plan: Phase exit (8–10 pass live) and FR-025 (partial)
- [X] T058 Fix the red gaming adversarial suite (`agents/gaming/eval/adversarial`, 20 of 40 turns, identical on the pre-011 commit `1904d8f`): loyalty questions route to booking lookup and give a self-excluded guest no mandated message, floor and responsible-gaming questions run the gate twice; as its own change, per Constitution IV and Development Standards "TEST BEFORE ADVANCE" (contradicts)
- [X] T059 Decide and record the bare-"and" browse-plus-book case ("show me the restaurants and book the steakhouse" books without showing the list; the comma form works end to end) and add a graph-level test for the comma form, per the spec edge case "A browse message that also books" and research R-12 (partial)
- [X] T060 Review and record the additions made during live verification that no task called for: the booking hand-off carry-over (`graph/routing/kinds.py::_try_browse_handoff`), `parse_explicit_range` and the stay range fix, named-venue resolution in `agents/dining/nodes/venue_discovery.py`, and the Linux test runner (`docker/test.Dockerfile`, compose service `tests`); ADR-059 carries the amendment, tasks and plan do not (unrequested)
