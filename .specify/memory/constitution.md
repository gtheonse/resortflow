<!--
SYNC IMPACT REPORT
==================
Version change: (unratified template) → 1.0.0
Bump rationale: Initial ratification. The prior file was the unfilled Spec Kit
scaffold with no adopted content, so this is the first governing version.

Principles defined (all five template slots filled):
  [PRINCIPLE_1_NAME] → I. Ambiguity-Based Routing
  [PRINCIPLE_2_NAME] → II. Governed Integration Surface
  [PRINCIPLE_3_NAME] → III. Two-Phase Mutation
  [PRINCIPLE_4_NAME] → IV. Policy Is Code, Not Prompt
  [PRINCIPLE_5_NAME] → V. Durable, Inspectable State

Sections defined:
  [SECTION_2_NAME]   → Development Standards
  [SECTION_3_NAME]   → Development Workflow & Quality Gates
  [GOVERNANCE_RULES] → Governance

Removed sections: none.
Deferred placeholders: none. No TODO tokens remain.

Source context: docs/ResortFlow-Enterprise-Multi-Agent-Plan.md §2 (why multi-agent,
and where it is the wrong answer), §3 (target architecture and the five
architectural commitments), §6 (holds, sagas and compensation);
docs/ResortFlow-HITL-Durability-State.md §3 (write-ahead hold ledger).

------------------------------------------------------------------
AMENDMENT — 2026-09-09
Version change: 1.0.0 → 1.1.0
Bump rationale: MINOR. A new principle was added; no existing principle was
removed or redefined, so no backward-incompatible governance change occurred.

Added: VI. Declared Model Tier
Modified principles: none.
Removed sections: none.
Deferred placeholders: none.

