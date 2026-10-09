# ADR-010 — Model tiering strategy and judge-model policy

**Status**: Accepted
**Date**: 2026-09-09
**Amends**: `.specify/memory/constitution.md` 1.0.0 → 1.1.0 (Principle VI, Declared Model Tier)

## Context

ResortFlow spends money on models along two independent axes, and they were at risk of being
treated as one problem.

**Build-time**: which Claude model executes a spec-kit task while the system is being built.
Paid from a subscription, chosen by a developer, per task.

**Runtime**: which Azure OpenAI model a graph node calls while serving a guest. Paid per
conversation, chosen by the graph, per node.

The prompting question was whether a model should be selected *dynamically by complexity* on
either axis, to reduce cost. That question needed answering before `/speckit-tasks` ran,
because task-level tier assignment is an annotation on the task list and retrofitting it
across nine features would be worse than deciding once.

## Options Considered

**1. Dynamic runtime routing by predicted complexity.** A classifier inspects each request
and routes to a cheap or frontier model. Genuinely attractive: it adapts to real input rather
than to a designer's guess, and it is the answer most people reach for first. It also
degrades gracefully in principle — a hard request gets more capability automatically.

**2. Static tiering per node, with no dynamic element.** Every node's model is fixed at design
time. Maximally predictable and trivially debuggable, but leaves nothing on the table when a
cheap node encounters an input it genuinely cannot handle.

**3. Static tiering per node, plus bounded upward escalation on validation failure.** Nodes
are fixed, but a node that fails its own output validation retries once at the higher tier and
then escalates to a human. Adds one failure path to reason about, in exchange for recovering
the genuinely-hard-input case that option 2 drops.

**4. No tiering at all — frontier everywhere.** Simplest, safest for correctness, and the
right answer for phases whose failure modes are silent. Roughly 5x the runtime cost, and
wastes frontier capability on boilerplate at build time.

## Decision

**Tiering is static on both axes. Build-time tier is declared per task in `tasks.md`; runtime
tier is declared per node at design time.** Option 1 is rejected. Option 3 is adopted for
runtime, with escalation strictly upward and strictly recorded. Option 4 is adopted
selectively — Phases 0, 5, 6 and 7 run entirely on the frontier tier at build time.

Dynamic runtime routing is rejected because **the graph decomposition already encodes the
complexity**. That is what decomposing into a supervisor and domain subgraphs bought. The
Planner performs multi-step reasoning with constraints; the Dining subgraph selects among six
tools with the hard part already resolved upstream. A classifier re-deriving that at runtime
re-solves a problem solved at design time, and pays for the privilege: a classifier call on
every turn costs latency and money, a misroute sends a hard request to the cheap model and
produces a bad plan *silently*, and model selection varying per request makes eval runs
non-comparable — which means the CI gate stops meaning anything.

Bounded escalation survives because it is **measured, not predicted**. It triggers on an
observed validation failure rather than on a guess about difficulty, and the escalation rate
becomes a first-class metric: a node escalating above roughly 5% has the wrong base tier and
gets promoted permanently. That is an empirical tiering policy.

The judge is excluded from economising entirely. A judge weaker than the generator produces
metrics that are decorative.

Consequent artifacts: `docs/models.md` as the build-time policy with an
eleven-entry `[CRIT]` never-economy table; Principle VI added to the constitution;
`.specify/templates/overrides/tasks-template.md` adds a Tier column so the assignment is
recorded per task; and a standing `/speckit-analyze` prompt turns it into something checked.
Per-node runtime assignments are recorded in `docs/model-tiering-runtime.md`.

## Consequences

**Made easier.** Cost becomes a declared, checkable property rather than a discovery on the
Azure bill. Roughly 80% of node executions run cheap for about a 5x reduction against
frontier-everywhere. Tier assignment doubles as a spec review: any task that cannot be
executed by the cheap model reveals an under-specified spec *before* implementation. Incident
debugging stays tractable because "which model ran?" has a static answer.

**Made harder.** Two tier vocabularies now exist in one repository and must be kept apart —
mitigated by separate documents with reciprocal header notes, but it is a real ongoing
hazard. Escalation adds a runtime path that must itself be tested. And per-node static
assignment means a genuinely hard request to a cheap node depends entirely on validation
catching it; weak validators make escalation useless.

**Foreclosed.** Per-request cost optimisation beyond node granularity. If a future workload
turns out to be strongly bimodal *within* a single node, this decision has to be revisited
rather than tuned.

**The real risk.** Not that cheap models produce code that fails — that surfaces immediately.
It is that they produce **code that works and is wrong**: an economy model reads less context
and honours fewer constraints, so an under-specified economy task yields plausible code that
quietly violates a constitutional principle. The `[CRIT]` table and the analyze gate exist
for exactly this, and both are worthless if analyze is skipped under delivery pressure — which
is precisely when tier drift happens.
