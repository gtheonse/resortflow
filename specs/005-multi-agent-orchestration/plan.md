# Implementation Plan: Multi-Agent Orchestration

**Branch**: `005-multi-agent-orchestration` | **Date**: 2026-09-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/005-multi-agent-orchestration/spec.md`

## Summary

Assemble the real multi-agent system this project has been building toward since Phase
0: a Supervisor that fans a guest request out across up to five domain subgraphs
(dining — already built in Phase 4, reused unmodified; entertainment, stay, wellness,
gaming — new this feature), runs a deterministic conflict check with no LLM in the loop,
takes a guest through hold → approve → sequential commit → verify, and recovers from
partial failure by proposing a real alternative rather than cancelling everything. Two
new guest-facing turns are added on top of what Phase 4 proved: a bounded clarification
loop for incomplete requests, and a bounded replan loop that terminates in human
escalation under adversarial input. Technical approach is almost entirely reuse of
already-frozen interfaces (`graph.state.ResortFlowState`, `graph.ledger.HoldLedger`,
`graph.models`, `agents.dining.*`, `rag.HybridRetriever`) plus five architecturally
significant additions, each recorded as its own ADR: resolving the two ambiguous node
names left in the registry, moving every domain (including dining) onto real MCP
wire-transport, closing the hold-ledger reaper gap, one additive `ResortFlowState` amendment,
and a new node for the clarification turn.

## Technical Context

None of the following carries `NEEDS CLARIFICATION` — this feature's stack is fixed
project-wide (`AGENTS.md`) and already implemented across features 001-004; this
feature's actual unknowns were architectural (resolved above and in `research.md`, each
producing an ADR where the bar was met), not technological.

**Language/Version**: Python 3.11+ (matches `agents/dining/`, `graph/`, `mcp/gateway/`)

**Primary Dependencies**: LangGraph (supervisor + domain subgraphs), Pydantic v2
(`graph.models`, all MCP response models), FastMCP (`mcp/gateway/`), `psycopg`
(`PostgresHoldLedger`), Azure OpenAI SDK (Planner/Recovery/judge-tier calls, gpt-4.1;
domain subgraphs, gpt-4.1-mini), Qdrant client (via `rag.HybridRetriever`, reused
unmodified)

**Storage**: PostgreSQL — `langgraph-checkpoint-postgres` (conversation state,
`EncryptedSerializer`) and the separate `hold_ledger` table (external side effects,
`graph.ledger.PostgresHoldLedger`) — two schemas of record in one database, per ADR-019,
unchanged by this feature except for the additive `mark_orphan_released` method (ADR-022)

**Testing**: pytest, following `agents/dining/eval/`'s existing pattern
(`run_case`/`run_all`/`write_results`) generalized per domain (see `quickstart.md`);
`tests/test_reducers.py`-style property tests for any new reducer-adjacent logic;
`agents/tests/test_fanout_pending_write_safety.py` (new, this feature) as the concurrency
property test named directly by the spec's edge cases

**Target Platform**: Linux server (docker-compose locally, matching `make up`; Azure in
the eventual production target per `AGENTS.md`'s Observability row)

**Project Type**: Multi-service backend (LangGraph orchestration service + MCP gateway
service + FastAPI simulators), file-disjoint by track per `AGENTS.md`'s track table —
not a web frontend/mobile project, so the template's Option 2/3 layouts do not apply

**Performance Goals**: Inherits Constitution Principle I's existing bound — any flow
under a 1.5s latency budget stays off the multi-hop agentic path; `Deterministic` and
`CLARIFY`'s classification re-run both target that bound. No new numeric target beyond
what Principle I already fixes.

**Constraints**: Every mutating action stays hold-then-confirm (Principle III); every
external mutation call carries an idempotency key (existing formula, generalized per
domain); `tenant_id` never derived from model output (Principle II, unchanged); gaming
stays read-only by absence of mutating tools (already true — verified in
`mcp/gateway/gaming/server.py`, unchanged by this feature)

**Scale/Scope**: Two tenants (`MGV`, `MHB`), five domains fanning out concurrently per
request (up from one), a frozen 22-node topology growing to 23 (one additive `CLARIFY`
node)

## Constitution Check

*GATE: evaluated before Phase 0 research and re-evaluated after Phase 1 design below.*

| Principle | Status | Notes |
|---|---|---|
| I. Ambiguity-Based Routing | **PASS** | `IntentRouter`/`Deterministic` fork unchanged; `Planner` and `Recovery` are the two LLM-invoking nodes this feature adds real substance to, both justified by genuine unbounded judgment (task decomposition across dependent domains; "what's the best partial outcome" per ADR-006). `CLARIFY` (new) is designed specifically so a request resolving to simple/deterministic after clarification never enters `Supervisor` (ADR-024) — the routing principle is honored *by* the new node's placement, not worked around by it. |
| II. Governed Integration Surface | **PASS, with required work** | `tenant_id` injection unchanged (server-side, from token claim). Gaming read-only confirmed unchanged (no mutating tool exists in `mcp/gateway/gaming/server.py`). Required work: finish `mcp/gateway/entertainment/server.py` (absent today) and move every domain — dining included — onto real MCP wire-transport (ADR-021), closing the Phase 4 handoff's flagged open question. |
| III. Two-Phase Mutation | **PASS** | Hold→propose→approve→commit→verify→compensate applies unchanged to entertainment/stay/wellness, following `agents/dining/`'s exact pattern. Sequential riskiest-first commit ordering resolved in `research.md` (entertainment, then dining/wellness, then stay). Idempotency keys generalize the existing formula per domain (no shared cross-domain function — each domain's identifiers are domain-local). Semantic compensation is `RECOVERY`'s explicit job (spec.md User Story 2); a compensation failure escalates to the now-real `HUMAN_HANDOFF` (ADR-020) with `escalate_reason="compensation_failed"` (ADR-023), satisfying the dead-letter-and-escalate requirement. |
| IV. Policy Is Code, Not Prompt | **PASS — explicitly deferred, not violated** | `PolicyPreflight`/`PolicyPostflight` are compiled as pass-through nodes this feature (topology contract, `contracts/graph-topology-v005.md`) — present so Phase 6 has a real attachment point, performing no compliance evaluation yet. `OFFER_AGENT` deliberately stays uncompiled specifically because its self-exclusion precondition is Policy Engine substance this feature does not build (ADR-020) — the gate this principle requires is honored by *not* shipping the node it would otherwise gate, not bypassed. |
| V. Durable, Inspectable State | **PASS, with required work** | Checkpointer/ledger separation unchanged (ADR-019 stands). Required work, each with its own ADR: close the hold-ledger reaper gap now that concurrent multi-domain holds raise the orphan-risk surface (ADR-022); every new cyclic path (`Clarify` loop, `Planner` replan loop including the `HumanApproval -> Planner: modified` edge) carries an explicit bound and escalates to a real `HUMAN_HANDOFF` node (ADR-020, ADR-023, ADR-024) — satisfying "every cyclic path... escalation to a human at the bound" for the first time this project has more than one such cycle. `graph_version` pinning (ADR-023) closes `ResortFlow-HITL-Durability-State.md` §8's previously-unaddressed gap, now load-bearing because this feature is the first with concurrent, long-lived interrupts across five domains. |
| VI. Declared Model Tier | **PASS** | *(Runtime tiering — which Azure OpenAI model a node calls when serving a guest, per `docs/models.md`'s own runtime/build-time distinction table. This row is entirely separate from `tasks.md`'s `[FRONTIER]`/`[CRIT]` column, which is build-time — which Claude Code model authors the task. `docs/models.md` explicitly warns against conflating the two; `docs/model-tiering-runtime.md`, the file it names as runtime tiering's authority, does not exist in the repo yet, so treat "mid tier" below as this feature's stated intent, not a value pulled from an existing runtime-tiering document.)* Supervisor, Planner, Recovery Agent: frontier tier (per the existing roster table, unchanged by this feature). New domain subgraphs (entertainment, stay, wellness, gaming): mid tier, matching dining's existing tier. Conflict Checker: no model at all (None tier) — the static travel-time matrix (ADR-025) keeps it that way rather than reaching for an LLM or an external service to approximate arithmetic. `CLARIFY`'s question-generation is not on the `[CRIT]` list (`docs/models.md`) — declared at task-assignment time in `tasks.md`, not decided here. |

No Constitution violation requires a Complexity Tracking entry — see below.

## Project Structure

### Documentation (this feature)

```text
specs/005-multi-agent-orchestration/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/            # Phase 1 output
│   ├── graph-topology-v005.md
│   ├── entertainment-mcp-tool-contract.md
│   ├── state-schema-amendment-005.md
│   └── hold-ledger-reaper-contract.md
└── tasks.md              # Phase 2 output (/speckit-tasks — not created by this command)
```

### Source Code (repository root)

This is not a single-project or web-application layout — it is this project's own
file-disjoint track structure (`AGENTS.md`'s track table), already established across
features 001-004 and unchanged in shape by this feature, only populated further:

```text
agents/
├── dining/               # UNCHANGED — mounted as-is under graph.nodes.DINING
├── entertainment/        # NEW — state.py, tools.py (McpEntertainmentTools), graph.py, eval/
├── stay/                 # NEW — same shape
├── wellness/             # NEW — same shape
├── gaming/               # NEW — read-only: no hold/commit nodes, no tools.py mutating methods
└── tests/
    └── test_fanout_pending_write_safety.py   # NEW — concurrency property test