Amendment ADR: docs/adr/010-model-tiering-strategy.md (required by the
Governance section's amendment procedure).

Source context: docs/ResortFlow-Model-Tiering.md §4c (the principle text), §1 (the
[CRIT] never-economy list), §3 (static-per-node runtime tiering and the
bounded-escalation exception).
-->

# ResortFlow Constitution

ResortFlow is an enterprise multi-agent hospitality
platform serving two tenants of Meridian Resorts International: `MGV` (Meridian Grand,
Las Vegas — Nevada/NGCB) and `MHB` (Meridian Harbor, Boston — Massachusetts/MGC). The two
jurisdictions are load-bearing: they force per-tenant *policy*, not per-tenant branding.

This constitution governs every feature. It is checked at `/speckit-plan` time and again at
`/speckit-analyze` time. A feature that violates a principle does not ship; it produces an
ADR that amends the principle, or it is redesigned.

## Core Principles

### I. Ambiguity-Based Routing

An LLM MUST be invoked only where intent is unbounded or a request spans two or more
systems. Deterministic API paths remain first-class and are the degradation fallback when
the agentic layer is unavailable or over budget.

- A request that is single-domain, low-ambiguity and has a correct lookup answer MUST NOT
  enter the agentic graph. It is a cache hit, a conventional API path, or a single
  retrieval call.
- Any feature that introduces an LLM into a well-defined flow MUST justify that choice in
  its plan's Constitution Check, naming the ambiguity the model resolves.
- Any flow with a latency budget under 1.5 seconds MUST stay off the multi-hop path.

**Rationale**: tool-selection accuracy degrades measurably past roughly 15–20 tools in one
context, and ResortFlow carries ~45 tools across six domains. Roughly 60% of traffic never
touching an LLM is a design goal, not a shortfall. Routing is by ambiguity, not by
capability.

### II. Governed Integration Surface

No agent holds a vendor SDK. All external system access MUST pass through MCP tools with
typed schemas and per-tool scopes.

- `tenant_id` MUST be injected server-side from the validated token claim. It MUST NEVER be
  derived from model output, and a tenant-crossing call MUST be impossible by construction,
  proven by test rather than asserted.
- Vendor error shapes MUST be normalized to the project error taxonomy at the MCP boundary;
  no vendor-specific error shape may reach an agent.
- The gaming domain is read-only. Enforcement is the **absence** of mutating gaming tools
  from the registry — not a prompt instruction, and not a discouraging description.

**Rationale**: five deliberately inconsistent vendors (differing auth, pagination, date
formats and error envelopes) is the condition the MCP layer exists to normalize. One
governed surface yields uniform audit, uniform retry, and one place to enforce tenancy.

### III. Two-Phase Mutation

No agent performs an irreversible external action in a single call. Every external mutation
MUST follow hold → propose → approve → commit → verify → compensate.

- Every mutating tool MUST take a client-generated idempotency key derived from
  `(session_id, hold_id, attempt)`. Simulators and services MUST honour it; this is contract
  enforcement, not test scaffolding.
- The human approval interrupt MUST sit exactly at the reversible/irreversible boundary —
  after holds are placed, before any commit. Earlier is annoying; later is dangerous.
- Commit MUST be sequential and ordered by scarcity and irreversibility, not parallel.
- Compensation MUST be semantic, preserving as much guest intent as possible. A mechanical
  rollback that cancels everything is a defect, not a safe default.
- A compensation that itself fails MUST write the full saga log to a dead-letter path and
  escalate to a human. Silent partial failure is the only unacceptable outcome.

**Rationale**: there is no two-phase commit across the vendors and there never will be.
Hold-then-confirm converts an irreversible distributed transaction into a reversible one,
and holds expire on their own, so "the agent crashed mid-flow" self-heals instead of
becoming a manual remediation ticket.

### IV. Policy Is Code, Not Prompt

Compliance rules — allergen handling, responsible gaming, jurisdiction-specific messaging,
entitlements — MUST be enforced in deterministic code before and after the model. A rule
that can be undone by a prompt edit is not enforced.

- Dietary restrictions MUST be carried as a structured enum plus severity. Free text MAY
  accompany the structured field and MUST NEVER replace it. A post-flight check MUST assert
  that every restriction stated in conversation appears in the structured payload before
  confirm is permitted; a mismatch is a hard block plus escalation.
- The self-exclusion check MUST run before any gaming-domain tool executes and before the
  Offer Agent runs at all, on every turn, regardless of topic.
- ResortFlow MUST NOT initiate gaming promotion. It answers gaming questions; it does not raise
  the subject.
- Jurisdiction-mandated messaging MUST be appended deterministically, never generated.
- Any utterance suggesting gambling-related distress MUST route to human handoff with
  responsible-gaming resources surfaced. No model judgment participates in that route.

**Rationale**: Nevada and Massachusetts differ on advertising restrictions, self-exclusion
enforcement and mandated messaging. Expressing that as per-tenant policy configuration
rather than per-tenant prompts is what makes the platform deployable in a licensed
business.

### V. Durable, Inspectable State

Every graph step MUST be checkpointed. Every conversation MUST be replayable, resumable and
auditable.

- The checkpointer is the record of **conversation state**. A separate write-ahead ledger is
  the record of **external side effects**. These MUST NOT be conflated: under `sync`
  durability the ordering of pending writes against the superseding checkpoint is not
  guaranteed, and resume *replays* steps rather than continuing from them.
- Conversation state MUST serialize under `JsonPlusSerializer` without the pickle fallback.
  Reaching for pickle means the state design is wrong.
- Checkpoints MUST be encrypted at the serializer layer, independently of database
  encryption at rest. Dietary requirements are health data.
- Holds recorded in state MUST be revalidated against the vendor on resume. State is a
  record of what was done, never a guarantee of what is still live.
- Every cyclic path in the graph MUST carry an explicit, bounded termination guarantee with
  escalation to a human at the bound.

**Rationale**: checkpoints are the audit trail for money-moving conversations. They need
durability and queryability, and they need a companion ledger for the effects they cannot
honestly claim to own.

### VI. Declared Model Tier

Every implementation task MUST declare a build-time model tier, and every LLM-invoking node
MUST declare a runtime model tier. Tiers are explicit; a defaulted tier is a defect.

- Tasks touching concurrency, transaction integrity, tenant isolation, policy enforcement or
  evaluation logic are `[CRIT]` and MUST NOT run on the economy tier, regardless of how
  mechanical the task appears. The enumerated list lives in `docs/models.md`.
- A task MAY be assigned the economy tier only where the specification fully determines its
  output. If executing it requires a judgment call that is not written down, it is not an
  economy task — and that gap is a specification defect to fix, not a tier to accept.
- Runtime tier is **static per node**. The graph decomposition already encodes complexity;
  re-deriving it per request costs latency, adds a silent misroute failure mode, and makes
  evaluation runs non-comparable.
- The only permitted dynamic behaviour is **bounded upward escalation** on validation
  failure: attempt at the assigned tier, validate, retry once at the higher tier, then
  escalate to a human. Escalation MUST be recorded. A node MUST NEVER be demoted at runtime,
  and the judge, planner and policy paths MUST NOT participate in escalation at all — they
  begin at the frontier tier and stay there.

**Rationale**: cost control that is not declared is cost control that erodes under delivery
pressure, and the erosion is invisible. Making the tier an explicit, checkable property lets
`/speckit-analyze` enforce it instead of relying on recall. The escalation rate is
additionally a real signal: a node escalating more than a few percent of the time has the
wrong base tier, which is an empirical answer rather than a guess.

## Development Standards

- **DETERMINISM FIRST**: where a computation has a correct answer, it MUST be a function,
  not a model call. Every LLM node MUST be justified. The conflict checker is deterministic
  on purpose — an LLM gets travel-time arithmetic right ~95% of the time; a function gets it
  right 100% of the time, in 2ms, and is unit-testable.
- **CONTRACT STABILITY**: interfaces frozen in an earlier feature MUST NOT be changed by a
  later one without an ADR. Graph node names are a persistence contract; renaming one is a
  breaking change, never a patch. A track needing a contract change raises it as a blocker
  rather than editing unilaterally.
- **EVERY DECISION IS DOCUMENTED**: significant technical decisions MUST produce an ADR in
  `docs/adr/` containing Context, Options Considered, Decision, and Consequences
  (including the accepted trade-off and what would change the decision).
- **TEST BEFORE ADVANCE**: a feature is not complete until its acceptance criteria are
  covered by automated tests. Saga failure permutations and tenant isolation are mandatory
  test categories.
- **OBSERVABILITY IS NOT OPTIONAL**: every external call and every graph node MUST emit an
  OpenTelemetry span carrying both `gen_ai.*` and `langfuse.*` attribute namespaces via the
  telemetry module. Attribute names MUST NOT be defined outside that module. PII MUST be
  redacted on trace and log write paths.
- **NO REAL CLIENT NAMES** anywhere in this repository. The client is Meridian Resorts
  International; the tenants are `MGV` and `MHB`.

## Development Workflow & Quality Gates

- **Spec-driven loop.** Each phase is one Spec Kit feature and follows
  `/speckit-specify` → `/speckit-clarify` → `/speckit-plan` → `/speckit-tasks` →
  `/speckit-analyze` → `/speckit-implement` → `/speckit-converge`. `clarify` and `analyze`
  are mandatory, not optional: `analyze` is the contract-drift detector, and drift is the
  primary delivery risk under parallel work.
- **Convergence is the exit gate.** `implement` reports success on partial work. A feature is
  done when `/speckit-converge` reports **Converged** *and* the phase exit criteria in
  `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §13 are independently checked.
- **Specs describe what and why; plans describe how.** A spec that starts re-deriving the
  orchestration framework is under-constrained. Architectural decisions already made are fed
  into `/speckit-plan` with explicit document references.
- **File-disjoint tracks.** After contracts are frozen, work parallelizes into tracks that
  own their directories: A `sims/` + `docs/contracts/openapi/`, B `mcp/`, C `agents/` +
  `graph/`, D `content/` + `rag/`, E `eval/`, F `infra/` + `docker/`, G `policy/`. Tasks may
  be marked `[P]` only when they touch disjoint directories. Cross-cutting changes go through
  a single integration commit.
- **Phase summary.** Each phase ends with a summary, kept with the feature's spec directory,
  recording what was built, deviations from spec, interfaces published, and open questions
  resolved.
- **CI gate.** The evaluation suite is the merge gate. An eval regression blocks merge, and a
  deliberately degraded prompt MUST fail CI.

## Governance

This constitution supersedes all other development practices. Where a document, prompt,
template or habit conflicts with it, this file wins.

- **Amendment procedure.** An amendment requires an ADR in `docs/adr/` recording the context
  and consequences, an edit to this file, and a version bump in the same change. Principles
  are not amended inline during feature work; a feature that needs an amendment stops and
  raises it.
- **Versioning policy.** Semantic versioning applies to this document. MAJOR for backward
  incompatible governance or principle removals and redefinitions; MINOR for a new principle
  or materially expanded guidance; PATCH for clarifications and wording that do not change
  meaning.
- **Compliance review.** `/speckit-plan` MUST evaluate its feature against every principle in
  its Constitution Check section and record the result. `/speckit-analyze` MUST re-check
  before implementation begins. Any accepted deviation MUST be recorded as an ADR, with the
  justification stated — complexity is permitted, unexplained complexity is not.
- **Runtime guidance.** `AGENTS.md` at the repository root (imported by `CLAUDE.md`) carries
  the standing operational context (client anonymization, hard rules, tech stack, module
  ownership, commands). It elaborates this constitution and MUST NOT contradict it; if it
  does, this file governs and `AGENTS.md` is corrected.

**Version**: 1.1.1 | **Ratified**: 2026-09-09 | **Last Amended**: 2026-10-08
