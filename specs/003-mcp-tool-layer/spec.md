# Feature Specification: MCP Tool Layer

**Feature Branch**: `003-mcp-tool-layer`

**Created**: 2026-09-10

**Status**: Draft

**Input**: User description: "Phase 2 — MCP Tool Layer (Track B). One MCP server per domain (dining, entertainment, stay, wellness, gaming), with tools generated from the five frozen OpenAPI contracts in docs/contracts/openapi/ and hand-refined for agent ergonomics (fewer, richer tools beat many thin ones). Every tool follows the naming/idempotency/error-shape/scope contract already frozen in docs/contracts/mcp-tool-contract.md: resortflow.<domain>.<verb>_<noun> naming, tenant_id injected server-side from the validated token claim (never caller-supplied, never model-supplied), a client-generated idempotency key derived from (session_id, hold_id, attempt) on every mutating tool, and every vendor error normalized to the ErrorClass taxonomy (RETRYABLE | TERMINAL | REQUIRES_HUMAN | POLICY_BLOCKED) at the MCP boundary per docs/contracts/schemas/tool-error.schema.json — no raw vendor error shape may reach an agent. The gaming domain carries read-only tools only (check_*/list_*/get_*); no create_*/confirm_*/cancel_* tool may exist in that namespace — enforced by the tool's absence from the registry, not a prompt instruction. Tools call the five simulators built in Phase 1 (sims/, docs/contracts/openapi/*.yaml) as their upstream. Also needed: retry with jitter and a circuit breaker per upstream vendor, per-tool scope strings (<domain>:<action>) checked against caller entitlements before invocation, and tool-level OpenTelemetry tracing using the attribute vocabulary already frozen in observability/attributes.py. Exit criteria: an MCP inspector lists every tool with a valid schema; a tenant-crossing call is impossible by construction, proved by test rather than asserted; upstream 500s from any sim surface as typed retryable errors."

## Clarifications

### Session 2026-09-10

- Q: The frozen contract says `tenant_id` is injected "from the validated token claim" and
  scopes are checked against "caller entitlements" — what is the caller-identity mechanism
  for this phase? → A: **A signed token from a local issuer**, carrying the tenant identifier
  and the caller's granted scopes as verified claims. Both tenancy and entitlement derive from
  that verified claim set; a missing, malformed, expired or unverifiable token is refused
  before any tool body executes. This is what makes the tenant-isolation proof meaningful
  rather than circular — a caller genuinely *cannot* assert its own tenancy.
- Q: "Fewer, richer tools beat many thin ones" — what roster size should this spec constrain
  to? → A: **A hard cap of 8 tools per domain**, roughly 30 across the five. The project's
  own architecture rationale cites tool-selection accuracy degrading past roughly 15–20 tools
  in one context; capping at 8 per domain keeps even a two-domain context under that
  threshold. Consolidation is achieved by composing several vendor endpoints into one
  guest-intent-shaped tool, never by overloading one tool with mode flags.
- Q: How far does this phase's deliverable extend — runnable servers, or library-level tools?
  → A: **Runnable.** The five servers actually run and are reachable through the platform's
  existing gateway service, calling the live Phase 1 simulators as their upstream. The exit
  proof is an MCP inspector enumerating every tool with a valid schema against running
  servers, not a registry dump asserted in-process.
- Correction (not a formal clarification question): the five MCP domains and the five Phase 1
  simulators use different names for the same pairings. The mapping is fixed by the frozen
  contracts, not chosen here: `dining` → dining simulator, `entertainment` → ticketing
  simulator, `stay` → front-desk/PMS simulator, `wellness` → amenities simulator, `gaming` →
  casino simulator. Only the first four have reservable inventory; the casino simulator
  publishes no mutating operation at all, which is why the `gaming` domain's read-only
  constraint is structural rather than merely policy.
- Q: When a composite tool's several underlying vendor calls partially succeed — one
  completes, another fails — what should it report to the caller? → A: The normalized
  failure it returns is the failing sub-call's own classified failure, augmented with the
  identity of every sub-call that already completed, so nothing that actually took effect is
  left untracked. The tool itself never attempts to undo or compensate that partial outcome —
  compensating a torn composite operation is the saga layer's job (Principle III, Phase 4–5),
  not this layer's. A composite call is never reported as an overall success when any of its
  sub-calls did not succeed.
- Q: What granularity should a tool's declared entitlement be? → A: Three verbs per domain —
  `<domain>:read`, `<domain>:hold`, `<domain>:commit` — 15 scopes total across the five
  domains, still fitting the frozen `<domain>:<action>` shape. The boundary the vocabulary
  encodes is reversibility, not mutation: placing a hold is reversible and grantable to a
  domain subgraph alongside read; committing is irreversible and granted only to whatever
  performs the confirm step, per Principle III's hold → propose → approve → commit sequence.
  A caller holding `read`+`hold` but not `commit` can advance a booking to the edge of
  irreversibility but cannot cross it. Per-tool scopes were rejected — the tool registry
  already provides per-tool addressability at a different layer, and per-tool entitlements
  would duplicate it without adding a boundary that matters architecturally.
