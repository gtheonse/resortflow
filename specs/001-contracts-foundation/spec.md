# Feature Specification: Contracts Foundation

**Feature Branch**: `001-contracts-foundation`

**Created**: 2026-09-09

**Status**: Draft

**Input**: User description: "Feature: contracts-foundation. Establish the frozen interface contracts that every later feature binds to. Nothing downstream may start until these are stable. Delivers a canonical conversation state schema supporting concurrent writes from parallel domain agents without data loss; domain models for the booking lifecycle; a normalized error taxonomy; a tool contract standard; a durable ledger schema recording external mutation intent and outcome independent of workflow checkpoints; and a local development environment definition. Five parallel workstreams follow this feature, so these interfaces are frozen and any change requires an ADR. OUT OF SCOPE: vendor simulators, MCP servers, agents, retrieval. Contracts only."

## Clarifications

### Session 2026-09-09

- Q: Should the error taxonomy's coverage requirement be bound to the failure matrix in
  `docs/ResortFlow-HITL-Durability-State.md` section 11 plus the failure-code enumerations in the
  five specifications under `docs/contracts/openapi/`, replacing the unresolvable reference
  in the brief? → A: Yes — both sources. The failure matrix covers system and durability
  failures; the specification enumerations cover external-system failures. Together they
  are the complete, finite set the taxonomy must classify.
- Q: Which hold dispositions must be treated as terminal, so a late-arriving write can
  never regress them back to pending or held? → A: Confirmed, released, expired and
  orphan-released are all terminal. Pending and held remain mutable. Re-acquiring inventory
  after expiry produces a new hold with a new idempotency key, never a revival of the old
  one.
- Q: Should this feature freeze the telemetry attribute naming contract, or defer it to the
  observability feature? → A: Freeze the names now, implement the emission later. Tool
  spans are emitted four features before the observability feature is built, so unfrozen
  names mean three workstreams inventing three vocabularies.
- Q: Should the domain models mark which fields carry personal or health data at the
  contract level? → A: Yes, at field level. Redaction and encryption then bind to the
  contract rather than to a hand-maintained list that drifts as models grow.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Parallel domain results survive concurrent writes (Priority: P1)

A guest asks for dinner, show tickets and a spa slot in one request. Three domain
workstreams run at the same time and each produces a proposal and places a reversible
hold. When they finish, the conversation holds all three results — nothing any of them
produced has been silently replaced by another.

**Why this priority**: this is the minimum viable contract. If concurrent results
overwrite one another, every feature built afterwards loses data silently and the loss is
invisible until a guest is missing a booking they were told they had. No later work is
safe until this behaviour is fixed and provable.

**Independent Test**: drive three simulated concurrent writers into the state schema
without any agent, tool or vendor present, and assert every contributed result is
retrievable afterwards. Delivers value on its own — it is the guarantee the four
downstream tracks code against.

**Acceptance Scenarios**:

1. **Given** an empty conversation, **When** three writers each contribute one proposal in
   the same step, **Then** all three proposals are present and none has been discarded.
2. **Given** an empty conversation, **When** three writers each place one hold in the same
   step, **Then** all three holds are present and each is identifiable before any vendor
   identifier exists for it.
3. **Given** a conversation containing a hold already recorded as confirmed, **When** a
   later-arriving write reports that hold as merely pending, **Then** the confirmed
   disposition is retained and not regressed.
4. **Given** a conversation with recorded results, **When** an identical write is applied a
   second time during a resume, **Then** the conversation is unchanged and nothing is
   duplicated.
5. **Given** a conversation with no prior value for a collection, **When** the first write
   arrives, **Then** it is accepted without error.

---

### User Story 2 - External mutation intent survives a crash at any point (Priority: P2)

A workstream is about to reserve inventory in an external system. It records what it is
about to do before making the call and records what happened afterwards. If the process
dies at any moment in between, a later sweep can determine what was attempted and release
anything left holding inventory, so a crash never strands a guest's inventory in a vendor.

**Why this priority**: reversibility is what makes the whole booking approach safe, and
reversibility depends on knowing what was attempted. It ranks below P1 only because the
state schema is what every other track imports first.

**Independent Test**: simulate an interruption at each of the three windows — after intent
is recorded but before the external call, after the call but before the outcome is
recorded, and after the outcome is recorded — and assert in each case that the attempt is
discoverable and releasable, with no external system involved.

**Acceptance Scenarios**:

1. **Given** an intent recorded with no outcome, **When** recovery runs, **Then** the
   attempt is discoverable by its idempotency key and can be released.
2. **Given** an external call that completed but whose outcome was never recorded, **When**
   recovery runs, **Then** the same idempotency key joins the record to the external
   result.
