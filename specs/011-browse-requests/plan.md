# Implementation Plan: Browse Requests (What's Available)

**Branch**: `011-browse-requests` (cut from `fix/010-demo-quickfixes`, itself cut from the unmerged `010-tiered-intent-routing`) | **Date**: 2026-09-30 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/011-browse-requests/spec.md`

## Summary

Add a seventh request kind, `browse`, and one new graph node, `BROWSE`, so a guest can see what is
available before booking. Tier 1 recognises enumeration phrasing deterministically; the node answers
from the same MCP tools each service books through (`list_venues`, `search_events`, `list_services`,
`search_rooms`, and the existing gated gaming floor lookup) using code-composed templates — no model,
no knowledge-base text, no mutation. It also adds the two enabling capabilities the live defects
exposed: relative date-range parsing ("next N days", "for N nights", "this weekend", "from X to Y")
and stay-period resolution from the guest's own held or confirmed room. A one-turn carry-over lets
"only Japanese" narrow the previous browse, the tier-2 classifier learns the new kind and stops
declining enumeration requests, and the evaluation gate measures all of it on an independent hold-out.

**Technical approach (from research.md):**

- **`browse` is a new `RequestKind` value, not a new state key** (R-01). `routing` already carries the
  decision and already round-trips through `EncryptedSerializer`; `RoutingDecision` gains two optional
  fields (`browse_filters`, `window`) and `RequestKind` one member. `GRAPH_VERSION` 5.1 → 5.2 (minor),
  so pre-011 conversations resume. New models are added to the serializer allowlist (ADR-060).
- **Precedence** (R-02): `out_of_scope > modify > lookup-phrases > browse > possessive-lookup > book >
  smalltalk > faq`. Browse sits *above* the possessive-lookup rule 010's demo-report fix added (that rule
  is what turned "events in my stay period" into a booking lookup) and *above* FAQ (what sent "what are
  the dining venues" to the knowledge base). It needs an enumeration cue **and** a catalogue noun (or a
  strong standalone phrase), so "what time is checkout" stays FAQ (FR-004).
- **One fetch, filter locally** (R-03): `search_events` takes a single `date`, not a range, so event
  browse fetches the upcoming list once (bounded pagination) and filters to the window in code rather
  than issuing one call per day.
- **Stay period from the booking view** (R-04): `BookingRecord` gains an additive `ends_at`; the stay
  window is the arrival/departure of the guest's held or confirmed stay, never text-derived, never assumed.
- **Gaming stays behind the existing gate** (R-05): a gaming-only browse goes to the existing gaming
  standalone graph (already `require_gaming_clear` + mandated message); the general overview calls the
  same gate fresh to decide whether to show its gaming line (FR-015a).
- **Tier 2 hardening** (R-08): `browse` added to the classifier's allowed kinds (code-injected, fail-closed);
  the prompt states enumeration requests are in scope; the 1.5 s timeout is measured against the
  `UNAVAILABLE` rate seen in live testing and revisited with data, not guessed.
- **Reply templates in code** (R-07): `graph/replies.py` gains deterministic list/overview/empty/error
  templates with a hard cap of 10 items and an explicit "and N more" line; the dietary "confirmed at
  booking" wording is a code constant, not model text.

## Technical Context

**Language/Version**: Python ≥ 3.11 (`pyproject.toml`).

**Primary Dependencies**: `langgraph`, `pydantic>=2`, `pyyaml` (all existing); `graph.routing.temporal`
(feature 010) extended; the existing MCP client tool protocols. **No new runtime dependency.**

**Storage**: No new store and **no new `ResortFlowState` key**. `RoutingDecision` (already checkpointed under
`routing`) gains optional fields; new small models join the msgpack allowlist. Data files:
`graph/routing/vocabulary.yaml` (browse cues, filter vocabularies), `eval/data/routing_cases.yaml`
(browse cases), `prompts/templates/intent_routing_classification.yaml` (browse added).

**Testing**: `pytest` (+ `hypothesis` for the date-range parser's invariants). Fake MCP tools for every
service. Pre-011 checkpoint fixture for resume. Live scenarios in [quickstart.md](quickstart.md).
Note: this machine's Application Control policy blocks the `schemathesis` plugin's DLL — run with
`-p no:schemathesis` (environmental, unrelated).

**Target Platform**: Linux containers (orchestrator image) plus local Windows dev; verification console.

**Project Type**: Changes inside the existing orchestration layer (`graph/`) with small cross-track edits.

**Performance Goals**: Tier-1 browse adds < 5 ms median (SC-007). A browse reply is one MCP read (event
browse: one bounded fetch); no model call on the deterministic path.

**Constraints**: Read-only (no hold, no ledger write, no wager). Node names additive only. Tenant and
property come from the token, never message text. Dietary wording never implies safety. Gaming gate
fresh every turn. Guest text never in new trace locations.

**Scale/Scope**: ≈ 8 new vocabulary cue groups; ≥ 96 new labelled routing cases (12 categories of
browse and near-miss × 2 tenants × ≥ 4); 1 new node; 1 new enum member; 2 new small models; 1 prompt
revision; 2 ADRs.

## Constitution Check

*GATE: evaluated before Phase 0 and re-checked after Phase 1 design. Result: **PASS**, no unjustified violations.*

| Principle | Assessment | Result |
|---|---|---|
| **I. Ambiguity-based routing** | Browse is decided at tier 0/1 from phrasing; the model sees only the genuinely ambiguous remainder and may now *return* browse. Deterministic paths stay first-class and are the fallback: with tier 2 down, every deterministic browse phrasing still answers (SC-005). No new LLM node. | PASS |
| **II. Governed integration surface** | Every catalogue read goes through the existing tool protocols via `tools_factory(tenant_id, guest_id)`; no vendor SDK, no direct sim access. Tenant comes from the token/state, never message text (FR-007). | PASS |
| **III. Two-phase mutation** | Browse performs **no mutation** at all (FR-015, SC-009): no hold, no ledger row, no purchase. A follow-up booking runs the unchanged hold→approve→commit path. | PASS |
| **IV. Policy is code** | The responsible-gaming gate (`require_gaming_clear`) runs fresh per turn for both a gaming browse and the overview's gaming line; the dietary "confirmed at booking" wording and the "your property only" statement are code constants, not prompts. | PASS |
| **V. Durable, inspectable state** | The browse decision is recorded on the existing `routing` key (kind, services, filters, window, tier) and traced with the existing `resortflow.routing.*` attributes. Additive fields + minor `GRAPH_VERSION`; pre-011 resume tested; serializer allowlist updated, no pickle. | PASS |
| **VI. Declared model tier** | No new runtime model-calling node. The one changed model prompt (tier-2 classifier) stays economy-tier, static, no escalation (`docs/model-tiering-runtime.md` row unchanged, note added). Build-time tiers per task declared in tasks.md. | PASS |
| Determinism first | Cue recognition, filter extraction, date-range parsing, stay-period resolution, and all reply text are functions/templates. | PASS |
| Contract stability | New node name additive; `RoutingDecision`/`RequestKind` extended additively; frozen reducers, MCP contracts and `ResortFlowState` key set untouched. | PASS |
| Observability | `resortflow.routing.kind` gains the value `browse` (bounded dimension, additive) — noted as an amendment to the 001 telemetry contract in IC-011. No new message-text sink. | PASS |
| File-disjoint tracks | Primary work in Track C (`graph/`, `agents/tests/`). Cross-track *interface* edits (prompt template, eval data, observability contract note, AGENTS.md pointer) land in **one integration commit, IC-011**, in tasks Phase 1; later tasks each stay inside one track. | PASS |

**Post-design re-check (after Phase 1)**: unchanged, PASS. One watch item, not a violation: event browse
follows at most three pages of `search_events` results — a very large calendar would be truncated with an
honest "and N more" rather than fully enumerated (R-03).

## Spec amendments found during research

- **S-1 (FR-010/SC-008)**: `search_events` accepts a single `date`, not a range; range browsing is
  therefore a bounded fetch plus a local filter. No spec text changes; recorded in R-03.
- **S-2 (FR-019)**: `BookingRecord` has no end date today, only `starts_at`; the stay period needs the
  departure. An additive `ends_at` (sourced from the `Proposal`) closes this. Recorded in R-04.
- **S-3 (FR-012)**: room search needs an adult count; when the guest gave none the browse assumes 2 and
  says so, consistent with how the stay booking path already defaults occupancy. Recorded in R-06.

## Project Structure

### Documentation (this feature)

```text
specs/011-browse-requests/
├── spec.md
├── plan.md              # this file
├── research.md          # Phase 0: R-01 … R-12
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   ├── browse-router-api.md
│   ├── graph-topology-v011.md
│   ├── browse-replies.md
│   ├── browse-classifier-prompt-delta.md
│   ├── state-schema-amendment-011.md
│   └── browse-eval-gate.md
├── checklists/requirements.md
└── tasks.md             # Phase 2 (/speckit-tasks, not created here)
```

### Source Code (repository root: `resortflow/`)

```text
graph/                                   # Track C
├── models.py                            # + RequestKind.BROWSE, BrowseFilters, DateWindow; RoutingDecision += browse_filters, window
├── nodes.py                             # + BROWSE
├── routing/
│   ├── vocabulary.yaml                  # + kinds.browse (cues, catalogue nouns, narrowing cues), browse.filters vocabularies
│   ├── kinds.py                         # browse precedence, filter extraction, browse carry-over, browse+book clause pair, booking hand-off after a browse (`_try_browse_handoff`, SC-006)
│   ├── temporal.py                      # + parse_date_range, parse_stay_length, parse_explicit_range, STAY_PERIOD marker
│   ├── browse.py                        # NEW: extract_filters(), resolve_window(), wants_stay_period(), TenantProfile/detect_other_property() (pure)
│   └── tests/                           # test_browse_rules.py, test_date_range.py, test_browse_filters.py, test_tenant_profile.py
├── bookings.py                          # BookingRecord += ends_at; stay_period(records)
├── replies.py                           # + browse templates (list / overview / empty / unavailable / dietary note)
├── browse_node.py                       # NEW (as built): BrowseContext, per-service handlers, compose_browse_reply — read-only, checkable by one structural test
├── supervisor.py                        # thin BROWSE node wrapper + edge, clarify-count reset on browse, GRAPH_VERSION 5.2, PreviousTurn carries filters/window
└── tests/                               # test_state_amendment_011.py, test_stay_period.py
agents/
├── dining/serde.py                      # allowlist: BrowseFilters, DateWindow  [CRIT]
├── stay/nodes/intent_parser.py          # nights from "next N days"/"N nights"; explicit "oct 5 to oct 8" range via temporal
├── dining/nodes/venue_discovery.py      # a venue named in the message is proposed (SC-006)
└── tests/                               # test_browse_dining.py, test_browse_events.py, test_browse_wellness.py,
                                         #   test_browse_stay.py, test_browse_gaming_gate.py, test_browse_refinement.py,
                                         #   test_browse_clarify.py, test_browse_overview_gate.py, test_browse_readonly_structural.py