- Q: When the fast-fail protection for an upstream is open, should a retry carrying an
  idempotency key the upstream already knows still be blocked, or let through as a special
  case? → A: Blocked, uniformly, with no special-casing by idempotency key — the protection's
  job is a total answer to "is this vendor healthy," not a case-by-case judgment call, and
  exempting known-key traffic would exempt most of the load present exactly when it opens. One
  consequence follows: a commit-class call whose retries are exhausted while the protection is
  engaged leaves an outcome that is genuinely unknown — the vendor may already hold the
  mutation the caller never saw confirmed — and, unlike a hold, that state does not self-heal
  by expiring. Such a call is classified `REQUIRES_HUMAN` rather than `RETRYABLE` (FR-032):
  reconciling it — probing the vendor by idempotency key once the protection closes and
  resolving to the true outcome, escalating to a human past a bound — is the write-ahead
  ledger's job in a later phase, not this one's, but the classification that makes that
  reconciliation possible is decided here.
- Q: When an upstream returns a success status with a body that doesn't match its frozen
  contract, how should it be classified? → A: The case splits in two. A body that fails to
  parse at all (truncated, invalid JSON) is a transport-level fault and classifies
  `RETRYABLE`. A body that parses but fails its frozen schema is contract drift, not a
  transient condition, and classifies `TERMINAL` — an identical retry would reproduce the
  identical violation. Neither case is ever partially salvaged: no field is extracted from a
  body that fails either check, and the caller receives the classified failure, never a
  partial success. Error class and effect state are orthogonal: on a mutating call, a
  `TERMINAL` classification here says nothing about whether the vendor's mutation actually
  happened — it probably did, and is now unidentifiable from this response alone — so the
  effect is recorded unresolved for the later ledger to reconcile by idempotency key, the same
  mechanism already established for an indeterminate commit outcome. A schema-invalid or
  unparseable body counts toward the upstream's failure-protection threshold exactly like any
  other failure — protection tracks contract compliance, not HTTP status alone.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Reach every vendor through one governed surface (Priority: P1)

A caller that needs something from any of the five vendor systems — a dining table, a show
ticket, a room, a spa slot, a loyalty record — asks for it through one uniform tool surface.
It never learns that the five vendors authenticate five different ways, paginate differently,
format dates differently, or describe failures in three different envelope shapes. It holds no
vendor credential and no vendor client library.

**Why this priority**: this is the feature. Every later phase — the dining vertical slice, the
multi-domain saga, the eval suite — calls vendors exclusively through this surface. Without
it, nothing downstream can be built, and the platform's central architectural claim (one
governed integration surface) is unproven.

**Independent Test**: can be fully tested by invoking at least one tool in each of the five
domains against the running simulators and verifying a well-formed typed result comes back,
with no vendor credential held by the caller and no vendor-specific field shape in the
response — delivering a usable integration surface on its own, independent of every other
story here.

**Acceptance Scenarios**:

1. **Given** the five servers are running against the seeded simulators, **When** a caller
   invokes a read tool in each domain, **Then** each returns a typed result conforming to that
   tool's declared output schema.
2. **Given** a caller holding no vendor credential of any kind, **When** it invokes a tool
   whose upstream requires vendor authentication, **Then** the call succeeds — the vendor
   credential is held and presented by the tool layer, never by the caller.
3. **Given** two domains whose upstreams describe the same concept differently (for example,
   differing date representations or pagination styles), **When** a caller reads from both,
   **Then** both results present that concept in the same normalized form.

---

### User Story 2 - Operate on exactly one tenant, with no way to choose (Priority: P1)

A caller acting for one resort property must be structurally unable to read or alter anything
belonging to the other property — not merely discouraged from it, and not prevented by a rule
it could be argued out of. Tenancy is decided by the caller's verified identity, before any
tool logic runs.

**Why this priority**: this is the phase's headline exit criterion and a direct constitutional
requirement. A tenant-crossing defect in this layer is not a bug to be patched later; it
invalidates the platform for a licensed business.

**Independent Test**: can be fully tested by presenting a credential for one tenant and
attempting, by every available means, to reach the other tenant's data — including naming the
other tenant explicitly in tool arguments and referencing a known identifier that belongs to
it — and verifying every attempt fails or returns nothing.

**Acceptance Scenarios**:

1. **Given** a caller whose verified identity names tenant A, **When** it invokes any tool,
   **Then** the operation is performed against tenant A's data only.
2. **Given** a caller whose verified identity names tenant A, **When** it supplies a tenant
   identifier naming tenant B as a tool argument, **Then** the argument is refused outright —
   no tool accepts a caller-supplied tenant, so the call fails rather than silently ignoring
   the argument.
