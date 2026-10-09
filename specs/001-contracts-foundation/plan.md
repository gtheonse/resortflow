# Implementation Plan: Contracts Foundation

**Branch**: `001-contracts-foundation` | **Date**: 2026-09-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-contracts-foundation/spec.md`

## Summary

Freeze the interfaces that all seven downstream tracks bind to: a canonical conversation
state schema whose collections merge rather than overwrite under parallel domain writes,
the eleven booking-lifecycle domain models, a four-way error taxonomy, the MCP tool
contract standard, a write-ahead mutation ledger owned by ResortFlow and deliberately separate
from the framework's checkpoint tables, the telemetry attribute vocabulary, and a local
service topology.

The technical core is a single insight: **the checkpointer is the system of record for
conversation state, not for external side effects.** Under `durability="sync"` the ordering
of `put_writes` against the superseding `put` is not enforced, and resume *replays* steps
rather than continuing from them — so a node can re-execute. Everything in this plan
follows from taking that seriously: merge-by-key reducers that are idempotent under replay,
hold identity that exists before the vendor responds, terminal states that cannot regress,
and a ledger keyed by idempotency key that survives a crash in any of the three windows
around an external call.

This feature ships zero LLM nodes and zero runtime surface. It is a contracts library.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: `langgraph`, `langgraph-checkpoint-postgres`, `pydantic>=2`

**Storage**: PostgreSQL — hosting both the framework-managed checkpoint tables
(`checkpoints`, `writes`) and the ResortFlow-owned `hold_ledger`, in the same database but as
deliberately separate schemas of record. Qdrant is a downstream dependency (feature 003)
and is declared in the topology only.

**Testing**: `pytest`, `mypy --strict`, `ruff`, `openapi-spec-validator`

**Target Platform**: Linux containers (Azure Container Apps); local development on Windows
and macOS via Docker Compose

**Project Type**: Single Python package — a contracts library with no runtime surface. No
web tier, no CLI, no services in this feature.

**Performance Goals**: Not applicable to a contracts library. One structural constraint
does apply: every reducer must merge in a single pass over its inputs, since reducers run
on the checkpoint write path of every superstep.

**Constraints**:
- Nothing in state may require the pickle fallback under `JsonPlusSerializer`.
- Graph node names and state keys are a persistence contract; renaming either is a breaking
  change, never a patch.
- `tenant_id` is set once at conversation entry from a validated token claim and is never
  derived from model output.
- Every reducer must be idempotent under replay and must accept an absent left value.

**Scale/Scope**: 11 domain entities, 4 reducers, a 4-way error taxonomy over the union of 9
documented system failure modes and 5 vendor failure-code enumerations, ~45 downstream
tools across 6 domains, 7 dependent workstreams.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` v1.1.0.

| Principle | Verdict | Evidence |
|---|---|---|
| **I. Ambiguity-Based Routing** | ✅ PASS | Zero LLM nodes introduced. Every artifact is a schema, a pure function, or a document. Nothing in this feature can invoke a model. |
| **II. Governed Integration Surface** | ✅ PASS | The tool contract standard (FR-015, FR-016) mandates server-side `tenant_id` injection and forbids caller-supplied tenancy. No vendor SDK appears anywhere. |
| **III. Two-Phase Mutation** | ✅ PASS — *enabling work* | `Hold`, `Confirmation` and the ledger encode hold-then-confirm structurally. Idempotency key is mandatory on `Hold`, not optional (FR-017). |
| **IV. Policy Is Code, Not Prompt** | ✅ PASS | `DietaryRequirement` carries a structured enum plus severity with free text explicitly non-substitutable (FR-010). `PolicyFlag` is a first-class entity. |
| **V. Durable, Inspectable State** | ✅ PASS — *enabling work* | The checkpointer/ledger separation (FR-018) is this feature's central deliverable. No-pickle and bounded-replan are enforced as testable constraints. |
| **VI. Declared Model Tier** | ✅ PASS | **Build-time**: every task declares `FRONTIER`, per the whole-phase rule in `docs/models.md` — Phase 0 is deliberately not tiered, because reducers and hold-ledger recovery are the two things in this project that fail silently and both live here. Four `[CRIT]` areas are in scope: state reducers, error taxonomy and classification, hold-ledger recovery paths, idempotency key derivation. **Runtime**: not applicable — this feature introduces no LLM-invoking node, and per-node runtime tiers are a Phase 4 input (`docs/ResortFlow-Model-Tiering.md` §5.4). |

| Development Standard | Verdict | Evidence |
|---|---|---|
| Determinism first | ✅ PASS | All four reducers and the error classifier are pure functions. No model call is possible in this feature. |
| Contract stability | ✅ PASS | Two contracts are frozen as strings that later features must bind to: `test_state_contract.py` asserts the ResortFlowState key set literally; a similar test will assert that every node name used in production traces back to `graph/nodes.py`. |
| Every decision documented | ✅ PASS | Seven ADRs obligated — see the ADR table below. The node-name freeze decision is documented in `research.md` item 12 and is folded into ADR-001's consequences rather than creating a separate ADR. |
| Test before advance | ✅ PASS | Five test modules covering merge, replay, serialization, taxonomy totality, and spec linting. |
| Observability not optional | ✅ PASS | FR-022 freezes the attribute vocabulary in a single owning module before any track can emit a span. |
| No real client names | ✅ PASS | Meridian Resorts International, `MGV`, `MHB` only. |

