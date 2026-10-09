# Feature Specification: Evaluation & Observability Gate

**Feature Branch**: `007-eval-observability-gate`

**Created**: 2026-09-13

**Status**: Draft

**Input**: User description: "Build the Phase 7 Evaluation & Observability capability for ResortFlow: a scored, CI-blocking evaluation suite plus end-to-end conversation tracing, so that the accuracy, safety, and reliability of every agent domain (dining, stay, entertainment, wellness, gaming) is measured continuously rather than asserted by developers. Covers retrieval-quality scoring, agent-trajectory scoring, a saga-correctness suite across the three transactional domains, per-node cost/latency budgets, a single CI-blocking scored report, named judge models on every metric, end-to-end conversation inspection, and a cloud-agnostic evaluation/tracing layer with no hardcoded vendor identifiers and no live-cloud dependency for the gate to pass."

## Clarifications

### Session 2026-09-13

- Q: What is the maximum wall-clock time the full evaluation suite is allowed to take
  before it must produce a merge-blocking verdict? → A: Three-tier structure. **PR
  tier** (target under ~10 minutes, blocks every pull request): binary correctness
  gates, a stratified single-run retrieval-quality subset, saga permutations scoped to
  only the domains the change touches, and a fixed cost/latency trace set. **Merge-queue
  tier** (blocks the final merge, runs less frequently than every push): the full
  retrieval-quality eval set at the 3×-repeated, variance-adjusted threshold, the
  complete saga-permutation sweep across all three transactional domains, the full
  multi-turn adversarial script, and the full agent-trajectory eval set. **Nightly
  tier** (non-blocking, report-only): red-team/adversarial probing and baseline-drift
  reporting. The ~90s/turn latency observed in Phase 6 adversarial testing is recorded
  as an optimization target, not an accepted constant — both blocking tiers are
  expected to get faster via independent-probe parallelization, fixed-seed cached
  simulator responses, pre-computed retrieval contexts where retrieval itself isn't
  under test, and batched judge-model calls.
- Q: When a dependency the evaluation suite needs mid-run (the judge-model API, or a
  vendor simulator a saga case depends on) becomes unavailable, what should happen to
  that run's verdict? → A: Bounded retry (2×, with backoff, capped by total added
  wall-clock so it can't blow the PR tier's time budget). If still unavailable, the
  affected case is marked NOT_EVALUATED and the run verdict is INCOMPLETE — never PASS,
  in any environment. Whether INCOMPLETE blocks the merge is environment-configured:
  it warns (non-blocking) locally, and blocks in the merge-queue tier. A simulator
  response produced by the project's existing fault-injection mechanism (a
  deliberately requested fault) is an expected test result, not a dependency error —
  only an unreachable simulator counts as "unavailable."
- Q: Should a Coverage Exemption expire automatically, or persist until someone
  manually resolves it? → A: Both a default expiry window and early clearing. On
  expiry the exemption is void and the gate fails closed immediately — no grace
  period, no auto-renewal. Renewal requires filing a brand-new exemption with fresh
  justification, not extending the old one. A third request against the same coverage
  surface requires either a recorded ADR justifying continued deferral or removal of
  the still-uncovered capability. The window length matches the same
  capability-declaration tiers that trigger the coverage requirement in the first
  place: 7 days for a `commit` entitlement, a new saga participant, or a compensation
  path; 14 days for a `hold` entitlement, a tenant-scoped content corpus, or a policy
  rule; 30 days for any other capability subject to a coverage requirement. Early
  clearing happens only when the coverage gate actually passes for that surface — a
  closed tracking issue is not evidence a test exists.
