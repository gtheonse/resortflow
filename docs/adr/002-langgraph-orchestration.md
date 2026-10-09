# ADR-002 — LangGraph over AutoGen / CrewAI / Semantic Kernel

**Status**: Accepted
**Date**: 2026-09-09
**Feature**: 001-contracts-foundation

## Context

ADR-001 commits to a supervisor with domain subgraphs, fan-out/fan-in, a bounded replan
cycle, and a human-approval interrupt sitting exactly at the reversible/irreversible
boundary before commit. Whatever orchestration framework is chosen has to express all four
of those natively, because this feature (001-contracts-foundation) is freezing the
conversation state schema, the node-name registry, and the checkpoint/serialization
contract that everything downstream binds to — the framework choice is load-bearing for
every one of those artifacts, not just an implementation detail deferred to feature 005.

## Options Considered

**1. AutoGen.** Strong at open-ended multi-agent conversation and negotiation patterns.
Weak on durable, resumable state — conversations are the primary abstraction, not a typed,
checkpointed graph, which makes "replay this exact turn after a crash" awkward to express
natively.

**2. CrewAI.** Good ergonomics for role-based agent crews with a lightweight task
abstraction. Similarly conversation/task-centric rather than state-machine-centric; cycles,
interrupts, and per-channel merge semantics (what this feature's `merge_holds` /
`merge_proposals` reducers depend on) are not first-class concepts.

**3. Semantic Kernel.** The natural choice for an all-Azure, .NET-leaning shop, with strong
enterprise integration. Its process/planning abstractions are capable, but checkpoint/resume
fidelity for money-moving flows — the specific property this feature's Technical Context
names as central (`durability="sync"` semantics, resume-replays-steps-not-continues) — is
less mature and less explicit than LangGraph's.

**4. LangGraph.** An explicit state machine (`StateGraph`) with typed state, `Annotated`
per-channel reducers, a Postgres-backed checkpointer, native `interrupt()` for
human-in-the-loop, and explicit cycle support with the caller responsible for termination
guarantees.

## Decision

**LangGraph.** The deciding factor is checkpoint/resume fidelity for money-moving flows,
not ecosystem or vendor alignment. Three concrete properties this feature depends on
directly:

- **Typed state with per-channel reducers.** `ResortFlowState` is a `TypedDict` specifically
  because LangGraph binds a reducer to each channel via `Annotated` metadata (see
  research.md item 4) — this is what lets `proposals`, `holds`, `confirmations` and
  `retrieved` each merge under their own rule instead of one global last-write-wins.
- **Durable, inspectable checkpointing.** Postgres-backed (ADR-004), with an explicit
  contract for what does and does not survive a crash — this feature's central insight
  (the checkpointer owns conversation state, not external side effects) only has
  somewhere to attach because LangGraph makes checkpoint semantics an explicit, documented
  contract rather than an implementation detail.
- **`interrupt()`-based human-in-the-loop with explicit cycle control.** The approval gate
  sits precisely at the reversible/irreversible boundary (ADR-006); the replan loop has an
  explicit bound (`MAX_REPLAN_COUNT = 3`, this feature's `graph/state.py`). Both require a
  framework where cycles and pauses are first-class, not a workaround.

## Consequences

**Made easier.** State durability, resumability, and auditability (constitution Principle
V) are framework-native rather than something ResortFlow has to build on top. The reducer
mechanism this feature freezes (`merge_proposals`, `merge_holds`, `append_only`,
`dedupe_by_id`) is a direct, idiomatic use of LangGraph's channel model rather than a
workaround.

**Made harder.** LangGraph's replay-on-resume semantics (a node can re-execute after a
crash) is a sharp edge that has to be designed around explicitly — it is the entire reason
this feature's write-ahead hold ledger (ADR-019) exists as a structure separate from the
checkpointer. A framework that hid this complexity would have felt easier short-term and
been wrong.

**Foreclosed.** Deep Semantic Kernel / Azure-native tooling integration is not free —
LangGraph is not a Microsoft-first framework, so some enterprise Azure conveniences (native
SK planners, first-party Azure agent tooling) are not available without adapter work.
AutoGen- and CrewAI-style open-ended agent negotiation patterns are also not a natural fit
on top of a StateGraph; if a future requirement genuinely needed unstructured multi-agent
negotiation rather than a bounded fan-out/fan-in graph, this decision would need revisiting.