3. **Given** a caller whose verified identity names tenant A, **When** it references a
   resource identifier that belongs to tenant B, **Then** the call returns not-found or is
   refused, and no tenant B data appears in the result or in any error detail.
4. **Given** a request carrying no credential, an expired one, or one whose signature does not
   verify, **When** any tool is invoked, **Then** the call is refused before the tool body
   executes.

---

### User Story 3 - Receive every failure as one of four known classes (Priority: P1)

A caller handling a failure needs to know only one thing to decide what to do next: is this
worth retrying, permanently failed, something a human must resolve, or something policy
forbids. It must never have to interpret a vendor's own error vocabulary, and must never
receive one.

**Why this priority**: the retry logic, the saga compensation logic, and the human-escalation
routing of every later phase all branch on this single field. If the classification is
incomplete or leaky, the safety behaviour built on top of it is unsound.

**Independent Test**: can be fully tested by driving each simulator's fault-injection
mechanism through every failure mode it supports and verifying every resulting response is a
normalized envelope carrying one of the four classes, with no vendor envelope structure
present.

**Acceptance Scenarios**:

1. **Given** any simulator forced to return a server error, **When** a tool calls it, **Then**
   the caller receives a normalized failure classified as retryable.
2. **Given** any simulator forced into any of its supported failure modes, **When** a tool
   calls it, **Then** the caller receives a normalized failure carrying exactly one of the
   four classes — the classification is total, with no unclassified or pass-through outcome.
3. **Given** a normalized failure, **When** the caller inspects it, **Then** the vendor's own
   code, status and detail are present as diagnostic fields, but the vendor's envelope
   structure is not, and the field the caller branches on is the class.
4. **Given** a retryable failure, **When** the caller inspects it, **Then** it carries the
   identifying key of the call that failed, so a retry or a later reconciliation can be joined
   to this exact failure.
5. **Given** any failure at all, **When** it is recorded or returned, **Then** its detail text
   contains no guest-identifying information.
6. **Given** a simulator forced to return a success status with an unparseable body, **When**
   a tool calls it, **Then** the caller receives a `RETRYABLE` failure; **given** the same
   forced to return a success status with a parseable but schema-invalid body, **When** a tool
   calls it, **Then** the caller receives a `TERMINAL` failure — and in neither case does any
   field from that body reach the caller.

---

### User Story 4 - Retry a mutation without causing a second one (Priority: P1)

A caller that has just asked to confirm a booking, and does not know whether the request
arrived, must be able to send the exact same request again and end up with exactly one
booking.

**Why this priority**: two-phase mutation is only a safety mechanism if retries are safe. Every
mutating path in every later phase depends on this, and the simulators already honour the
contract from the upstream side — this phase is where the calling side of it is built.

**Independent Test**: can be fully tested by issuing a mutating call, repeating it identically,
and verifying exactly one mutation occurred and the second call returned the first call's
outcome rather than an error.

**Acceptance Scenarios**:

1. **Given** a mutating tool, **When** it is invoked without an idempotency key, **Then** the
   call is refused — the key is required, not optional.
2. **Given** a mutating call that timed out with an unknown outcome, **When** it is retried
   with the same key, **Then** exactly one mutation exists and the retry returns the original
   outcome.
3. **Given** a mutating call that failed terminally and is being deliberately re-attempted as a
   new logical action, **When** it is retried, **Then** it carries a distinct key — the attempt
   component of the key changes, so this is not treated as a replay of the failed call.
4. **Given** a read-only tool, **When** its schema is inspected, **Then** it declares no
   idempotency key — the requirement applies to mutations only.

---

### User Story 5 - Let a failing vendor degrade without taking the platform with it (Priority: P2)

When one vendor becomes slow or starts failing, calls to that vendor must stop quickly and
predictably rather than piling up, and the other four domains must keep working normally.
Recovery must be automatic once the vendor is healthy again.

**Why this priority**: it protects the availability of the whole surface, but the surface has
to exist and be correct first — so it sits just behind the P1 stories.

**Independent Test**: can be fully tested by holding one simulator in a failing state, driving
repeated calls to it, and verifying calls begin failing fast with a typed error while the other
four domains continue to succeed; then restoring the simulator and verifying calls resume
without intervention.

**Acceptance Scenarios**:

1. **Given** an upstream returning retryable failures, **When** a tool calls it, **Then** the
   call is retried a bounded number of times with varied (not fixed-interval) spacing before
   the failure is returned.
2. **Given** an upstream that has failed repeatedly past a defined threshold, **When** a
   further call is made to it, **Then** the call fails immediately with a typed error rather
   than waiting on the upstream.
3. **Given** one upstream in that fast-failing state, **When** tools for the other four domains
   are invoked, **Then** they operate normally — the protection is per-vendor, not global.
4. **Given** an upstream that has recovered, **When** the protection's cool-down elapses,
   **Then** calls resume succeeding with no operator action.