- Q: When a statistical gate compares against the main-branch baseline to detect a
  regression, what exactly is "the baseline," and how is the comparison computed? → A:
  The baseline-delta check (FR-007 condition (b)) compares the **current run's raw
  mean** against the **baseline run's raw mean** — never the variance-adjusted
  (mean − 1σ) value, which belongs only to the absolute-floor check (FR-007a) and is a
  one-sided confidence estimate; applying it to both sides of a comparison would
  import the baseline's own variance into the verdict and reintroduce the flakiness
  FR-007a exists to remove. The "meaningful margin" for a regression is
  `max(fixed %, 2 × σ_baseline)`, so noise tolerance lives in the threshold, not in
  the compared values. The baseline itself is the most recent main-branch merge-queue
  run that reached a COMPLETE (non-INCOMPLETE) verdict — an INCOMPLETE run must never
  become a baseline. The baseline record carries its own pinned judge model (FR-009);
  if the current run's judge model differs from the baseline's, the comparison MUST
  report BASELINE_INVALID (requiring re-baselining before the delta check resumes)
  rather than a false regression triggered purely by a judge-model upgrade.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automated regression gate blocks a bad merge (Priority: P1)

An engineer opens a pull request that changes a prompt, a retrieval setting, or an
agent's tool-selection logic. On the pull request itself, a fast tier of the
evaluation suite runs automatically and produces a scored report covering answer
quality, agent behavior, and transactional correctness for the domains the change
touches. Before the change can actually merge, a fuller tier runs the complete suite
across all domains. If either tier measurably regresses any scored category, the
merge is blocked with a clear reason — the same way a broken test blocks a merge
today.

**Why this priority**: This is the core value proposition of the phase — "measured,
not asserted." Every other capability in this feature exists to feed this gate. Without
it, the rest of the feature is a dashboard nobody is forced to look at.

**Independent Test**: Can be fully tested by deliberately degrading a prompt or
retrieval setting on a branch, running the suite, and confirming the merge gate fails
with the regressed metric named in the report. A trajectory-specific regression
(Acceptance Scenario 2) is only observable at the merge-queue tier, since the PR tier
does not score agent trajectory (see Assumptions). Delivers value on its own: it is
the minimum viable form of "measured, not asserted."

**Acceptance Scenarios**:

1. **Given** a change that degrades answer grounding on a knowledge-based question,
   **When** the evaluation suite runs, **Then** the report shows a lowered
   faithfulness/relevance score for the affected domain and the run is marked failing.
2. **Given** a change that causes an agent to select the wrong tool or take
   unnecessary steps for a known scenario, **When** the evaluation suite runs,
   **Then** the report flags the affected trajectory and the run is marked failing.
3. **Given** a change with no measurable regression in any scored category,
   **When** the evaluation suite runs, **Then** the report is marked passing and the
   merge is not blocked on evaluation grounds.
4. **Given** a completed evaluation run, **When** the report is produced,
   **Then** every individual metric value in it names the specific judge model that
   produced it.

---

### User Story 2 - Saga correctness is proven, not assumed (Priority: P1)

Before any change to the booking/reservation flow can merge, an automated suite
exercises every way a multi-step commitment (place a hold, confirm it, or roll it
back) can fail partway through, across every domain that performs this kind of
commitment. Each case proves that a partial failure leaves the guest and the
resort's systems in a correct, recovered state rather than a stuck or double-committed
one.

**Why this priority**: This is the highest-consequence failure mode in the whole
platform — a partial failure that isn't correctly recovered means a guest is charged,
double-booked, or left with a phantom reservation. It is equally foundational to
"measured, not asserted" as User Story 1, and is called out as its own deliverable in
the program plan.

**Independent Test**: Can be fully tested by running the saga suite alone against the
three domains that perform multi-step commitments and confirming every defined
failure-and-recovery permutation is exercised and passes, independent of the
retrieval- or trajectory-scoring categories.

**Acceptance Scenarios**:

1. **Given** a commitment flow where the second of two steps fails,
   **When** the saga suite runs that case, **Then** the report confirms the first
   step was correctly rolled back and no partial commitment remains.
2. **Given** a commitment flow where every step succeeds,
   **When** the saga suite runs that case, **Then** the report confirms the full
   commitment completed and no unnecessary rollback occurred.
