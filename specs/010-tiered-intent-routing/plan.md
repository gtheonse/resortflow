# Implementation Plan: Tiered Intent Routing

**Branch**: `010-tiered-intent-routing` | **Date**: 2026-09-28 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/010-tiered-intent-routing/spec.md`

## Summary

Replace the substring keyword scan that decides what a guest wants with a three-tier router, and
fix how booking outcomes are reported. Tier 0 matches service vocabulary as whole words, tolerates
typos (a neighbouring-letter swap counts as one edit) and accepts word variants. Tier 1 decides the
request kind (`book`, `lookup`, `modify`, `faq`, `out_of_scope`, `smalltalk`) and handles one-turn
follow-ups. Tier 2 is a constrained, economy-tier model classifier used only for the cases the
first two tiers cannot decide. Four small deterministic nodes are added: booking lookup, modify
hand-off, decline and small talk. Knowledge answers now require a minimum relevance. Booking
replies report only what happened this turn, each booking is recorded once, and the console stops
counting down finished holds. A labelled routing set becomes part of the eval gate.

**Technical approach (from research.md):**

- **The decision moves into the `INTENT_ROUTER` node and into state** (R-01). Today it is computed
  in a conditional edge, so it is never recorded, never traced, and never visible to the next turn.
  `DETERMINISTIC` and `PLANNER` read the decision instead of re-scanning keywords.
- **Additive `ResortFlowState` amendment** (R-02, ADR-056): `routing` and `turn_baseline`, one writer
  each, registered in the serializer allowlist. `GRAPH_VERSION` 5.0 → 5.1 (minor, so pre-010
  conversations resume).
- **Vocabulary as reviewable YAML** (R-04/R-05) with explicit variants, phrases, exclusions and a
  load-time cross-service collision check. `stay` alone stops being a trigger.
- **Tier 2** (R-07/R-08, ADR-055): a prompt from the feature-009 registry, `gpt-4.1-mini` (economy,
  static), JSON output validated against code enums, 1.5 s timeout, no retry. Every failure
  becomes the existing clarifying question. It sees only the previous turn's kind and services.
- **Booking lookup without a model** (R-10): a read-model joined on `idempotency_key` and deduped.
  A new date parser handles "Sep 30" (the existing one cannot). Live hold re-check is possible only
  for dining; the other services are reported honestly as "could not be confirmed right now".
- **Relevance floor** (R-11, ADR-057) aimed at *off-topic* questions only. The documented earlier
  failure of a score threshold on *availability* questions is respected by keeping that path's
  deterministic short-circuit. No `rag/` code change: `retrieve_hits` already exposes the score.
- **Per-turn reporting** (R-12, ADR-058): a turn baseline, a writer fix at the `DETERMINISTIC`
  copy-back that stops re-appending confirmations, reader-side dedupe for old checkpoints, and no
  change to the frozen reducers.

Research found three facts the spec did not know. They are recorded as amendments S-1 to S-3
below.

## Technical Context

**Language/Version**: Python ≥ 3.11 (`pyproject.toml`; CI pins 3.11). Console: vanilla JS in
`ui/static/index.html`.

**Primary Dependencies**: `langgraph` (existing), `pydantic>=2`, `pyyaml`, `openai` (existing;
`AsyncAzureOpenAI` for tier 2), `prompts.registry` (feature 009). **No new runtime dependency**:
the edit distance is about 30 lines of pure Python (R-03); `rapidfuzz` was rejected.

**Storage**: No new store. Two additive checkpointed state keys (Postgres checkpointer, encrypted
serializer). Data files: `graph/routing/vocabulary.yaml`, `eval/data/routing_cases.yaml`,
`rag/eval/negative_set.yaml` (extended), `prompts/templates/intent_routing_classification.yaml`,
`prompts/prompts.lock.json` (entry added).

**Testing**: `pytest` (+ `hypothesis` for matcher properties: whole-word only, never matches a
strict substring, swap symmetry). Fake classifier for the tier-2 failure matrix. Fixture
checkpoints for resume and dedupe. Opt-in live scenarios in [quickstart.md](quickstart.md).

**Target Platform**: Linux containers (orchestrator image) plus local Windows/macOS dev; verification
console on localhost.

**Project Type**: Changes inside the existing monorepo's orchestration layer (`graph/`), with
small cross-track edits (see Project Structure).

**Performance Goals**: tiers 0–1 add < 5 ms median per message (SC-007). Tier 2 ≤ 1.5 s hard
timeout and ≤ 20% of traffic (SC-005). Knowledge answer latency unchanged (same retrieval call).

**Constraints**: offline-capable (tier 2 absent ⇒ deterministic plus clarify, SC-006). Node names
additive only. Frozen reducers and MCP contracts untouched. Guest text never in a system message
or in new trace locations. `tenant_id` never from model output.

**Scale/Scope**: about 200 vocabulary entries across 5 services; ≥ 176 labelled routing cases; 4 new
nodes; 2 new state keys; 1 new prompt; 4 ADRs.

## Constitution Check

*GATE: evaluated before Phase 0 and re-checked after Phase 1 design. Result: **PASS**, no
unjustified violations.*

| Principle | Assessment | Result |
|---|---|---|
| **I. Ambiguity-based routing** | The model is invoked only when tiers 0–1 report low confidence (FR-009), never for smalltalk or out-of-scope, and its share is gated at ≤ 20% (SC-005). Single-domain, low-ambiguity requests stay deterministic, now more of them (typos no longer fall through). ADR-055 justifies the one new LLM node, as the principle requires. | PASS |
| **II. Governed integration surface** | Tier 2 is a model call, not a vendor tool, and uses the existing Azure client pattern. The lookup re-check uses the existing dining MCP tool `check_dining_status`. No new SDK in agents. `tenant_id` is never read from classifier output (FR-012; the `ClassifierOutput` schema has no such field). | PASS |
| **III. Two-phase mutation** | No new mutation. `modify` is handed off, not performed. A misroute (including a prompt-injected one) can at worst start the existing hold → approve → commit flow, which keeps all its guards. The turn baseline does not change saga behaviour. | PASS |
| **IV. Policy is code** | The out-of-scope decline, the gaming read-only rule and every reply are deterministic code. Allowed kinds and services are code-injected into the prompt (fail-closed required vars). Policy post-flight and the Offer Agent self-exclusion gate are called by every new END-bound node (topology contract). | PASS |
| **V. Durable, inspectable state** | The routing decision becomes inspectable state (it is lost today). Additive keys, minor version, pre-010 resume tested, serializer allowlist updated, no pickle. Holds are still revalidated before being reported as held (dining live; others explicitly "could not be confirmed"). | PASS |
| **VI. Declared model tier** | The tier-2 node declares **economy runtime tier (`MODEL_CHEAP`), static**, no escalation (R-07). Build-time tiers per task are declared in tasks.md. `[CRIT]` candidates: the `DETERMINISTIC` copy-back fix (transaction-integrity adjacent), eval metric implementation, the serializer allowlist, and all ADRs, none on economy. | PASS |
| Determinism first | Tiers 0–1, lookup, date parsing, replies and floor application are all functions. The one model call covers only the unbounded remainder. | PASS |
| Contract stability | Node names additive; state amendment through ADR-056; `escalate_reason` appended; frozen reducers, MCP contracts and `turn_summary` shape unchanged. | PASS |
| Observability | `resortflow.routing.*` attributes defined only in `observability/attributes.py`. The tier-2 call goes through `start_generation_span`. No new message-text sinks. | PASS |
| File-disjoint tracks | Primary work in Track C (`graph/`, `agents/`, and `ui/`, assigned to Track C by this feature). Every cross-track **interface** edit (`observability/` attributes + helper + 001 contract amendment, `prompts/` owner group + template + lock, `eval/` and `rag/eval/` scaffolds, the AGENTS.md row) lands in **one integration commit** in tasks Phases 1–2; later tasks each stay inside one track. `[P]` only across different top-level areas (009 precedent). | PASS (fixed at analyze C1/C2) |

**Post-design re-check (after Phase 1)**: unchanged, PASS. Design surfaced one watch item, not a
violation: `MODIFY_REQUEST` uses the existing hand-off, which stops automation for the rest of the
conversation. That is existing behaviour, disclosed in ADR-055; changing it is out of scope.

## Spec amendments found during research

- **S-1 (FR-015)**: only dining has a hold-status tool. Wellness, stay and entertainment holds
  cannot be re-checked without changing frozen MCP contracts. They are reported as "held — status
  could not be confirmed right now", which is FR-015's own failure wording. No spec text changes,
  but acceptance scenario US2-AS2 is exercised with dining. Recorded in R-10.
- **S-2 (SC-010)**: the existing date parser cannot read "Sep 30", so the defect reproduction
  depends on the new `graph/routing/temporal.py`. Recorded in R-10.
- **S-3 (FR-021/FR-022)**: a relevance threshold previously failed on availability questions. The
  floor is scoped to off-topic questions and calibrated against a new off-topic set. The
  availability short-circuit is unchanged. Recorded in R-11.
- **S-4 (SC-008)**: live calibration (T079, research.md R-16) found the answerable and off-topic
  score distributions overlap by exactly one case each — no single floor achieves both 100%
  answerable-kept and 100% off-topic-refused. The calibrated floor (0.75) is set to guarantee
  100% answerable-kept, accepting one known adversarial-adjacent off-topic case ("what's a good
  recipe for clam chowder?", which lexically overlaps real dining-menu content) as a disclosed
  residual gap rather than forcing the number to claim a clean 100%/100% that measurement does
  not support. SC-010's own out-of-scope example remains comfortably refused at this floor.

## Project Structure

### Documentation (this feature)

```text
specs/010-tiered-intent-routing/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0: R-01 … R-15 (R-16 calibration appended at implementation)
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   ├── state-schema-amendment-010.md
│   ├── graph-topology-v010.md
│   ├── router-api.md
│   ├── routing-classifier-prompt.md
│   ├── turn-replies.md
│   └── routing-eval-gate.md
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks, not created here)
```

### Source Code (repository root: `resortflow/`)

```text
graph/                                   # Track C
├── routing/                             # NEW package
│   ├── __init__.py
│   ├── vocabulary.yaml                  # reviewable data (FR-004, FR-002a)
│   ├── vocabulary.py                    # load, validate, collision check
│   ├── thresholds.yaml                  # knowledge_relevance_floor (calibrated; Track C owns it)
│   ├── matcher.py                       # tier 0 (OSA distance, whole words, phrases)
│   ├── kinds.py                         # tier 1 + clause split + carry-over
│   ├── temporal.py                      # "sep 30", "8 PM", ...
│   ├── classifier.py                    # tier 2 (only module with a model call)
│   ├── router.py                        # route()
│   ├── explain.py                       # debug CLI (python -m graph.routing.explain)
│   └── tests/
├── bookings.py                          # NEW: BookingRecord view, turn_outcomes, lookup matcher
├── replies.py                           # NEW: deterministic reply templates
├── models.py                            # + RequestKind, RoutingTier, Tier2Outcome, RoutingDecision, TurnBaseline
├── state.py                             # + routing, turn_baseline (additive; reducers untouched)
├── nodes.py                             # + BOOKING_LOOKUP, MODIFY_REQUEST, DECLINE, SMALLTALK
├── supervisor.py                        # router node, edge map, 4 new nodes, reply composer, copy-back fix,
│                                        #   KNOWLEDGE_QA floor, GRAPH_VERSION 5.1, _DOMAIN_KEYWORDS removed
└── tests/test_state_amendment_010.py
agents/
├── dining/serde.py                      # allowlist: new state types  [CRIT]
└── tests/                               # test_turn_reporting.py, test_booking_lookup.py, test_decline_and_smalltalk.py, test_modify_handoff.py,
                                         #   test_knowledge_qa.py (fakes gain retrieve_hits), test_injection_structural.py (+ graph/routing)