5. **Given** an operation that has already mutated vendor state, **When** it is retried
   automatically, **Then** it reuses the same idempotency key — automatic retry never causes a
   duplicate mutation.
6. **Given** a `commit`-scoped call whose retries are exhausted while the upstream's protection
   is open, **When** its outcome is surfaced, **Then** it is classified `REQUIRES_HUMAN` — not
   `RETRYABLE` — because the call may already have taken effect at the vendor.

---

### User Story 6 - Invoke only what the caller is entitled to invoke (Priority: P2)

A caller granted only read access to a domain must be unable to place a hold in that domain,
and one granted read and hold access must still be unable to commit — cross the
reversible/irreversible boundary — on its own. The check must not depend on anyone having
named those tools correctly.

**Why this priority**: entitlement separation is what makes the surface governed rather than
merely uniform, and it is the enforcement point that later policy work (Track G) and the
human-approval interrupt (Principle III) bind to. It depends on the surface and its identity
mechanism already existing.

**Independent Test**: can be fully tested by presenting a credential granting only
`<domain>:read`, invoking a read tool (succeeds) and a hold tool (refused); then granting
`<domain>:read`+`<domain>:hold` and verifying a hold tool now succeeds but a commit tool is
still refused.

**Acceptance Scenarios**:

1. **Given** a caller lacking a tool's declared entitlement, **When** it invokes that tool,
   **Then** the call is refused before the tool body executes and before any upstream call is
   made.
2. **Given** a caller holding a tool's declared entitlement, **When** it invokes that tool,
   **Then** the call proceeds normally.
3. **Given** a caller holding `<domain>:read` and `<domain>:hold` but not `<domain>:commit`,
   **When** it invokes a commit tool in that domain, **Then** the call is refused — holding a
   reversible entitlement never implies the irreversible one.
4. **Given** every tool in the registry, **When** each is inspected, **Then** it declares an
   entitlement of the form domain-plus-action, and no tool exists without one.
5. **Given** a tool whose name does not accurately reflect what it does, **When** it is
   invoked without the matching entitlement, **Then** it is still refused — enforcement reads
   the declared entitlement, never the name.

---

### User Story 7 - Confirm the gaming domain cannot mutate anything (Priority: P2)

Anyone auditing the platform — an engineer, a reviewer, a regulator — must be able to establish
that no gaming transaction can be performed through this surface by listing what exists, not by
reading a policy document or a prompt.

**Why this priority**: it is a constitutional guarantee and a licensing-relevant claim, and it
is cheap to prove — but it can only be proved once the registry exists.

**Independent Test**: can be fully tested by enumerating every tool in the gaming domain and
verifying every one is a read operation, with no create, confirm, or cancel operation present
anywhere in that namespace.

**Acceptance Scenarios**:

1. **Given** the running gaming server, **When** its tools are enumerated, **Then** every tool
   is a read operation and none mutates vendor state.
2. **Given** the gaming namespace, **When** it is searched for any create, confirm or cancel
   operation, **Then** none exists — the guarantee is the absence of the tool, not a refusal
   inside one.
3. **Given** a caller attempting to invoke a gaming mutation by name, **When** the call is
   made, **Then** it fails as an unknown tool rather than as a denied one.

---

### User Story 8 - Trace any tool call after the fact (Priority: P3)

An engineer investigating what happened during a conversation needs every vendor interaction to
have left a durable, queryable record: which tool, which domain, which tenant, which session,
which idempotency key, and whether it succeeded.

**Why this priority**: it is essential for the platform overall, but the correctness stories
above can be verified without it, and the reporting backends themselves are a later phase's
work. What must land here is that every call emits a record using the already-frozen
vocabulary.

**Independent Test**: can be fully tested by invoking a tool and verifying exactly one
telemetry record was emitted for it, carrying the expected identifiers, with every attribute
name drawn from the project's single frozen vocabulary.

**Acceptance Scenarios**:

1. **Given** any tool invocation, successful or failed, **When** it completes, **Then** exactly
   one telemetry record for that call is emitted.
2. **Given** an emitted record, **When** its attributes are inspected, **Then** every attribute
   name comes from the project's frozen telemetry vocabulary, and no name is defined anywhere
   else.
3. **Given** a failed call, **When** its record and its returned failure are compared, **Then**
   they can be joined — the failure carries the correlating trace identifier.
4. **Given** any emitted record, **When** its contents are inspected, **Then** no guest-
   identifying information is present.

---

### Edge Cases

- **Resolved** (already settled by FR-007, no ambiguity): the platform recognizes exactly two
  tenants (MGV, MHB); a credential naming any other tenant identifier fails verification under
  FR-007 like any other malformed credential — there is no separate "unknown tenant" outcome
  to design.
- **Resolved** (already settled by FR-013, no ambiguity): a credential that verifies but
  carries no entitlements is refused on every tool it invokes — FR-013's check applies
  uniformly regardless of how many or how few entitlements the caller holds; zero is not a
  special case.