prompts/                                 # cross-cutting (009 owner)
└── templates/intent_routing_classification.yaml   # + browse kind + in-scope-default rule
eval/                                    # Track E
├── data/routing_cases.yaml              # + browse and near-miss cases, dev/holdout split
├── routing_scorer.py                    # + browse metrics
├── runner.py                            # + floors (browse_accuracy, browse_catalogue_misroute)
└── tests/test_browse_cases_data.py
observability/attributes.py              # kind value set note only (no new attribute)
specs/001-contracts-foundation/contracts/telemetry-attributes.md   # + Amendment — Feature 011 (kind=browse)
docs/adr/059…060                         # see ADR table
docs/model-tiering-runtime.md            # note only: classifier prompt revised, tier unchanged
docker/test.Dockerfile, compose service `tests` (profile `test`)   # Track F: the suite on Linux, where Windows Smart App Control blocks native wheels
```

**Structure Decision**: Browse cue/filter/window logic lives in `graph/routing/` (pure, no I/O, so it is
structurally testable and cannot import a tool or a model), while the tool calls and reply composition
live in the `BROWSE` node in `graph/supervisor.py` plus templates in `graph/replies.py`. That keeps the
existing rule that the routing package imports neither a vendor tool nor `openai`, and keeps the
read-only guarantee checkable by one structural test over the node's tool calls (T023 in tasks.md). Tenant display names and
the other-property check are a small code constant in the same module (`TenantProfile`, data-model.md).

## Complexity Tracking

No constitution violations to justify. Deliberate complexities recorded for review:

| Complexity | Why needed | Simpler alternative rejected because |
|---|---|---|
| `RoutingDecision` gains two optional fields | one-turn refinement (FR-005a) and the reply's "dates I used" statement need the previous browse's filters/window to survive the turn | re-deriving them from message text is impossible for "only Japanese" (no service or cue in it) and non-reproducible when tier 2 decided |
| A new node rather than routing browse into `KNOWLEDGE_QA` | the answer must come from a provider, be bounded and read-only, and never fall back to KB text (FR-006/FR-017) | extending `KNOWLEDGE_QA` would blur the "documents only" boundary that keeps the injection-screening rule simple (ADR-038) |
| Gaming browse routes to the existing standalone graph instead of the new node | that graph already implements the gate + mandated-message + jurisdiction wording; duplicating it would create a second copy of a policy-critical path | a second implementation could drift from the responsible-gaming rules |

## Architecture Decision Records *(mandatory — ResortFlow project override)*

| ADR | Decision | Status |
|-----|----------|--------|
| 059 | Browse as a deterministic, read-only request kind answered from providers: precedence over FAQ and possessive lookup, why not knowledge base / model, gaming stays behind the existing gate, bounded lists, honest failure (FR-029; states how it preserves deterministic-first routing and the read-only guarantee) | [X] drafted / [X] complete — Accepted |
| 060 | `RoutingDecision`/`RequestKind` amendment 011: `browse` kind + optional `browse_filters`/`window`, no new state key, minor `GRAPH_VERSION` 5.1→5.2, one-turn refinement carry-over, serializer allowlist entries | [X] drafted / [X] complete — Accepted |

Each contains Context, Options Considered, Decision, and Consequences.

Not ADR-worthy (routine application of existing decisions): the date-range parser (extends feature 010's
temporal module), the vocabulary additions (FR-004 of 010), the prompt revision (ADR-052/053 registry
flow), the eval cases (ADR-042 gate), the clarify wording.

## Build-time tier guidance for `/speckit-tasks`

- **FRONTIER / `[CRIT]`**: both ADRs; the browse ground-truth cases and their data test (authored before,
  and independently of, the new vocabulary, with a dev/holdout split — feature 010's D1 lesson, applied from
  day one, including the *near-miss* cases that guard FAQ/booking); the read-only structural test (Principle
  III); the gaming gate wiring and its four-way test (cleared / restricted / unavailable / self-excluded
  overview); the serializer allowlist and `RoutingDecision` amendment (Principle V); the eval scorer metrics,
  gate wiring and calibration; the tier-2 prompt revision (policy surface).
- **MID**: browse cue/filter vocabulary content (collision and near-miss judgment); `kinds.py` precedence and
  carry-over changes; the date-range parser; the `BROWSE` node, per-service handlers and reply templates;
  stay-period resolution; the classifier prompt template edit; non-trivial tests.
- **ECONOMY**: node-name constant; enum member; `ends_at` field plumbing; telemetry-contract amendment text;
  repo-wide mechanical checks; docstrings.

## Phase exit

- `/speckit-converge` reports Converged; quickstart scenarios 1–7 pass offline, 8–10 pass live.
- research.md records calibrated values: browse accuracy floor, tier-2 timeout decision.
- the Phase 7.8 handoff written: what was built, deviations (S-1 … S-3), interfaces published (the six
  contracts), open questions.