3. **Given** a recorded outcome holding live inventory whose conversation is no longer
   active, **When** the orphan sweep runs, **Then** the entry is released and marked as
   orphan-released.
4. **Given** two attempts submitted with the same idempotency key, **When** both are
   recorded, **Then** only one entry exists.

---

### User Story 3 - Every failure classifies into exactly one disposition (Priority: P3)

Five external systems report failures in five different shapes. Anything consuming them
sees one normalized vocabulary with four dispositions — retry it, stop, ask a human, or
refuse on policy grounds — so that handling logic is written once rather than per vendor.

**Why this priority**: it is required before any integration work begins, but it is
verifiable in isolation and blocks fewer immediate consumers than the state schema.

**Independent Test**: feed every enumerated external failure code and every documented
system failure mode through classification and assert each resolves to exactly one
disposition, with no input left unclassified.

**Acceptance Scenarios**:

1. **Given** any enumerated external failure code, **When** it is classified, **Then**
   exactly one of the four dispositions is returned.
2. **Given** a failure code that is not enumerated, **When** it is classified, **Then** a
   deterministic disposition is still returned and the result does not vary between runs.
3. **Given** a failure caused by a compliance rule, **When** it is classified, **Then** it
   is reported as a policy refusal and never as a retryable condition.
4. **Given** a transient upstream failure, **When** it is classified, **Then** it is
   reported as retryable and carries any wait interval the external system supplied.

---

### Edge Cases

- A reversible hold expires while the request is waiting for a human decision.
- A resume replays a step that already ran, and the replayed write must not double-count.
- An external system responds successfully for an attempt whose intent was never recorded.
- Two workstreams independently propose the same item for the same time.
- A dietary restriction is stated in conversation but is absent from the structured
  payload at the moment of commitment.
- The planning cycle fails to reach a coherent plan and would otherwise loop indefinitely.
- A downstream workstream needs a frozen interface changed while other workstreams are
  actively building against it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST define one canonical conversation state shared by every
  domain workstream, covering identity and tenancy, conversation history, planning,
  domain results, grounding, and control.
- **FR-002**: Concurrent contributions to proposals MUST merge by proposal identity. No
  contributor's result may be discarded by another contributor in the same step.
- **FR-003**: Concurrent contributions to holds MUST merge on an identifier that exists
  *before* the external system responds, so an in-flight attempt is never anonymous.
- **FR-004**: A terminal hold disposition MUST NOT regress to a non-terminal one when a
  later write arrives out of order. The terminal dispositions are **confirmed, released,
  expired and orphan-released**; **pending** and **held** are the only mutable ones.
  Re-acquiring inventory after an expiry MUST produce a new hold with a new idempotency
  key, never a revival of the expired one.
- **FR-005**: Confirmations, failures and saga events MUST accumulate append-only and
  MUST NOT be rewritten once recorded.
- **FR-006**: Retrieved grounding material MUST be deduplicated by identity while
  preserving the relevance order in which items were first seen.
- **FR-007**: Every merge behaviour MUST be idempotent: applying an identical contribution
  a second time MUST leave the conversation unchanged.
- **FR-008**: Every merge behaviour MUST accept a first contribution where no prior value
  exists, without error.
- **FR-009**: The system MUST define domain models for guest, stay, proposal, hold,
  confirmation, saga event, plan, policy flag and dietary requirement.
- **FR-010**: A dietary requirement MUST carry a structured restriction and a severity.
  Free text MAY accompany them and MUST NEVER substitute for them.
- **FR-011**: Tenancy MUST be established once when a conversation begins and MUST NEVER be
  derived from model output.
- **FR-012**: The planning cycle MUST carry an explicit bound and MUST escalate to a human
  when that bound is reached, so no cycle can run unbounded.
- **FR-013**: The error taxonomy MUST classify every failure into exactly one of four
  dispositions: retryable, terminal, requires-human, policy-blocked.
- **FR-014**: Classification MUST be total and deterministic. The set it must cover is the
  union of two sources: the nine system and durability failure modes enumerated in the
  failure matrix at `docs/ResortFlow-HITL-Durability-State.md` section 11, and the failure-code
  enumerations in the five specifications under `docs/contracts/openapi/`. Every member of
  that union MUST resolve, and an input outside it MUST still resolve to the same
  disposition on every run.
- **FR-015**: The tool contract standard MUST define a naming convention, the derivation of
  the idempotency key for mutating operations, a uniform failure envelope mapped onto the
  taxonomy, and a per-operation scope declaration.
- **FR-016**: The tool contract standard MUST require tenancy to be supplied by the calling
  infrastructure rather than by any operation's caller-provided arguments.
- **FR-017**: The mutation ledger MUST record the intent of an external mutation before the
  external call is made, and its outcome after, keyed uniquely by idempotency key.