3. **Given** all defined failure permutations across the three transactional domains,
   **When** the saga suite completes, **Then** the report shows each permutation
   individually, not collapsed into a single pass/fail line.

---

### User Story 3 - An engineer diagnoses a single guest conversation end-to-end (Priority: P2)

A guest reports that something went wrong during their stay (a wrong recommendation,
a booking that didn't happen, an unexpected wait). An engineer or support operator
looks up that specific conversation and sees, in order, what the guest said, what the
system decided to do at each step, what information it retrieved, what it produced,
and how long and how much each step cost — without needing to reconstruct it from
scattered application logs.

**Why this priority**: This is the day-two operational payoff of the same
instrumentation the eval suite depends on. It's ranked below the two gates because the
gates are what prevent bad changes from shipping in the first place; this is what
makes the ones that slip through diagnosable.

**Independent Test**: Can be fully tested by taking a known conversation, retrieving
its end-to-end record, and confirming every step (input, decision, tool call,
retrieved context, output, cost, latency) is present and in the correct order — without
running the evaluation suite at all.

**Acceptance Scenarios**:

1. **Given** a completed guest conversation, **When** a project engineer looks it up,
   **Then** they see every turn's tool calls, retrieved context, output, cost, and
   latency in chronological order.
2. **Given** the same conversation, **When** it is later scored by the evaluation
   suite, **Then** the suite scores it from this same end-to-end record rather than a
   separate, potentially divergent log.

---

### User Story 4 - Cost and latency stay within budget (Priority: P3)

An engineering lead wants to know, per conversation and per processing step, whether
the system is staying within its expected cost and time budget — and wants an
early, automatic flag if a change makes any step routinely slower or more expensive
than expected, before it shows up as a guest-facing complaint or a surprise bill.

**Why this priority**: Correctness (Stories 1–2) and diagnosability (Story 3) are more
urgent than cost/latency control, but budget drift is the kind of gradual regression
that nothing else in this feature would otherwise catch.

**Independent Test**: Can be fully tested by running a conversation whose cost or
latency deliberately exceeds a defined budget for one step and confirming the report
flags that step specifically, independent of whether the conversation's correctness
scores pass.

**Acceptance Scenarios**:

1. **Given** a processing step whose actual cost or latency exceeds its assigned
   budget, **When** the evaluation suite runs, **Then** the report flags that step by
   name.
2. **Given** a conversation where every step stays within budget, **When** the
   evaluation suite runs, **Then** no budget flag is raised for that conversation.

---

### Edge Cases

- **Dependency unavailable mid-run** (judge-model API, or an unreachable simulator —
  not one returning a deliberately injected fault): retried a bounded number of times,
  then the case is marked NOT_EVALUATED and the run verdict is INCOMPLETE; whether
  that blocks the merge is environment-configured (see FR-021–FR-024).
- **Missing eval coverage** for a newly introduced or modified capability: fails
  closed automatically, scoped to exactly what the change touches, bypassable only by
  an explicit, tracked exemption (see FR-009a).
- **Correctness passes but a cost/latency budget is exceeded, or vice versa**: either
  category blocks the merge independently — binary and statistical gates never require
  a combined threshold to fail (see FR-007).
- **A project engineer requests the end-to-end record of a conversation containing
  guest personal information**: it is returned with that information masked, never
  denied outright and never returned unmasked (see FR-018).
- **The suite runs with no live network access to any cloud vendor's proprietary
  observability/evaluation platform** (the judge model's own LLM call is a separate,
  expected exception — see FR-013): the gate must still reach a complete verdict
  (PASS/FAIL/INCOMPLETE); only optional, non-blocking platform add-ons may be
  unavailable (see FR-013, FR-016).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The evaluation suite MUST score every knowledge-grounded answer it
  evaluates for context recall, context precision, faithfulness, and answer relevance.
- **FR-002**: The evaluation suite MUST score every evaluated conversation turn for
  correct tool selection, plan quality, and unnecessary-step rate.
- **FR-003**: The evaluation suite MUST include an automated case for every defined
  failure permutation of the hold/confirm/compensate commitment pattern, across each
  of the three transactional domains (dining, stay, entertainment).
- **FR-004**: The evaluation suite MUST record actual cost and latency per processing
  step for every conversation it evaluates, and compare each against that step's
  assigned budget.
- **FR-005**: The evaluation suite MUST flag, individually, any step or conversation
  whose actual cost or latency exceeds its assigned budget.
- **FR-006**: The system MUST provide a single command per gate tier (FR-019) that runs
  that tier's scope across the four evaluation categories — retrieval quality, agent
  trajectory, saga correctness, and cost/latency — and produces one consolidated
  scored report for that run.
- **FR-007**: The consolidated report MUST function as an automated merge gate, applying
  two distinct gate types rather than one uniform threshold:
  - **Binary gates** (zero tolerance): saga correctness, tenant isolation, allergen
    fidelity, policy enforcement, and tool schema validity. A single failing case in
    any of these MUST block the merge outright — there is no acceptable rate of
    failure for these categories.
  - **Statistical gates** (threshold-based): retrieval quality, faithfulness, agent
    trajectory quality, and cost/latency. These MUST fail the merge if either:
    (a) the current run's variance-adjusted score (FR-007a) falls below its defined
    absolute floor, or
    (b) the current run's **raw mean** regresses from the baseline run's **raw mean**
    (FR-007c) by more than a meaningful margin — a score that stays above the floor
    but drops materially from baseline (for example, 0.92 → 0.86 against a 0.85 floor)
    MUST still be treated as a regression and block the merge. Condition (a) and
    condition (b) MUST use distinct statistics (variance-adjusted for the floor,
    raw means for the delta) and MUST NOT be conflated.
- **FR-007a**: To prevent judge-model scoring variance from producing flaky,
  non-reproducible gate failures, every statistical-gate metric in the merge-queue
  tier (FR-019) MUST be computed from 3 repeated runs of its eval set within the same
  evaluation run (not a single pass), and the absolute-floor check (FR-007 condition
  (a)) MUST be evaluated against a variance-adjusted score (the mean reduced by one
  standard deviation across those 3 repeats) rather than a single raw score. The PR
  tier's stratified retrieval subset runs once, not 3 times, to stay within its speed
  target (FR-019).
- **FR-007b**: The meaningful margin used by the baseline-delta check (FR-007
  condition (b)) MUST be computed as `max(fixed percentage threshold, 2 × standard
  deviation of the baseline run's repeated measurements)`, so that noise tolerance is
  carried by the threshold itself rather than by adjusting either side of the
  comparison.
- **FR-007c**: A run MAY serve as the baseline for FR-007 condition (b) only if it is
  the most recent main-branch merge-queue-tier run that reached a COMPLETE Run
  Verdict (FR-022) — an INCOMPLETE run MUST NEVER become a baseline. The baseline
  record MUST carry the pinned judge model (FR-009) that produced it; if the current
  run's judge model differs from the baseline's, the delta check MUST report
  BASELINE_INVALID and be suspended until a new baseline is established under the
  current judge model, rather than reporting a false regression caused purely by a
  judge-model change. The absolute-floor check (FR-007a) is unaffected by
  BASELINE_INVALID and continues to apply.
- **FR-008**: A deliberately degraded prompt, or a regression in retrieval or agent
  behavior, MUST cause the gate to fail — not merely lower a score that remains
  reported as passing.
- **FR-009**: Every metric value in every produced report MUST name the specific judge
  model that produced it, and that judge model MUST be pinned to a fixed version for
  the run (never a "latest"/floating alias) so a judge-model change can never silently
  explain a score change.
- **FR-009a**: The set of domains/flows/capabilities that require saga-failure or
  retrieval-quality coverage before merge MUST be derived automatically from what a
  change actually touches, not from a separately maintained list that can go stale.
  Specifically, the gate MUST fail closed (treat missing coverage as a blocking
  failure) whenever a change introduces or modifies, grouped into two urgency tiers:
  - **Tier 1** — a tool that declares a `commit` entitlement, a new saga participant,
    or a compensation path.
  - **Tier 2** — a tool that declares a `hold` entitlement, a tenant-scoped content
    corpus, or a policy rule.

  Changes limited to read-only (`read`-entitlement) tools or non-behavioral
  configuration tuning MUST only warn, not block. An explicit, individually reviewable
  exemption (an annotation carrying a reason and a linked tracking issue) MAY bypass a
  specific required-coverage failure, subject to the lifecycle rules in FR-025–FR-029;
  every currently active exemption MUST be counted and listed in the evaluation
  report so exemptions cannot silently accumulate.
- **FR-010**: The evaluation suite MUST NOT use a judge model weaker than the model
  being judged to score that model's output.
- **FR-011**: The system MUST let any project engineer retrieve a complete,
  chronologically ordered, end-to-end record of any single evaluated or live guest
  conversation, across either tenant — inputs, decisions, tool calls, retrieved
  context, outputs, cost, and latency per step. This feature does not introduce
  role- or tenant-scoped access restrictions on this record (see Assumptions).
- **FR-012**: The end-to-end conversation record used for diagnosis (FR-011) and the
  record the evaluation suite scores against MUST be the same underlying record, not
  two separately maintained logs that can drift apart.
- **FR-013**: The evaluation suite and merge gate's tracing/export layer and
  gate-computation logic MUST be runnable to a complete verdict with no live
  dependency on any specific cloud provider's proprietary observability or
  evaluation platform. The judge-model LLM call itself (FR-001/002/009/010) is
  exempted from this requirement — consistent with the project's existing
  "everything runs offline except the LLM provider" rule — because scoring
  genuinely requires an LLM inference call; cloud-agnosticism here means the
  judge model's *provider* is swappable via configuration (FR-013a), not that
  no LLM call occurs.
- **FR-013a**: The judge-model client MUST select its LLM provider (endpoint,
  authentication, and the pinned model deployment) entirely via configuration,
  the same way FR-014 requires for the tracing exporter. Azure OpenAI MAY be
  the default/only provider actually exercised in this build, but switching to
  a different OpenAI-compatible provider later MUST be a configuration change,
  not a code change to `eval/judge.py` or any scorer that calls it.
- **FR-014**: The conversation-tracing layer underlying FR-011 MUST be able to send its
  records to a different destination purely through configuration, without changing
  agent or application code.
- **FR-015**: No cloud-provider-specific identifier MUST be fixed in the shared
  conversation-tracing data; any such identifier MUST be derived from configuration
  rather than hardcoded.
- **FR-016**: A cloud platform's optional, proprietary evaluation capabilities (for
  example, a vendor's built-in safety scoring) MAY supplement the consolidated report
  but MUST NOT be required for the merge gate to run or produce a verdict.
- **FR-017**: The system MUST retain past scored reports so that a given run's results
  can be compared against a prior baseline to identify a regression, not just judged in
  isolation.
- **FR-018**: No report or end-to-end conversation record produced by this feature
  MUST expose unmasked guest personal information to the users authorized to view it.
- **FR-019**: The evaluation suite MUST run as three distinct tiers, each producing a
  verdict at a different point in the merge workflow:
  - **PR tier** — runs on every pull request, target under ~10 minutes: all binary
    gates (FR-007), a stratified single-run subset of the retrieval-quality eval set,
    saga-failure permutations scoped only to the domains the change's required
    coverage (FR-009a) actually touches, and a fixed representative cost/latency trace
    set.
  - **Merge-queue tier** — runs before final merge, less frequently than every push:
    the full retrieval-quality eval set at the 3×-repeated, variance-adjusted
    threshold (FR-007a), the complete saga-permutation sweep across all three
    transactional domains, the full multi-turn adversarial script, and the full
    agent-trajectory eval set.
  - **Nightly tier** — non-blocking, report-only: red-team/adversarial-style probing
    and baseline-drift reporting; surfaced in the report but MUST NOT block any merge.
- **FR-020**: The system MUST treat per-turn evaluation latency as an explicit
  optimization target rather than an accepted fixed cost, specifically reducing it
  below the ~90s/turn baseline observed in prior adversarial testing, through
  techniques such as parallelizing independent probes, using fixed-seed cached
  simulator responses, pre-computing retrieval contexts where retrieval itself isn't
  under test, and batching judge-model calls — for both the PR and merge-queue tiers.
- **FR-021**: When a case's required dependency (the judge-model API, or a vendor
  simulator) is unreachable, the system MUST retry that case up to 2 additional times
  with backoff before treating it as unavailable, with the total added retry time
  capped so it cannot cause the PR tier to exceed its time target (FR-019).