**Gate result: PASS.** No violations requiring justification, so Complexity Tracking is
empty by design. Two deliberate deviations from source documents are recorded below — both
are additions that a downstream feature would otherwise have to make unilaterally, which is
precisely the drift this feature exists to prevent.

### Recorded deviations

1. **`saga_id` added to `ResortFlowState`.** Section 5 of `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md`
   does not list it. It is required to join conversation state to ledger rows, and section 2
   of `docs/ResortFlow-Observability-Tracing-Deep-Dive.md` establishes that `saga_id` deliberately
   outlives `trace_id` — a guest holds in turn 3, approves in turn 5, compensation runs in
   turn 6, which is three traces and one transaction. It therefore cannot be derived from
   trace context and must be carried in state. Recorded in ADR-019.
2. **`messages` history windowing deferred to feature 005.** Section 5 of
   `docs/ResortFlow-HITL-Durability-State.md` recommends a windowing reducer, and `DeltaChannel`
   is the eventual optimisation. `DeltaChannel` requires `langgraph>=1.2`, is beta, and its
   on-disk representation may change. Standard channels are used now; the decision to
   revisit is recorded rather than silently deferred.

## Project Structure

### Documentation (this feature)

```text
specs/001-contracts-foundation/
├── plan.md              # This file
├── research.md          # Phase 0 output — 11 decisions with alternatives
├── data-model.md        # Phase 1 output — 11 entities + Hold state machine
├── quickstart.md        # Phase 1 output — runnable validation guide
├── contracts/           # Phase 1 output — design-time interface specifications
│   ├── state-schema.md
│   ├── mcp-tool-contract.md
│   ├── tool-error.schema.json
│   ├── hold-ledger.sql
│   └── telemetry-attributes.md
├── checklists/
│   └── requirements.md  # 16/16 passing
└── tasks.md             # Created by /speckit-tasks, not by this command
```

### Source Code (repository root)

```text
graph/                          # Track C — the frozen state and orchestration contracts
├── __init__.py
├── state.py                    # ResortFlowState + merge_proposals, merge_holds,
│                               #   append_only, dedupe_by_id
├── models.py                   # 11 domain entities, enums mirroring the vendor specs
├── errors.py                   # ErrorClass, exception hierarchy, pure classifier
└── nodes.py                    # Graph node-name registry (constants only, no impl)

observability/                  # NEW package — the single owning module for
├── __init__.py                 #   attribute names. Constants only in this feature;
└── attributes.py               #   no tracer, no exporter, no emission.

docs/contracts/                 # Track A owns openapi/; this feature adds the rest
├── openapi/*.yaml              # Frozen inputs — five vendor specifications
├── mcp-tool-contract.md        # Naming, idempotency, error shape, scopes
└── schemas/tool-error.schema.json

infra/sql/                      # Track F
└── 001_hold_ledger.sql         # ResortFlow-owned, separate from checkpoint tables

docs/adr/                       # 001-006 + 019
tests/                          # Seven modules — see Verification
docker-compose.yml              # Service topology only; sims and app behind profiles
Makefile                        # up / seed / eval / demo
pyproject.toml
```

**Structure Decision**: Single Python package at the repository root, matching the existing
track-ownership layout in `AGENTS.md` rather than introducing a `src/` tree. The directories
already exist as `.gitkeep` placeholders and the track table already assigns owners to them;
adding a `src/` prefix would invalidate that table for no benefit.

`observability/` is the one new directory. It is deliberately not placed inside `graph/`:
Tracks B, D and G all need to import attribute names, and burying a cross-cutting contract
inside the agent track would force three tracks to import from a directory they do not own.
The track-ownership table in `AGENTS.md` gains a row for it during implementation.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations. The Constitution Check gate passed on both the initial and post-design
evaluations, so this section is intentionally empty.

## Architecture Decision Records *(mandatory — ResortFlow project override)*

Applying the significance bar from the template: a decision qualifies if it constrains a
later feature, could reasonably have gone the other way, would be breaking to reverse, or
trades one project value against another.

### Plan completion gate

| ADR | Decision | Status |
|-----|----------|--------|
| 001 | Supervisor + domain subgraphs over a single ReAct agent | [ ] drafted |
| 002 | LangGraph over AutoGen / CrewAI / Semantic Kernel | [ ] drafted |
| 003 | Qdrant for build, Azure AI Search as production target | [ ] drafted |
| 004 | Postgres checkpointer over Redis / in-memory, in both environments | [ ] drafted |
| 005 | MCP as the sole integration surface | [ ] drafted |
| 006 | Hold-then-confirm saga over direct booking | [ ] drafted |
| 019 | Write-ahead hold ledger, outside the checkpointer | [ ] drafted |

ADR-019 is not in the register in section 15 of `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md`.
It is added deliberately: the ledger is the most defensible decision this feature makes and
the register has no entry for it. It also carries the `saga_id` state deviation.

Decisions made in this plan that did **not** meet the significance bar, and why:

- Choosing `pytest`, `ruff` and `mypy` — conventional tooling, reversible in an afternoon,
  constrains no interface.
- Enum value spellings — these are transcriptions of the frozen vendor specifications, not
  decisions. The decision to treat those specs as authoritative is part of ADR-005.
- Placing `observability/` at the repository root — a structural consequence of the
  already-recorded telemetry decision, documented in this plan's Structure Decision rather
  than as a separate ADR.