- **Resolved** (already settled by FR-009, no ambiguity): a tool argument naming a tenant
  identifier is refused whether or not it happens to match the caller's own tenant — no tool
  accepts a caller-supplied tenant at all, so correctness of the value is never evaluated.
- **Resolved**: an upstream returning a success status with a body that fails to parse is
  classified `RETRYABLE`; one that parses but fails its frozen schema is classified `TERMINAL`
  (FR-034, FR-035), and no part of either body is ever passed through (FR-036).
- **Resolved** (already frozen in `specs/001-contracts-foundation/data-model.md`, not decided
  here): an upstream failure shape the classification table does not recognize falls back by
  status family — unknown code with a `5xx` status classifies `RETRYABLE`, unknown code with a
  `4xx` status classifies `TERMINAL` — so an unfamiliar vendor code never raises and never goes
  unclassified (FR-033).
- **Resolved** (already settled by FR-018, no ambiguity): a manual retry after the automatic
  budget is exhausted on a `RETRYABLE` failure reuses the same idempotency key — FR-018 only
  advances the attempt component after a terminal failure, and an exhausted-but-still-retryable
  failure is not terminal.
- **Resolved**: the fast-fail protection opening while a `commit` call's outcome is unknown
  (FR-031, FR-032) blocks any further attempt uniformly and classifies the indeterminate
  outcome `REQUIRES_HUMAN`; reconciling it is the write-ahead ledger's job in a later phase.
- What happens when an upstream signals a wait interval longer than the caller's own patience?
- **Resolved**: a composite tool's several underlying vendor calls partially succeeding
  (FR-030) surfaces the failing sub-call's classified failure plus the identity of every
  sub-call that already completed; the tool never compensates that outcome itself.
- **Resolved** (already settled by FR-001/FR-002, no ambiguity): a tool name that does not
  exist in any domain's registry fails as an unknown tool — there is nothing to check
  tenancy, entitlement, or schema against, so this is a lookup failure, not a new outcome
  category.

## Requirements *(mandatory)*

### Functional Requirements

**Surface and registry**

- **FR-001**: System MUST provide five independently operable domain servers — dining,
  entertainment, stay, wellness, and gaming — each exposing a tool registry over the standard
  tool protocol and each reachable while running.
- **FR-002**: Every tool MUST publish a machine-readable input and output schema, and every
  published schema MUST be valid and enumerable by a standard protocol inspector against a
  running server.
- **FR-003**: Every tool name MUST conform to the frozen project naming convention
  (project-prefix, domain, verb-noun), and the domain component MUST be one of the five named
  domains.
- **FR-004**: Each domain server MUST expose no more than 8 tools. Tools MUST be shaped around
  guest intent rather than mirroring individual vendor endpoints; where several vendor calls
  serve one intent, they MUST be composed into one tool rather than exposed separately.
- **FR-005**: The five domain servers MUST call the project's five existing vendor simulators
  as their upstream, and MUST NOT require any change to those simulators' **frozen contract
  surface** — no path, field, status code, or auth scheme may be added, removed, or altered to
  accommodate a tool. Out-of-band **test controls** that live outside the frozen contracts
  entirely (the established `X-Sim-Fault` header mechanism and its named modes, per ADR-008)
  are excluded from this prohibition: adding a new fault mode changes no contract, and is
  scheduled through the cross-track integration commit rather than performed by this feature's
  own track. Runtime behaviour of the tool layer MUST NOT depend on any such control.
- **FR-006**: No tool MUST expose vendor-specific transport concerns — credentials, pagination
  cursors, envelope wrappers, or vendor-native date representations — in its published schema.

**Tenancy and identity**

- **FR-007**: System MUST require a verifiable caller credential on every tool invocation and
  MUST refuse the call before the tool body executes when the credential is absent, malformed,
  expired, or fails verification.
- **FR-008**: System MUST derive the operating tenant solely from the verified credential's
  claims, and MUST apply it to every upstream call.
- **FR-009**: No tool MUST accept a tenant identifier as a caller-supplied argument. A call
  supplying one MUST be refused rather than having the argument ignored.
- **FR-010**: A caller operating as one tenant MUST NOT be able to read or modify another
  tenant's data by any means available through this surface, including by referencing another
  tenant's resource identifiers.
- **FR-011**: No response, failure detail, or emitted record MUST disclose data belonging to a
  tenant other than the caller's.

**Entitlements**

- **FR-012**: Every tool MUST declare an entitlement of the form domain-plus-action, where
  action is exactly one of three verbs — `read`, `hold`, or `commit` — chosen by where the
  tool sits relative to the reversible/irreversible boundary (Principle III): a `read` tool
  performs no mutation, a `hold` tool performs only reversible mutation, and a `commit` tool
  performs an irreversible one. No tool declares more than one of the three.