- **FR-022**: If the dependency remains unreachable after retries, the affected case
  MUST be marked NOT_EVALUATED and the overall evaluation run's verdict MUST be
  INCOMPLETE. An evaluation run MUST NEVER report a PASS verdict when one or more of
  its required cases did not actually complete, in any environment.
- **FR-023**: Whether an INCOMPLETE verdict blocks the merge MUST be configurable per
  environment: a local/developer run MAY treat INCOMPLETE as a non-blocking warning,
  but the merge-queue tier MUST treat INCOMPLETE as blocking.
- **FR-024**: A simulator response produced by the project's existing fault-injection
  mechanism (a deliberately requested fault, used by saga test cases to simulate a
  failure) MUST be treated as an expected test result, not as a dependency-unavailability
  error — only a simulator that is unreachable (no response at all) counts toward
  FR-021/FR-022.
- **FR-025**: Every Coverage Exemption MUST carry an expiry window sized to the
  urgency tier of the capability it covers (FR-009a): 7 days for Tier 1 (`commit`
  entitlement, saga participant, compensation path); 14 days for Tier 2 (`hold`
  entitlement, tenant-scoped corpus, policy rule); 30 days for any other capability
  subject to a coverage requirement.
- **FR-026**: An exemption MUST clear early, before its expiry window elapses, the
  moment the coverage gate actually passes for the surface it covers. Closing the
  exemption's linked tracking issue MUST NOT by itself clear the exemption — issue
  closure is not evidence that a corresponding test exists.