observability/                           # cross-cutting
├── attributes.py                        # + resortflow.routing.*
└── telemetry.py                         # + annotate_routing()
prompts/                                 # cross-cutting (009 owner)
├── templates/intent_routing_classification.yaml
├── owners.yaml                          # + platform group
└── prompts.lock.json                    # + entry via sync flow
eval/                                    # Track E
├── routing_scorer.py
├── runner.py                            # _score_routing, floors
├── data/routing_cases.yaml
└── tests/test_routing_cases_data.py, test_routing_gate_degraded.py
rag/eval/                                # Track D (data + calibration script only; no retriever change)
├── negative_set.yaml                    # + offtopic section
├── runner.py                            # run off-topic section
└── calibrate_floor.py
ui/static/index.html                     # countdown only for pending/held; badge from booking_outcome (FR-035)
ui/app.py, graph/turn_summary.py         # additive booking_outcome field
AGENTS.md                                # + ui/ row under Track C (integration commit)
specs/001-contracts-foundation/contracts/telemetry-attributes.md  # + Amendment — Feature 010
docs/adr/055…058                         # see ADR table
```

**Structure Decision**: Routing logic gets its own package under `graph/routing/` so that the
one model call is isolated in one module (`classifier.py`). That makes it structurally testable
(no other routing module imports `openai` or `prompts.registry`), and keeps `supervisor.py` from
growing past its current 2,100 lines with matching logic. Booking read-model and reply templates
are separate modules because four nodes share them.

## Complexity Tracking

No constitution violations to justify. Two deliberate complexities are recorded for review:

| Complexity | Why needed | Simpler alternative rejected because |
|---|---|---|
| Two new state keys | carry-over (FR-008a) and per-turn reporting (FR-031) need facts that survive the turn | re-deriving from messages is non-reproducible when tier 2 decided; hiding them in message metadata is not inspectable state (R-02) |
| Reader-side dedupe *and* writer fix | pre-010 checkpoints already contain duplicates | writer fix alone leaves old conversations wrong; a reducer change touches frozen `[CRIT]` code and hides the bug (R-12) |

## Architecture Decision Records *(mandatory — ResortFlow project override)*

| ADR | Decision | Status |
|-----|----------|--------|
| 055 | Tiered intent routing: tolerant deterministic tiers first; a constrained, economy-tier model classifier only for low-confidence messages, with every failure falling back to clarify (FR-030; states how most traffic stays deterministic, bounded injection impact, hand-off lock-in consequence for `modify`) | [X] drafted / [X] complete (Accepted, T077) |
| 056 | `ResortFlowState` amendment 010: `routing` + `turn_baseline`, single-writer, additive, minor graph version; `escalate_reason += "modify_request"` | [X] drafted / [X] complete (Accepted, T077) |
| 057 | Knowledge-answer relevance floor scoped to off-topic questions, calibrated on a labelled off-topic set; availability questions keep the deterministic short-circuit (reconciles with the earlier failed threshold) | [X] drafted / [X] complete (Accepted, T077) |
| 058 | Booking replies report the current turn's outcomes via a key-set baseline with a `turn_index` (option d); confirmations are fixed at the writer (`DETERMINISTIC` copy-back) and deduped by readers, not by changing frozen reducers; the console badge reports the booking outcome (FR-035); a turn-stamped saga event log is recorded as the follow-up target (option b) with the last-10-events risk stated | [X] drafted / [X] complete (Accepted, T077) |

Each contains Context, Options Considered, Decision, and Consequences.

Not ADR-worthy (routine application of existing decisions): the vocabulary as YAML (application of
FR-004), the prompt going through the 009 registry (ADR-052/053), the eval metrics going through the
007 gate (ADR-042), the console countdown fix (a UI bug fix).

## Build-time tier guidance for `/speckit-tasks`

- **FRONTIER / `[CRIT]`**: the four ADRs; the routing ground-truth cases and their data test —
  authored before, and independently of, the vocabulary, with a dev/holdout split (analyze D1:
  a vocabulary-fitted set makes the gate decorative); the tier-2 output validator and failure
  matrix (policy enforcement); the degraded-gate test, `routing_scorer` metrics, gate wiring and
  the threshold calibration that flips a metric to blocking; the state amendment, serializer
  allowlist and booking-view dedupe (Principle V); the turn-scoped reporting fix — regression
  suite, `turn_outcomes`, the reply composer, the `DETERMINISTIC` copy-back fix, and the
  `booking_outcome` summary field the console badge trusts.
- **MID**: router tiers 0–2 and their orchestration; `vocabulary.yaml`'s **content** (collision
  and exclusion judgment, so MID, not ECONOMY — analyze D1); temporal and booking-match parsing;
  the lookup, decline, smalltalk and modify nodes and their wiring; the knowledge floor and its
  calibration script; the telemetry helper; the classifier prompt template.
- **ECONOMY**: package skeletons; node-name and telemetry-attribute constants; the owner-group
  addition; the `observability/` contract-amendment text; the off-topic data entries; reply
  template strings from `turn-replies.md`; the console badge/countdown condition; `explain` CLI;
  repo-wide mechanical checks; docstrings.

## Phase exit

- `/speckit-converge` reports Converged; quickstart scenarios 1–5 and 9 pass in CI, and 6–8 pass
  live.
- research.md R-16 records the calibrated floors (routing accuracy, LLM share, relevance floor), and
  `llm_tier_share` flips to blocking.
- the Phase 7.7 handoff written: what was built, deviations (S-1 … S-3), interfaces published
  (the six contracts), open questions.