- **FR-018**: The mutation ledger MUST live in a durable store separate from the workflow
  checkpoint store, so the two can be reasoned about and recovered independently.
- **FR-019**: The mutation ledger MUST allow entries that hold external inventory but have
  no active conversation and no terminal outcome to be identified and released.
- **FR-020**: The local environment definition MUST enumerate the service topology and the
  address each service is reachable at, and MUST NOT include any service implementation.
- **FR-021**: Every interface frozen by this feature MUST be changeable only through a
  recorded architecture decision, never by unilateral edit.
- **FR-022**: The telemetry attribute naming contract MUST be frozen by this feature: the
  canonical attribute names emitted for external calls and workflow steps, the correlation
  identifiers that join a conversation to its mutation ledger entries, and the single
  owning location those names may be defined in. Emission itself is out of scope and
  belongs to the observability feature; only the vocabulary is frozen here.
- **FR-023**: Domain models MUST mark, at field level, which fields carry personal data and
  which carry health data, so that redaction and encryption bind to the contract rather
  than to a separately maintained list.
- **FR-024**: This feature MUST freeze the set of graph node names that every later feature
  binds to — the identity strings by which a workflow step, its checkpoints, and its
  telemetry are addressed. No node logic is defined here; only the frozen name each future
  node MUST use once built.

### Key Entities

- **Conversation State**: the single shared record of an in-flight guest request — who it
  is for, which property and jurisdiction, what has been said, what is planned, what has
  been proposed, held, confirmed or failed, what was retrieved, and what control decisions
  are outstanding.
- **Guest**: the person the request is for, including the entitlements that gate what may
  be offered to them. Carries personal data, marked as such at field level.
- **Stay**: the guest's presence at a property, bounding the dates and location a request
  can address.
- **Proposal**: a candidate item a workstream is offering, before any commitment exists.
- **Hold**: a reversible claim on external inventory, identified before the external system
  responds, carrying an expiry and a disposition. Its disposition is one of pending, held,
  confirmed, released, expired or orphan-released; the last four are terminal.
- **Confirmation**: an irreversible commitment that an external system has acknowledged.
- **Saga Event**: an append-only record of a step in a multi-system transaction, used
  to reconstruct what happened and to drive recovery.
- **Plan**: the decomposition of a request into tasks with their dependencies and
  constraints.
- **Policy Flag**: a compliance determination attached to a conversation, recording what was
  enforced and why.
- **Dietary Requirement**: a structured restriction and severity for a named guest, with
  optional accompanying detail. Safety-critical, and health data — marked as such at field
  level so redaction and encryption apply without a separate list.
- **Ledger Entry**: the durable record of one external mutation attempt — its intent, its
  outcome, the key that joins the two across any interruption, and its current disposition.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Three concurrent contributors produce three retained results — zero result
  loss across 100 repetitions of the concurrent-write scenario.
- **SC-002**: Replaying any recorded contribution a second time produces no change and no
  duplicate, in 100% of replay scenarios tested.
- **SC-003**: 100% of enumerated external failure codes and documented system failure modes
  classify into exactly one of the four dispositions, with no unclassified input.
- **SC-004**: An interruption at any of the three points between recording intent and
  recording outcome leaves the attempt discoverable and releasable — 3 of 3 crash windows
  recoverable.
- **SC-005**: Every one of the seven downstream workstreams can begin building against
  these interfaces without requesting a change to them.
- **SC-006**: The conversation state can be written and read back in full with no loss of
  fidelity to any entity it contains.
- **SC-007**: Every interface change after this feature is accompanied by a recorded
  decision — zero unilateral contract edits.
- **SC-008**: Each significant decision made in this feature is recorded with its context,
  the options considered, the consequences accepted, and a one-paragraph statement of the
  trade-off.
- **SC-009**: The local environment definition enumerates every service the platform
  depends on, validates as a well-formed definition, and contains zero service
  implementations.
- **SC-010**: Every telemetry attribute name used by any later feature traces back to the
  vocabulary frozen here — zero attribute names introduced outside the single owning
  location.
- **SC-011**: Every field carrying personal or health data is identifiable from the model
  definition alone, with no reference to any external list — 100% of such fields marked.
- **SC-012**: Every workflow-step identity used by any later feature traces back to the
  name frozen here — zero step identities introduced outside this registry.

## Assumptions

- **Corrected source reference.** The supplied acceptance criteria required the error
  taxonomy to cover "every failure mode listed in `docs/architecture-and-plan.md` section
  13". That reference does not resolve: the file is
  `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md`, and its section 13 is the phase plan, which
  contains no failure-mode list. The taxonomy is therefore bound to the two places the
  failure modes are actually enumerated — the failure matrix in
  `docs/ResortFlow-HITL-Durability-State.md` section 11 (nine modes), and the failure-code
  enumerations in the five frozen external interface specifications under
  `docs/contracts/openapi/`. **Confirmed during clarification on 2026-09-09** — see FR-014.