- **FR-027**: When an exemption's expiry window elapses without the coverage gate
  passing for that surface, the exemption MUST become void immediately: no grace
  period, no automatic renewal. The gate MUST fail closed for that surface from that
  point forward.
- **FR-028**: Renewing coverage for a surface whose exemption has expired or is
  expiring MUST require filing a new exemption with its own fresh justification;
  extending or reactivating the prior exemption record MUST NOT be permitted.
- **FR-029**: A third exemption request against the same coverage surface MUST NOT be
  auto-approved; it MUST require either a recorded architectural decision (ADR)
  justifying continued deferral, or removal of the still-uncovered capability. The
  system MUST NOT allow the same surface to be indefinitely re-exempted.

### Key Entities

- **Evaluation Run**: One execution of a given Gate Tier's suite. Has a timestamp, the
  code version it ran against, the tier it ran as, a Run Verdict, and links to every
  metric score it produced.
- **Run Verdict**: The outcome of one Evaluation Run — PASS, FAIL, or INCOMPLETE (one
  or more required cases could not be evaluated after retries). Only PASS and FAIL are
  ever produced when every case completed; INCOMPLETE is never silently reported as
  PASS, and whether it blocks a merge is environment-configured.
- **Gate Tier**: One of the three scopes an Evaluation Run can execute at — PR (fast,
  every pull request), Merge-Queue (full suite, blocks final merge), or Nightly
  (non-blocking, report-only) — each with its own subset of checks and its own point
  in the merge workflow where it applies.