- **FR-013**: System MUST verify the caller holds a tool's declared entitlement before the tool
  body executes and before any upstream call is made, refusing the call otherwise. Holding
  `read` or `hold` for a domain MUST NOT imply `commit` for that domain — the three are
  independent grants, and crossing into `commit` requires it explicitly.
- **FR-014**: Entitlement enforcement MUST read each tool's declared entitlement and MUST NOT
  be derived from the tool's name.

**Mutation safety**

- **FR-015**: Every mutating tool MUST require an idempotency key derived from the caller's
  session, the client-side hold identifier, and an attempt counter, and MUST refuse the call
  when it is absent.
- **FR-016**: No read-only tool MUST require or accept an idempotency key.
- **FR-017**: A repeated mutating call carrying an unchanged idempotency key MUST NOT produce a
  second mutation, and MUST return the original outcome.
- **FR-018**: Automatic retries performed by the platform MUST reuse the original idempotency
  key; only an explicit, caller-initiated re-attempt after a terminal failure MUST advance the
  attempt component.

**Error normalization**

- **FR-019**: System MUST normalize every unsuccessful outcome from every upstream to the
  project's frozen failure envelope, classified into exactly one of retryable, terminal,
  requires-human, or policy-blocked. The classification MUST be total: no outcome may be
  returned unclassified or passed through in vendor form.
- **FR-020**: An upstream server error from any of the five vendors MUST be classified as
  retryable.
- **FR-021**: Normalized failures MUST preserve the vendor's own code, status and detail as
  diagnostic fields while excluding the vendor's envelope structure, and MUST carry the
  correlating trace identifier; retryable failures MUST additionally carry the idempotency key
  of the failing call.
- **FR-022**: Failure detail text MUST contain no guest-identifying information.
- **FR-033**: A failure code the classification table does not recognize MUST fall back by
  status family, per the project's frozen mapping in
  `specs/001-contracts-foundation/data-model.md`: an unrecognized code carried on a `5xx`
  status classifies retryable, and on a `4xx` status classifies terminal — an unfamiliar
  vendor code MUST NOT raise an unhandled error and MUST NOT go unclassified.
- **FR-034**: A response body that fails to parse at all on an otherwise-successful status
  (truncated, malformed) MUST be classified `RETRYABLE` — this is a transport-level fault, not
  a vendor contract violation.
- **FR-035**: A response body that parses but fails its frozen schema on an otherwise-
  successful status MUST be classified `TERMINAL` — a vendor contract violation, not a
  transient condition an identical retry could resolve.
- **FR-036**: A tool MUST NOT extract or pass through any field from a response body that
  fails parsing or schema validation. Such a response is rejected as a whole, in the shape of
  its classified failure, never as a partial success.
- **FR-037**: An error class MUST NOT be read as evidence of whether a mutating call's
  vendor-side effect did or did not occur — the two are orthogonal. For any mutating-call
  failure that does not affirmatively rule out the effect having taken place — including a
  `REQUIRES_HUMAN`-classified indeterminate commit outcome (FR-032) and a `TERMINAL`-classified
  schema-invalid response to a mutating call (FR-035) — the effect's status MUST be recorded as
  unresolved rather than assumed absent, for the write-ahead ledger to reconcile in a later
  phase.
- **FR-038**: A schema-invalid or unparseable response body MUST count toward its upstream's
  failure-protection threshold on the same basis as any other failure — the protection
  measures contract compliance, not HTTP status code alone.
- **FR-030**: When a composite tool (one composing multiple vendor calls behind a single
  guest-intent tool, per FR-004) fails after one or more of its underlying vendor calls
  already succeeded, its normalized failure MUST additionally identify every already-completed
  sub-call — which resource it committed and how a caller or the saga layer can join to it for
  reconciliation — so no effect that actually took place is left silently untracked. The tool
  MUST NOT attempt to undo or compensate that effect itself, and MUST NOT report the composite
  call as an overall success when any sub-call did not succeed.

**Resilience**

- **FR-023**: System MUST retry retryable upstream failures a bounded number of times with
  varied (non-fixed) spacing between attempts before surfacing the failure, and MUST honour an
  upstream-supplied wait interval when one is present.
- **FR-024**: System MUST maintain per-upstream failure protection that, past a defined failure
  threshold, causes subsequent calls to that upstream to fail immediately with a typed failure
  rather than waiting on it, and that resumes normal operation automatically after a defined
  recovery interval.
- **FR-025**: Failure protection MUST be scoped per upstream vendor: one failing vendor MUST NOT
  affect the availability of the other four domains.
- **FR-031**: Failure protection MUST block calls to an open upstream uniformly, with no
  exemption for a call carrying an idempotency key already known to that upstream — a retry of
  a known key is blocked exactly like any other call while the protection is open.
- **FR-032**: When a `commit`-scoped tool's outcome cannot be determined because its retries
  were exhausted while failure protection for that upstream was open, the resulting failure
  MUST be classified `REQUIRES_HUMAN`, never `RETRYABLE` — an indeterminate commit outcome
  MUST NOT be presented as an ordinary transient failure safe to leave for a future retry that
  may never come. Its effect status follows FR-037.