graph/
├── nodes.py              # AMENDED (additive) — CLARIFY added; HUMAN_HANDOFF now compiled
├── state.py               # AMENDED (additive) — graph_version, clarification_count,
│                           #   escalate_reason, MAX_CLARIFICATION_COUNT
├── ledger.py               # AMENDED (additive) — mark_orphan_released
├── models.py               # AMENDED (additive) — Conflict
├── mcp_client.py           # NEW — shared real MCP wire-transport client (ADR-021)
├── travel_time.py          # NEW — static per-tenant zone matrix (ADR-025)
├── supervisor.py           # NEW — the Supervisor StateGraph itself: IntentRouter, Clarify,
│                           #   PolicyPreflight (pass-through), Planner, FanOut, ConflictCheck,
│                           #   ProposalAssembly, HumanApproval, Commit, CommitVerify,
│                           #   Recovery, PolicyPostflight (pass-through), Respond, HumanHandoff
└── demo.py                 # NEW — CLI entry point, `quickstart.md`'s scenarios / make demo

mcp/gateway/
├── entertainment/
│   └── server.py           # NEW — the only missing gateway server (Track B work item, spec.md)
└── main.py                 # AMENDED — mount entertainment_app alongside the existing four

infra/
├── sql/                    # AMENDED (additive) — ORPHAN_RELEASED reachable via new method
└── jobs/
    └── hold_ledger_reaper.py   # NEW — standalone scheduled reaper (ADR-022)