- **Metric Score**: A single measured value (for example, a context-recall score for
  one answer) tied to the domain, conversation, and step it was measured on, and to the
  judge model that produced it.
- **Saga Failure Case**: A specific, named permutation of a commitment flow failing at
  a given step in a given domain, together with the expected correctly-recovered
  outcome it must be proven against.
- **Conversation Record**: The ordered, end-to-end record of one guest conversation —
  every turn's input, decisions, tool calls, retrieved context, output, cost, and
  latency — shared by the diagnostic view and the evaluation suite.
- **Cost/Latency Budget**: A threshold assigned to a processing step or domain, against
  which an actual conversation's measured cost or latency is compared.
- **Judge Model Record**: The identity and pinned version of the model that produced a
  given Metric Score, carried alongside that score in every report.
- **Baseline**: The most recent main-branch merge-queue run to reach a COMPLETE Run
  Verdict (an INCOMPLETE run is never eligible). Carries its raw per-metric means, its
  per-metric standard deviations (for the FR-007b margin), and the pinned judge model
  that produced it — used as the comparison point for the FR-007 condition (b)
  baseline-delta check. A judge-model mismatch against the current run makes the
  baseline BASELINE_INVALID for that comparison until re-baselined.
- **Coverage Requirement**: An automatically derived obligation that a given capability
  (a `commit`/`hold`-entitled tool, saga participant, compensation path, tenant-scoped
  corpus, or policy rule) must have a corresponding saga-failure or retrieval-quality
  case before a change touching it can merge, classified into Tier 1 or Tier 2 (FR-009a)
  for exemption-window sizing.