**Gaming read-only guarantee**

- **FR-026**: The gaming domain MUST contain read operations only. No create, confirm, or cancel
  operation may exist in that namespace, and the guarantee MUST be the absence of such a tool
  from the registry rather than a refusal implemented inside one.

**Observability**

- **FR-027**: Every tool invocation, successful or failed, MUST emit exactly one telemetry
  record identifying the tool, domain, tenant, session, idempotency key where applicable, and
  outcome.
- **FR-028**: Every telemetry attribute name used MUST come from the project's single frozen
  telemetry vocabulary; no attribute name may be defined elsewhere.
- **FR-029**: Emitted records MUST contain no guest-identifying information.

### Key Entities

- **Tool**: One invocable capability in one domain. Carries a contract-conformant name, an
  input and output schema, a declared entitlement, a mutating-or-read classification, and the
  upstream vendor it consumes.
- **Domain Server**: One of five independently operable registries of tools, each bound to a
  single upstream vendor.
- **Caller Identity**: The verified claim set accompanying an invocation, carrying the tenant
  the caller operates as and the entitlements it holds. It is the sole source of tenancy.
- **Entitlement**: A domain-plus-action grant — action being `read`, `hold`, or `commit` —
  held by a caller and declared by a tool, checked before invocation. The three actions are
  independent grants tracking the reversible/irreversible boundary, not a hierarchy.
- **Idempotency Key**: The client-generated value derived from session, hold identifier, and
  attempt, carried by every mutating call and used to make retries safe.
- **Normalized Failure**: The single project-wide failure shape every unsuccessful outcome is
  converted to, carrying one of four classes plus preserved vendor diagnostics. When it
  reports a composite tool's partial failure, it additionally identifies every sub-call that
  already completed (FR-030).
- **Upstream Protection State**: Per-vendor health tracking that decides whether calls to that
  vendor are attempted, retried, or failed immediately.
- **Tool Invocation Record**: The telemetry record emitted once per call, drawn entirely from
  the frozen attribute vocabulary.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A standard protocol inspector, run against the five running servers, enumerates
  100% of tools with a valid, complete schema — zero tools missing, zero schemas invalid.
- **SC-002**: The total roster is at most 40 tools with at most 8 in any one domain, and 100%
  of tool names conform to the frozen naming convention.
- **SC-003**: Across an adversarial isolation pass covering both tenants and all five domains —
  including explicit tenant arguments and borrowed cross-tenant identifiers — zero cross-tenant
  reads and zero cross-tenant modifications succeed, and zero other-tenant values appear in any
  response or failure detail.
- **SC-004**: 100% of invocations presenting an absent, expired, or unverifiable credential are
  refused before any upstream call is made.
- **SC-005**: 100% of invocations lacking a tool's declared entitlement are refused before any
  upstream call is made, and 100% of tools declare exactly one of `read`, `hold`, or `commit`.
  Across every domain, a caller holding `read`+`hold` but not `commit` is refused on 100% of
  attempted commit-tool invocations in that domain.
- **SC-006**: 100% of injected upstream server errors, from every one of the five vendors,
  surface to the caller as a retryable normalized failure; 0% of failures reach a caller in
  vendor envelope form.
- **SC-007**: Every failure mode each simulator supports is exercised, and 100% of the resulting
  outcomes carry exactly one of the four defined classes — no outcome is unclassified.
- **SC-008**: Across all tested retry permutations — including repeated identical mutations,
  timeout-then-retry, and automatic platform retries — the number of duplicate mutations
  observed is zero.
- **SC-009**: 100% of mutating tools reject a call with no idempotency key, and 0% of read-only
  tools require one.
- **SC-010**: With one vendor held in a failing state, calls to it begin failing immediately
  within the defined failure threshold, calls to the other four domains continue succeeding at
  their normal rate, and calls to the recovered vendor resume succeeding within the defined
  recovery interval with zero operator actions.
- **SC-011**: The gaming domain contains zero mutating tools, verified by enumerating the
  running registry rather than by inspecting documentation or instructions.
- **SC-012**: 100% of tool invocations emit exactly one telemetry record, and 100% of attribute
  names used appear in the project's frozen telemetry vocabulary — zero defined elsewhere.
- **SC-013**: Zero guest-identifying values appear in emitted records or in returned failure
  details across the full test pass.
- **SC-014**: 100% of composite-tool partial failures observed in testing correctly identify
  every already-completed sub-call in the returned failure, with zero already-completed
  effects left unreported.
- **SC-015**: 100% of `commit`-scoped calls whose outcome could not be determined because
  retries were exhausted while the upstream's failure protection was open are classified
  `REQUIRES_HUMAN`; 0% are classified `RETRYABLE` or silently reported as successful.