rag/                        # UNCHANGED — HybridRetriever wired into entertainment/stay/
                             #   wellness's own venue-content queries the same way dining
                             #   already wires it (agents/dining/menu.py's pattern)
```

**Structure Decision**: Continue the existing file-disjoint track layout exactly.
`agents/` and `graph/` are Track C (this feature's primary owner); the one
`mcp/gateway/entertainment/server.py` addition is a Track B item nested inside this
feature (per the earlier scope decision to include it as a user story rather than an
external blocker); `infra/jobs/` is a small, new Track F surface for the reaper. No
directory outside this set is touched — `policy/` (Track G) and `eval/` (Track E) are
explicitly out of scope for this plan (Constitution Principle IV deferral;
research.md's Track E coordination note).

## Complexity Tracking

*No entries.* Every Constitution gate above passed without requiring a justified
deviation — the "required work" items under Principles II and V are scope this feature
fulfills, not violations it excuses. No 4th project, no repository-pattern-style
indirection, and no structure beyond the existing five-track layout is introduced.

## Architecture Decision Records *(mandatory — ResortFlow project override)*

Eight decisions in this plan met the significance bar (constrains a later feature; a
competent engineer could reasonably have chosen differently; reversing it later would be
breaking; or it trades one stated project value against another). Each is fully drafted
at `docs/adr/`:

| ADR | Decision | Status |
|---|---|---|
| [020](../../docs/adr/020-offer-agent-human-handoff-topology.md) | `HUMAN_HANDOFF` compiled as a real node this feature; `OFFER_AGENT` stays reserved, uncompiled | [x] drafted |
| [021](../../docs/adr/021-mcp-wire-transport-adoption.md) | All five domains (dining retrofitted) adopt real MCP wire-transport via one shared client | [x] drafted |
| [022](../../docs/adr/022-hold-ledger-reaper-execution-model.md) | Hold-ledger reaper ships as a standalone scheduled job; `HoldLedger` gains `mark_orphan_released` | [x] drafted |
| [023](../../docs/adr/023-resortflowstate-amendment-feature-005.md) | `ResortFlowState` gains `graph_version`, `clarification_count`, `escalate_reason` (additive) | [x] drafted |
| [024](../../docs/adr/024-clarification-turn-topology.md) | New `CLARIFY` node owns the guest clarification turn, looping through `IntentRouter` | [x] drafted |
| [025](../../docs/adr/025-static-travel-time-matrix.md) | Conflict checker's travel-time data is a static per-tenant matrix, not a live service | [x] drafted |
| [026](../../docs/adr/026-cancellation-reuses-decline-path.md) | Explicit guest cancellation reuses the `HumanApproval` decline path, not a separate signal | [x] drafted |
| [027](../../docs/adr/027-compensation-dead-letter-reuses-saga-log.md) | Failed-compensation dead-letter path reuses the existing `saga_log` Postgres mirror, not a new table | [x] drafted |

Every ADR above records its context, options, decision, and consequences, per this
project's standard for decision records. Decisions applying an already-recorded pattern to a new domain (the entertainment
MCP server's shape, per-domain idempotency key instantiation, sequential commit ordering
as a direct application of ADR-006) are documented in `research.md` without a new ADR,
since none of them meet the significance bar — each is routine application of a decision
already made in an earlier feature.