- **Coverage Exemption**: An explicit, reason-and-issue-linked, time-boxed bypass of
  one Coverage Requirement — expires void (fail-closed, no grace period) after its
  tier's window unless the coverage gate passes first; individually reviewable,
  counted in every evaluation report, and escalates to requiring an ADR or capability
  removal on a third request against the same surface.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A single command produces one consolidated scored report for whichever
  gate tier it runs, with no manual assembly of separate results; the merge-queue
  tier's report covers all five agent domains and all four evaluation categories
  (retrieval quality, agent trajectory, saga correctness, cost/latency) in full.
- **SC-002**: A deliberately degraded prompt or a knowingly regressed change fails the
  automated merge gate in 100% of repeated runs, before merge — including a case that
  regresses from baseline while staying above its absolute floor.
- **SC-003**: 100% of defined saga failure permutations across the three transactional
  domains are exercised by an automated case that verifies correct recovery, not only
  the success path, and any such case failing blocks 100% of merges (zero-tolerance).
- **SC-004**: 100% of metric values in every produced report are attributable to a
  named, version-pinned judge model within that same report.
- **SC-005**: Any project engineer can retrieve the complete end-to-end record of any
  given guest conversation, in either tenant, (decisions, tool calls, retrieved
  context, output, cost, latency) without needing access to any specific cloud
  vendor's console or tooling.
- **SC-006**: The evaluation suite computes the same scored result (PASS/FAIL/
  INCOMPLETE) for a given change whether run on a developer's own machine or in the CI
  environment, with zero dependency on any specific cloud provider's proprietary
  observability or evaluation platform required to reach that result (the
  judge-model LLM call itself is expected and unavoidable, per FR-001/002) —
  only whether an INCOMPLETE result blocks the merge is intentionally
  environment-specific (FR-023).
- **SC-007**: 100% of evaluated conversations have their per-step cost and latency
  compared against an assigned budget, with every exceedance individually identified
  in the report rather than folded into an aggregate number.
- **SC-008**: A change that introduces or modifies a hold/commit-capable tool, a saga
  participant, a compensation path, a tenant-scoped corpus, or a policy rule, and adds
  no corresponding eval coverage, is blocked from merging in 100% of cases unless an
  explicit, visible exemption is recorded.