- **SC-016**: 100% of tested unparseable success bodies classify `RETRYABLE` and 100% of
  tested schema-invalid-but-parseable success bodies classify `TERMINAL`; 0% of either case
  passes any body field through to the caller, and 100% count toward their upstream's
  failure-protection threshold.

## Assumptions

- Agents, the orchestration graph, and prompt-level tool selection are out of scope; this phase
  delivers the surface those later phases will call. Consumers are proven here by tests, not by
  an agent.
- The write-ahead hold ledger and saga orchestration are later-phase concerns. Tools in this
  phase return hold identifiers and outcomes; they do not own saga state, do not decide
  compensation, and do not persist conversation state.
- Deterministic policy enforcement (responsible-gaming checks, allergen assertion,
  jurisdiction-specific messaging) belongs to the policy track. This phase carries
  policy-blocked only as a class the failure envelope can express, and the gaming read-only
  guarantee, which is structural rather than policy-evaluated.
- Telemetry export to any particular backend is a later-phase concern. This phase emits records
  against the frozen vocabulary and is verified by asserting on emitted attributes, not by
  backend delivery.
- The caller-credential issuer used in this phase is a local development fixture, not a
  production identity provider; the claim shape it issues is what this phase binds to.
- The five frozen vendor contracts and the five simulators are unchanged by this phase. If a
  tool's ergonomics appear to require a contract change, that is raised as a blocker rather
  than resolved by editing a frozen contract.
- Exact retry counts, jitter distribution, failure thresholds and recovery intervals are
  implementation-plan details; this spec requires only that each exists, is bounded, and is
  per-vendor.
- "Fewer, richer tools" means composing multiple vendor calls behind one guest-intent tool. It
  does not mean collapsing unrelated operations behind a single tool with a mode argument,
  which would move the selection problem into the arguments rather than solving it.
- **Carried forward to `/speckit-plan`, not resolved here**: FR-030's completed-sub-call data
  does not obviously fit the frozen `tool-error.schema.json` envelope as it stands today
  (`additionalProperties: false` over an enumerated field set with no place for a list of
  completed effects). Whether that data extends the frozen envelope (an ADR-backed amendment)
  or travels as a companion record alongside it is a planning-time decision, not a spec-level
  one — flagged here so it is not discovered as an unplanned contract change mid-implementation.
- **Carried forward to `/speckit-plan`, not resolved here**: per this feature's clarification,
  a composite tool retrying its whole operation MUST derive each of its sub-calls' idempotency
  keys deterministically, so the same sub-call gets the same key on every retry of the same
  attempt — otherwise a retried composite could double-commit a sub-call whose key changed
  underneath it. This follows from FR-004/FR-015/FR-018 applied per sub-call; it is called out
  explicitly because it is easy to lose in the transition from spec to design.
- **Carried forward beyond this phase, not resolved here**: FR-037's unresolved effect status
  — whether triggered by an indeterminate commit outcome (FR-032) or a schema-invalid response
  to a mutating call (FR-035) — is only half the mechanism; this phase records that the effect
  is unresolved, it does not reconcile it. Actually reconciling it — an `UNRESOLVED` state in
  the write-ahead hold ledger (distinct from a hold's own `active/confirmed/released/expired`
  states, since an unresolved commit does not self-expire), a reconciler that probes the vendor
  by idempotency key once the upstream's failure protection closes, and escalation to a human
  past a bound if the probe stays inconclusive — belongs to the ledger and durability work in a
  later phase (Principle V; the project's hard rule to never trust recorded hold state on
  resume without revalidating against the vendor applies here directly). This phase's
  obligation is only to classify the outcome and its effect status correctly so that later
  reconciliation has something honest to act on.
- **FR-030 and SC-014 have no shipped producer in this phase, by construction.** Every
  composite tool in this feature's roster combines read-only vendor calls; a composite that
  performs two or more *mutations* is inherently cross-domain (dinner plus show plus room), and
  cross-domain composition is the saga layer's job in a later phase, explicitly out of scope
  here. The partial-failure mechanism and its envelope payload are therefore built and proven
  against a synthetic two-slot mutating composite in test, so the behaviour is real and
  measurable, while no contrived production tool is invented to justify it. The same applies to
  the `completed_sub_calls` half of the `tool-error.schema.json` amendment: it is deliberately
  forward-looking, and its first real producer will be a later phase's saga tooling.
- **Cross-track dependency, flagged not resolved here**: testing FR-035's and FR-038's
  schema-invalid-response behavior requires the simulators to be able to produce a
  schema-invalid-but-parseable success body on demand. That means adding a new fault-injection
  signal (e.g. `schema_drift`) to the existing sim fault vocabulary — `sims/_shared/faults.py`
  and `specs/002-simulation-ecosystem/contracts/sim-control-plane.md`, both Track A's, both
  already shipped in Phase 1. This feature's own track (`mcp/`) cannot add it unilaterally; it
  is a cross-track addition scheduled through the integration commit the working agreement
  already reserves for exactly this situation, not something assumed to already exist.
