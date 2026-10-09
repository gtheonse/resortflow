# ADR-001 — Supervisor + domain subgraphs over a single ReAct agent

**Status**: Accepted
**Date**: 2026-09-09
**Feature**: 001-contracts-foundation

## Context

ResortFlow serves six domains (dining, entertainment, stay, wellness, gaming, plus the
cross-cutting policy/conflict/recovery machinery) across roughly 45 tools. A guest request
like "dinner, a show, and a spa slot" touches three of those domains in a single turn, and
the two-jurisdiction, two-property structure means each domain carries its own compliance
rules that must not leak into another domain's prompt. Phase 0 has to freeze the graph node
registry (`graph/nodes.py`, FR-024) and the conversation state schema (`ResortFlowState`,
FR-001) before any agent is built, and both of those artifacts are shaped entirely by
whether the eventual runtime is one agent or several — so this decision cannot be deferred
to feature 005 without forcing a contract rewrite.

## Options Considered

**1. A single ReAct agent with all ~45 tools in one flat list.** Simplest possible
architecture — one prompt, one loop, one state. The natural first reach for a system this
size, and genuinely the right answer at a smaller scale.

**2. Supervisor pattern with per-domain subgraphs.** Each domain (dining, entertainment,
stay, wellness, read-only gaming) is a compiled LangGraph subgraph exposed to a supervisor
as a callable node. The supervisor owns routing, fan-out/fan-in, and the replan loop; each
subgraph only ever sees its own domain's tools and business rules.

**3. A fully decentralized swarm** (agents negotiating peer-to-peer with no central
coordinator). Maximally flexible in principle, but replaces a bounded fan-out/fan-in graph
with an unbounded negotiation protocol — durability, ordering, and termination all become
harder to reason about, and LangGraph's checkpoint/interrupt machinery has nothing natural
to attach to.

## Decision

**Supervisor pattern with domain subgraphs.** A single ReAct agent over the full tool set
is rejected on four independent grounds:

- **Tool-selection accuracy degrades with tool count.** Past roughly 15–20 tools in one
  context, selection accuracy falls off measurably; ResortFlow has ~45 tools across six domains.
  A flat list risks the model calling `spa.book` when it meant `dining.book`.
- **Context window economics.** Every tool schema, every domain's business rules, every
  policy fragment in one prompt is expensive and slow on *every* turn — even a turn that
  only needed the dining domain.
- **Independent evaluation is impossible with one agent.** You can only measure end-to-end
  success; you cannot answer "is the dining logic regressing?" Domain subgraphs each get
  their own eval set and their own pass/fail gate in CI.
- **Blast radius.** A prompt change to improve upsell phrasing must not be able to alter how
  allergen requirements are transmitted to a kitchen. Separate subgraphs give separate
  deployment and separate rollback.

A fifth reason is additive, not required: **parallelism**. "Dinner and a show and a spa
slot" is three independent availability lookups. A single agent serializes them; a
supervisor with fan-out runs them concurrently — the difference between roughly 9 seconds
and 3.

## Consequences

**Made easier.** Each domain subgraph is independently testable, deployable, and
rollback-able. `ResortFlowState`'s reducers (`merge_proposals`, `merge_holds`, `append_only`,
`dedupe_by_id` — this feature's User Story 1) exist specifically because concurrent domain
writes are now a first-class case, not an edge case. The node registry frozen in this
feature (`intent_router`, `supervisor`, `dining`, `entertainment`, `stay`, `wellness`,
`gaming`, …) gives feature 005 an unambiguous target.

**Made harder.** More moving parts than a single agent: a routing layer that must itself be
correct, cross-domain constraints (the conflict checker) that must run outside any single
subgraph's view, and a state schema that must merge rather than overwrite. Debugging a
multi-domain turn means reasoning about a fan-out/fan-in shape, not a linear trace.

**Foreclosed.** A future domain cannot silently reuse another domain's tool access or
prompt fragment — every domain subgraph's boundary is enforced by the topology itself, not
by convention. That is deliberate, but it means adding a domain is a structural change
(a new subgraph, a new node-registry entry via ADR), never a one-line prompt edit.