- **SC-009**: Every evaluation report lists the full, current set of active coverage
  exemptions — an exemption never merges silently or drops out of visibility over time.
- **SC-010**: The PR gate tier completes and returns a verdict in under ~10 minutes for
  a typical single-domain change.
- **SC-011**: Per-turn evaluation latency measurably decreases from the ~90s/turn
  baseline observed in prior adversarial testing, for both the PR and merge-queue
  tiers, rather than remaining flat as suite coverage grows.
- **SC-012**: In 100% of cases where a required dependency remains unavailable after
  retries, the affected run is reported as INCOMPLETE, never as PASS — and a
  deliberately injected test fault is never mistaken for a dependency failure.
- **SC-013**: 100% of coverage exemptions that reach their expiry window without the
  coverage gate passing become void and fail closed immediately, with no grace period
  or automatic renewal observed in any run.
- **SC-014**: 100% of third-or-later exemption requests against the same coverage
  surface are blocked from auto-approval, requiring either a linked ADR or removal of
  the uncovered capability before the request can proceed.
- **SC-015**: A judge-model upgrade never by itself produces a reported regression —
  100% of such cases are reported as BASELINE_INVALID rather than a false
  baseline-delta failure, while the absolute-floor check continues to apply unaffected.
- **SC-016**: No produced evaluation report or conversation record contains unmasked
  guest personal information, verified by an automated check in 100% of runs.
- **SC-017**: Switching the judge model's LLM provider requires only a configuration
  change — no code change to `eval/judge.py` or any scorer that calls it.

## Assumptions

- Historical scored reports are retained using the project's existing standard data
  retention practices; no separate retention policy is introduced by this feature.
- Cost and latency budget values themselves (the specific numeric thresholds per step)
  are configuration to be defined during planning, not a specification-level decision —
  this spec requires that budgets exist and be enforced, not their exact values.
- "Judge model weaker than the model being judged" is evaluated using the project's
  existing model-capability tiering, not a new ranking scheme introduced by this
  feature.
- A cloud platform's optional evaluation add-ons (e.g., vendor-specific safety
  evaluators) may still be integrated as a supplementary, non-blocking signal in the
  consolidated report — cloud-agnostic operation constrains what the gate *requires*,
  not what it is permitted to additionally surface.
- The gaming domain is read-only and performs no hold/confirm/compensate commitments,
  so it is out of scope for the saga-correctness suite (FR-003) but remains in scope
  for retrieval-quality and trajectory scoring wherever it produces knowledge-grounded
  answers or agent turns.
- Conversation-record access (FR-011) is intentionally left unrestricted for this
  build: ResortFlow's two tenants are synthetic, and no real guest data exists yet. This is a
  deliberate scope decision, not an oversight — access control (tenant-scoped or
  role-gated visibility) should be revisited as its own feature before any deployment
  handling real guest data.
- The "meaningful margin" formula itself is fixed by FR-007b
  (`max(fixed %, 2 × σ_baseline)`); only the fixed-percentage constant within that
  formula and each metric's absolute floor (FR-007) are configuration/threshold
  decisions left to planning, not fixed by this specification.
- The merge-queue tier has no fixed wall-clock ceiling analogous to the PR tier's
  ~10-minute target (FR-019); it is expected to run less frequently than every push and
  to get faster over time via the FR-020 optimizations, but an exact time budget for it
  is a planning-level parameter, not a specification-level decision.
- Azure OpenAI is the only LLM provider actually exercised by the judge model in this
  build (matching the project's existing runtime LLM choice everywhere else) — FR-013a's
  provider-swappability requirement is about the *design* not locking to Azure
  specifically, not about this build actually running against a second provider.
- Because the PR tier does not score agent trajectory (FR-019 — only the merge-queue
  tier runs the full agent-trajectory eval set), validating a tool-selection or
  plan-quality regression (User Story 1, Acceptance Scenario 2) requires running the
  merge-queue tier, not the PR tier.