- The five external interface specifications are frozen **inputs** to this feature, not
  deliverables of it. They already exist and are treated as authoritative for entity
  vocabulary, failure codes and idempotency semantics.
- **What the five specifications actually simulate.** Named explicitly so the "frozen
  input" claim above is traceable rather than abstract, per
  `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §1 and §9: the dining specification stands in
  for a SevenRooms-equivalent reservation platform, the ticketing specification for a
  Ticketmaster-equivalent event platform, and the stay specification for an Opera-equivalent
  property management system — all three named as such in the specifications' own titles.
  The casino and amenities specifications simulate a generic casino management system and a
  generic spa/amenities scheduler respectively; the source material does not name a specific
  real-world system for either, and this document does not invent one.
- **The workstreams this feature's contracts are built for, named explicitly.** This
  feature builds no agents and no orchestration graph — that is out of scope, stated below
  — but the entities and the node registry it freezes exist to be bound by a specific,
  already-designed roster, so that a reader can confirm nothing was overlooked rather than
  taking scope boundaries on faith. Per §4 of `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md`:
  an Intent Router, a Supervisor, a Planner, five domain subgraphs (Dining, Entertainment,
  Stay, Wellness, and read-only Gaming & Loyalty), a Conflict Checker, an Offer Agent, a
  Policy Engine, a Recovery Agent, and a Human Handoff interrupt. Each of the five domain
  subgraphs is the eventual consumer of one `Domain` enum value; all thirteen bind to the
  node names this feature freezes once feature 005 (multi-agent-orchestration) builds them.
- The two properties and their two jurisdictions are fixed and known at the start of a
  conversation; no feature discovers them at runtime.
- The four architectural decisions already taken with the project owner hold: the workflow
  checkpoint store is the same technology in local and hosted environments; decision
  records 001–006 are written as registered, plus one additional record for the mutation
  ledger; the external interface specifications under `docs/contracts/openapi/` are
  canonical and the duplicate copy is removed; and the acceptance criteria are verified by
  executing them, not by assertion.
- **Scope added during clarification.** The original brief did not mention telemetry or
  data-sensitivity marking. Both were added deliberately (FR-022, FR-023) because both are
  contracts that later features bind to, and both are governed by standards in the project
  constitution that would otherwise be unenforceable until several features later. Only the
  vocabulary and the markers are frozen here; emission and redaction remain out of scope.
- **Scope added after review.** A review of this spec against
  `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §4 found that the graph node-name registry
  (FR-024) was absent despite the constitution's contract-stability standard treating node
  names as load-bearing, and despite this feature's own telemetry vocabulary already
  declaring workflow-step identity a bounded dimension with no registry to bound it against.
  Added for the same reason as FR-022: the alternative is Track C choosing node names
  unilaterally when feature 005 begins, which is exactly the drift this feature exists to
  prevent. One naming question in the source material is unresolved rather than silently
  settled: §4's roster table lists an Offer Agent and a Human Handoff node, but its
  `stateDiagram-v2` omits the Offer Agent entirely and represents Human Handoff only
  implicitly inside the approval interrupt. This feature reserves a name for each rather
  than picking a side of that inconsistency.
- **Three deferrals made explicit after the analyze pass.** Each is a requirement this
  feature can only half-satisfy, because the enforcing runtime does not exist until a later
  feature. They are recorded here rather than left silent, matching how this feature already
  handled `messages` windowing and per-node model tiers:
  - **FR-012, the escalation half.** The bound itself is delivered — `MAX_REPLAN_COUNT = 3`
    becomes a frozen, importable value. Escalating when the bound is breached requires a
    planning cycle to escalate *from*, and no planner exists until feature 005
    (multi-agent-orchestration), which owns that behaviour.
  - **FR-011, the provenance half.** That tenancy is *established once* is delivered
    structurally: it is a plain state key with no reducer, so nothing can merge into it.
    That it is *never derived from model output* is a property of which node writes it, and
    there are no nodes until feature 005.
  - **Hold revalidation on resume.** The constitution (Principle V) and AGENTS.md's hard
    rules both require that holds recorded in state are revalidated against the vendor on
    resume rather than trusted. This feature freezes the `Hold` fields that revalidation
    reads — `idempotency_key`, `hold_id`, `expires_at`, `status` — but the resume path
    itself belongs to feature 005. No requirement in this document should be read as
    claiming revalidation is implemented here.
- This feature delivers contracts only. Vendor simulators, the tool layer, agents and
  retrieval are explicitly out of scope and are separate features.
