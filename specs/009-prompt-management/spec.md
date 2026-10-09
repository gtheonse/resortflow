# Feature Specification: Prompt Management — Git-First Prompt Registry with Langfuse Mirror

**Feature Branch**: `009-prompt-management`

**Created**: 2026-09-25

**Status**: Draft

**Input**: User description: "Feature 009 — Prompt management: Git-first prompt registry with Langfuse mirror. Externalise the three hard-coded prompt families (dining_intent_extraction; eval_retrieval_judge incl. its 4 user templates; eval_trajectory_judge incl. its plan_quality template) into Git-canonical templates mirrored to Langfuse via a sync CLI (push/lock/verify/promote). Runtime prompt source git|langfuse (git default in prod). Production may only serve prompt content whose hash is in a build-time allow-list, else falls back to the Git copy. Code-mandated template variables (dietary enums) fail closed at runtime. Judge prompts resolve by exact version/hash; a judge-prompt hash change yields a BASELINE_STALE hard gate failure unless the baseline is regenerated in the same PR. Lineage via resortflow.prompt.* telemetry attributes plus Langfuse prompt-link keys, with a blocking spike verifying Langfuse UI linkage. Label promotion staging→production only after eval gate PASS; rollback by moving the label to an approved hash. Supersedes Observability Deep-Dive C6 via ADR-052. Out of scope: supervisor extractor wiring, canary/shadow traffic, A/B comparison workflow."

## Summary

Every LLM prompt in ResortFlow is currently a string literal inside Python code. That gives three
concrete gaps: (1) a prompt's version is only knowable as "whatever commit was deployed",
(2) no trace records which prompt produced a model call, and (3) there is no gated
promotion or fast rollback path for a prompt change independent of a code redeploy.

This feature moves prompts into version-controlled template files that remain the single
source of truth, mirrors them into the already-running Langfuse instance as a versioned
registry, and makes every model call carry the identity (name, version, content hash, and
source) of the prompt it used. It deliberately does **not** turn Langfuse into an
uncontrolled editing surface for production: production can only ever serve prompt
content that passed the eval gate, enforced at runtime, not just in CI.

The existing decision in `docs/ResortFlow-Observability-Tracing-Deep-Dive.md` C6 ("don't use
Langfuse prompt management at all") is superseded by ADR-052. C6's dev/prod-parity
objection is **addressed, not eliminated**: parity is enforced by hash equality (in CI and
at runtime) rather than by physical identity of source.

**Who does what.** Domain teams (concierge, culinary, legal) can draft prompt wording in
the registry UI (or its playground) without touching Python. Registry edits are scratch: nothing
imports them into Git and the running application never serves them unless their hash is locked. An engineer transcribes an accepted change
into the template file, and it then passes the same hashing, lint, locking, owner-approval and eval
gate as any other change. A registry-to-Git import tool was specified and deferred (ADR-052 D9).

### Scope (exhaustive)

Exactly three prompt families are in scope. No other code path calls a chat model.

| Prompt family | Today | Consumers | Resolution policy |
|---|---|---|---|
| `dining_intent_extraction` | Class-level constant in the dining intent parser | Dining intent-parser node on the standalone dining graph | By environment label, subject to allow-list |
| `eval_retrieval_judge` (4 prompts: `eval_retrieval_judge_context_recall`, `..._context_precision`, `..._faithfulness`, `..._answer_relevance`) | System prompt + 4 inline user templates in the retrieval scorer | Eval runner; UI inspector groundedness panel | Content hash, always resolved from version control |
| `eval_trajectory_judge` (1 prompt: `eval_trajectory_judge_plan_quality`) | System prompt + 1 inline user template in the trajectory scorer | Plan-quality scorer | Content hash, always resolved from version control |

**Out of scope**: the supervisor's hard-wired rule-based extractor (unchanged); rule-based
parsers (no prompts); embeddings; test-probe prompts; canary / shadow-traffic routing;
side-by-side prompt A/B comparison workflow (recorded as future work in ADR-052);
per-tenant prompt variants and base-plus-tenant-partial composition (rejected; see FR-012a);
registry-to-Git import (`pull`), `derived_from` base tracking, discard logging and editor co-author
credit (specified during clarification, deferred 2026-09-26; ADR-052 D9).

## Clarifications

### Session 2026-09-25

- Q: When a judge prompt changes, which mechanism produces the regenerated baseline the PR must commit, given ADR-045 lets only automation write `baseline.json`? → A: A CI job on the PR branch runs the merge-queue-tier evaluation and commits the regenerated baseline back to the same PR branch (automation-only; no local/manual baseline writes). ADR-045 is amended to name this second automation path.
- Q: Should an approved prompt version be permanently revocable, so a rollback or stale label can never serve it again? → A: Yes — the lock holds approved hashes plus an explicit `revoked` tombstone list; revoked hashes are refused at runtime and by promote even though once approved. Additionally: (1) revoking the hash production currently serves must be atomic with promoting a non-revoked replacement, and a revoked hash in the bundled fallback is a build-time failure; (2) revocation propagates via an expedited deploy (lock is immutable per image; no runtime revocation channel), with a stated propagation SLA and no claim of instant effect; (3) tombstones are never deleted (hash, reason, date, incident/PR link), CI rejects any diff that shrinks the revoked list, and the scheduled drift job fails if any registry label — including `staging` — points at a revoked hash; (4) revocation applies equally to judge prompts, and revoking a judge hash invalidates any baseline recorded with it.
- Q: How should a non-engineer's registry-UI wording edit reach the canonical Git copy? → A: One-way authoring with an explicit `pull` import (Option B): domain teams author, engineering lands. Additionally: (1) every pushed version records `derived_from` (Git hash) and pull does a three-way comparison, refusing or marking conflicts when the base is not the current Git head; (2) push lists unpulled registry versions newer than their base and requires an explicit discard option, logging actor and versions; (3) pull and CI reject literal dietary enum values in template text (Principle IV), with reviewed per-token exemptions, and pull rejects any registry-side model-configuration change; (4) staging serves unlocked hashes as `registry-unlocked` while production serves locked hashes only; (5) the pull-generated PR credits the domain editor with a co-author trailer. **[Superseded 2026-09-26: `pull` and its safeguards deferred - see Session 2026-09-26.]**
- Q: Should each prompt be one tenant-agnostic template, or may it have per-tenant variants? → A: One tenant-agnostic template per prompt (Option A); per-tenant variants and base-plus-tenant-partial composition are rejected (a tenant partial is a route for jurisdiction-specific wording, contrary to Principle IV). Additionally: (1) tenant-specific content enters only via a closed, code-owned typed tenant model — display name, venue names, enums — length-bounded and validated, never free text, with a contract test fixing the set of tenant variables per template so adding one needs a code change and review; (2) one hash and one baseline per family, but the gate reports metrics per tenant, stratifies the eval set to cover both tenants, and applies absolute floors per tenant as well as on the average; (3) regulator-dictated wording (responsible-gambling disclosures, age language) is never requested from a model — it is fixed text appended by code after generation, selected by the token-derived tenant (ADR-034); (4) accepted trade-off: no tenant-specific brand voice in prompt text — tone differences must come from bounded variables or from the content RAG grounds on — and revisiting this needs its own ADR.
- Q: Should a prompt change need sign-off from a named owner group beyond engineering review, and for which prompts? → A: Yes (Option B): each template declares an owner group; the dining intent prompt requires its compliance owner group's approval, enforced in CI. Additionally: (1) the approval follows the policy — the dietary restriction/severity enumerations and the contract test binding them to template variables also require the compliance owner's approval; (2) judge prompts require a named eval-track owner group as required approver (not any engineer), and any judge rubric scoring dietary or allergen criteria also requires the compliance owner, since the judge is the gate; (3) revocation has a break-glass path — engineering approval plus an incident label may merge it, the owner group must approve after the fact within a stated window, unapproved break-glass revocations are flagged, and only removal (never promotion) can be expedited; (4) mechanics — the template `owner` field and the approval rules must not drift (rules generated from the owner fields, CI fails on disagreement), owner groups need at least two named members, and "change" means template text, variable list, or model configuration. **[Superseded 2026-09-26 (analysis remediation): the "plus compliance" rubric rule is replaced by FR-036b (compliance as sole owner), and break-glass approval is by the incident-responder group or compliance.]**

### Session 2026-09-26 (planning review)

- B-1 telemetry vocabulary: the five `resortflow.prompt.*` constants are approved now; the two `langfuse.observation.prompt.*` constants are added only if the T001 spike proves the registry link works. **[Superseded 2026-09-26 (task-generation decisions): only the five `resortflow.prompt.*` constants are added.]**
- B-2 enumeration approval: compliance approval attaches to a snapshot of the dietary enumerations plus their contract test (FR-037 reworded); the enumerations are neither moved nor is the whole model module owned.
- B-4 approvers: single-maintainer repository - the ownership mechanism is built with documented synthetic owner groups; enforcement is demonstrable, not activated.
- Scope: User Story 5 (pull, `derived_from`, discard log, editor credit) is deferred entirely; FR-028a-d, f, g and SC-013/SC-014 are withdrawn; registry edits are scratch; staging mode is retained. **[Staging-mode part superseded 2026-09-26 (task-generation decisions).]**
- Planning amendments applied: model tier removed from template config (FR-001); judge prompts resolve from version control by hash (FR-016); revocation atomicity restated as a deterministic invariant (FR-014b); dining intent metrics added so FR-035 holds for the dining prompt (FR-035a); six prompt identifiers (Scope).

### Session 2026-09-26 (task-generation decisions)

- Staging-serving mode dropped: allow-list enforcement is always on and there are five sources (`git`, `langfuse`, `git-fallback`, `git-fallback-unapproved`, `git-fallback-revoked`); `registry-unlocked` is removed. A registry edit is scratch and is never served by the application unless its hash is locked.
- Break-glass audit job dropped: the break-glass *merge path* stays as a documented manual convention with the CI invariant that no new approved hash may be added in the same change; there is no automated audit or flagging job.
- Rebaseline bot uses a fine-grained PAT (`REBASELINE_TOKEN`); no GitHub App.
- ADR-052 D5 is pre-decided as option (ii): lineage is `resortflow.prompt.*` attributes only; no `langfuse.observation.prompt.*` constants, no claim that the registry UI links a generation to a prompt version, no propagation span processor. The blocking spike and User Story 4 are withdrawn and replaced by a non-blocking unit test on the client's tracing-disabled behaviour.

### Session 2026-09-26 (analysis remediation)

- Approval rules are generated **last-match-wins** with `prompts/` defaulting to compliance (FR-042); CODEOWNERS is OR, not AND, so no requirement promises dual approval (future work) and a dietary judge rubric names compliance as its sole owner (FR-036b; the `co_owners` field is removed).
- The lock may gain a hash only together with its template; "base" is the merge-base (FR-013, invariant L2b).
- Judge calls are wrapped in the generation span; the `prompt` argument is a required keyword (FR-029, T050a).
- `dietary_recall` is report-only until calibrated (FR-035a, T053a); rollback takes effect "within the cache window plus one resolve" (FR-027, SC-006).
- Handoff is the Phase 7.6 handoff.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Dining intent prompt is externalised, traceable, and fails closed (Priority: P1)

A developer changes the dining intent extraction wording by editing a template file
rather than Python code. When the dining agent runs, every model call records exactly
which prompt version and content hash it used. If the prompt registry is unreachable, or
the resolved prompt is missing a code-mandated variable (the structured dietary
restriction and severity vocabularies), the agent still works by using the pinned
version-controlled copy — it never sends a prompt with a missing or unrendered variable.

**Why this priority**: This is the only production-path prompt. It carries a
compliance-relevant input (dietary restriction vocabulary) that Principle IV says must
never be undone by a prompt edit. Externalising it safely is the core value of the
feature.

**Independent Test**: Run the dining intent parser in each prompt-source mode (version
control; registry available; registry down; registry returning a locked older template that lacks a
now-required variable) and confirm the extracted intent is correct in every case and the
emitted telemetry names the prompt, version, hash, and actual source used.

**Acceptance Scenarios**:

1. **Given** prompt source is version control, **When** the dining intent parser runs,
   **Then** it uses the template file's content and the model-call span records prompt
   name, version, content hash, and source = `git`.
2. **Given** prompt source is the registry and the registry serves an approved version,
   **When** the parser runs, **Then** it uses the registry content and the span records
   source = `langfuse` with the registry version number.
3. **Given** prompt source is the registry and the registry is unreachable, **When** the
   parser runs, **Then** the turn completes using the version-controlled copy and the
   span records source = `git-fallback`.
4. **Given** the registry serves a **locked** template (for example after rolling back to an
   older approved version) that omits the dietary restriction or severity variable now
   required by the call site, **When** the parser resolves the prompt, **Then** it rejects that
   content, uses the version-controlled copy, records a rejection reason on the span, and logs a
   warning. (An unlocked stripped template is rejected earlier as `unapproved`.)
5. **Given** the version-controlled template itself omits a mandatory variable, **When**
   the automated test suite runs, **Then** it fails (this state cannot ship).
6. **Given** any prompt source, **When** the parser runs, **Then** the set of permitted
   dietary restriction and severity values offered to the model is exactly the set defined
   in code, and model output is still validated in code after the call.

---

### User Story 2 - Judge prompts are pinned and a judge-prompt change is a hard gate (Priority: P2)

An engineer changes a judge prompt. The eval gate refuses to compare new scores against a
baseline produced under the old judge prompt: the PR fails with an explicit
`BASELINE_STALE` verdict until an automated rebaseline job has regenerated the baseline and
committed it to the same PR branch.
Every eval report records the exact judge prompt identity used, alongside the already-
recorded judge model.

**Why this priority**: The eval suite is the merge gate. A silently changed judge prompt
makes baseline comparisons meaningless — an insensitive gate that looks like a passing
gate. This protects the integrity of every other gate decision.

**Independent Test**: Change one character of a judge template, run the PR-tier eval
gate, and confirm it fails with `BASELINE_STALE`; let the rebaseline job run and commit to
the PR branch, re-run the gate, and confirm the stale reason clears.

**Acceptance Scenarios**:

1. **Given** judge templates unchanged since the baseline was recorded, **When** the PR
   gate runs, **Then** no `BASELINE_STALE` reason is raised.
2. **Given** a judge template whose content hash differs from the one recorded in the
   baseline, **When** the PR gate runs, **Then** the verdict is FAIL with reason
   `BASELINE_STALE`, naming the affected prompt(s), and the process exits non-zero.
3. **Given** a stale baseline, **When** the rebaseline job runs on the PR branch and
   commits the regenerated baseline to that same branch, **Then** the next gate run no
   longer reports `BASELINE_STALE`.
4. **Given** any eval run, **When** its report is written, **Then** each judge-scored
   metric records the judge prompt name, version, and content hash in addition to the
   judge provider and model.
5. **Given** a judge prompt is resolved, **When** a registry label for that prompt has
   been moved, **Then** the judge still uses the exact pinned version — labels never
   affect judge prompt resolution.

---

### User Story 3 - Sync, gated promotion, rollback, and runtime allow-list (Priority: P3)

> **Descoped by decision (2026-09-27), after the US1+US2 checkpoint — not implemented.**
> Production runs `PROMPT_SOURCE=git` regardless of this story, so US1+US2 deliver a fully
> functional feature without it. See the Phase 7.6 handoff for the rationale and exactly
> what was and wasn't built; `tasks.md` T063–T082b remain unchecked and ready to execute as
> written if this story is picked up later.

A maintainer merges a prompt change. The change is mirrored to the registry as a new
version labelled `staging`; the approved content hash is added to a build-time allow-list
shipped with the application; after the eval gate passes, the `production` label is
promoted. If a problem appears, the maintainer re-points `production` to an earlier
approved version and it takes effect within the cache window. If anyone edits the
registry's production content outside this path, the running application refuses to
serve it and a scheduled drift check raises an alert.

**Why this priority**: This delivers the operational benefits (versioned registry, fast
rollback, drift detection) on top of Stories 1–2. It is valuable but depends on the
externalisation in Story 1 existing first.

**Independent Test**: Run sync against the local registry twice and confirm the second
run creates no new versions; promote; move the label back and confirm the older approved
version is served after the cache window; edit production content directly in the
registry UI and confirm the application serves the version-controlled copy instead and
the drift check exits non-zero.

**Acceptance Scenarios**:

1. **Given** template files, **When** sync push runs, **Then** each prompt whose content
   hash differs from the registry's latest version is created as a new version labelled
   `staging`; unchanged prompts create no new version.
2. **Given** template files, **When** the lock step runs, **Then** the allow-list contains
   the current hash of every prompt plus every previously approved hash (it accumulates;
   it never drops approved history), and the revoked tombstone list is preserved intact.
3. **Given** the allow-list is out of date with the templates, **When** the PR gate runs,
   **Then** it fails.
4. **Given** a merge-queue eval run with verdict PASS, **When** promotion runs, **Then** the
   `production` label is moved to the version currently labelled `staging`; on any other
   verdict, promotion does not run.
5. **Given** the registry's labelled content hashes to a
   value not in the allow-list, **When** a prompt is resolved at runtime, **Then** the
   version-controlled copy is served, the span records source = `git-fallback-unapproved`,
   and a warning is logged.
6. **Given** the `production` label is moved to an earlier approved, non-revoked version,
   **When** the cache window elapses, **Then** that earlier version is served without any
   redeploy.
7. **Given** registry production content is not in the approved set or is revoked, **When**
   the verify step runs (PR gate or scheduled job), **Then** it exits non-zero naming the
   offending prompt(s). The scheduled job alerts only; the runtime allow-list is the
   enforcement guarantee. (A production hash that is approved but differs from version
   control is reported as a "rollback in effect" notice, not a failure.)
8. **Given** a hash on the revoked list, **When** the registry serves it under any label
   to an instance built with that lock, **Then** the instance serves the version-controlled
   copy with source `git-fallback-revoked` and logs a warning.
9. **Given** a change under which a revoked hash equals the hash of a version-controlled or
   pinned-judge template, **When** CI runs, **Then** it fails and the change cannot merge.
   **Given** the registry's `production` label points at a revoked hash, **When** the verify
   step runs, **Then** it exits non-zero and reports the promotion of a replacement as pending.
10. **Given** a diff that removes or shrinks the revoked list, **When** CI runs, **Then** it
    fails; **Given** any registry label, including `staging`, points at a revoked hash,
    **When** the scheduled drift job runs, **Then** it fails and alerts.
11. **Given** a judge prompt hash is revoked, **When** the eval gate runs against a baseline
    recorded with that hash, **Then** the verdict is FAIL with `BASELINE_STALE` until the
    rebaseline job replaces the baseline.

---

### Edge Cases

- The dietary vocabularies are ordinary words (`egg`, `soy`, `fish`, `dairy`, `gluten`, and
  severities such as `preference` and `intolerance`), so the literal-value check will
  collide with everyday wording (for example "preference"). Exemptions MUST be explicit,
  reviewed, recorded with a reason, and scoped per token and per prompt; the check itself
  is derived from the code-defined enumerations so it cannot drift from them.
- A registry edit made in the UI (any label) is scratch: the next push supersedes it on `staging`,
  push prints what it supersedes (FR-022a), nothing imports it into Git, and the application never
  serves it (an unlocked hash resolves to the version-controlled copy). An engineer transcribes an
  accepted change into the template, which then passes the normal gate.
- Registry reachable but slow: resolution must not stall a turn beyond the configured
  fetch timeout; on timeout it falls back to the version-controlled copy.
- Registry returns a prompt of the wrong shape (e.g. text instead of chat messages, or a
  malformed config): treated as a rejection → version-controlled copy.
- Template *references* a placeholder for which no value was supplied: rejected → fallback.
- Guest or retrieved text that itself contains `{{…}}` is a value, not a template: it is rendered
  literally, never re-expanded, and never causes a rejection or an error.
- A caller passes a value for a code-mandated variable: the code-injected value always
  wins; callers cannot override the dietary vocabularies.
- Registry credentials or host absent (the client's default cloud host is never used): registry mode behaves as version-control mode with source
  = `git-fallback`; no exception reaches the guest.
- Rollback target is an old version whose hash is not in the allow-list, or is revoked: it
  is refused at runtime (fallback served); rollback is only possible to approved,
  non-revoked versions.
- The revoked hash is the one currently in production and the bundled fallback is the same
  hash: the change is rejected in CI (no servable prompt would remain); it can only land
  together with a non-revoked replacement.
- A revoked hash is still being served by running instances before the expedited deploy
  completes: expected and documented; the propagation target bounds the window.
- First-ever baseline (current baseline is empty): the first rebaseline records judge
  prompt hashes; no `BASELINE_STALE` can be raised against an empty baseline, but the gate
  must report the baseline as absent rather than silently passing the stale check.
- `BASELINE_STALE` (judge prompt hash mismatch) is a new, separate hard-fail reason. The
  existing `BASELINE_INVALID` behaviour for a judge *model* mismatch (delta check
  suspended, absolute floors still applied) is unchanged by this feature.
- The rebaseline job's commit touches only the baseline file; it MUST NOT itself retrigger
  an endless rebaseline loop, and a failed or non-COMPLETE evaluation run MUST NOT commit
  a baseline (existing eligibility rule, ADR-045).
- Sync run twice concurrently: idempotent by content hash; no duplicate versions.
- Telemetry exporter down: prompt resolution and the turn are unaffected.
- Prompt content must never carry guest PII; guest text is supplied only as a runtime
  variable and is subject to the existing redaction on trace write paths.
- Tenant identity is never a template-selectable input derived from model output; tenant
  data reaches a template only through the closed typed tenant model of FR-012b, injected
  server-side from the token-derived tenant.
- The current dining eval set is skewed (20 Las Vegas cases, 5 Boston) and the retrieval-
  judge cases carry no tenant tag at all; per-tenant floors on a thin stratum are noisy, so
  an under-sized tenant stratum is a coverage failure, never a silent pass (FR-021c).
- An owner group's members are all unavailable during an allergen incident: revocation
  proceeds through break-glass (FR-040); nothing else may bypass the owner requirement.
- A break-glass revocation targets the live hash but no already-approved replacement
  exists: it cannot use break-glass and follows the normal path, since new content always
  needs prior approval.

## Requirements *(mandatory)*

### Functional Requirements

**Canonical storage & resolution**

- **FR-001**: Each in-scope prompt family MUST be defined in a version-controlled template
  file containing: prompt identifier, chat messages with named placeholders, model-call
  configuration (temperature and response format only - never a model or model tier, which are
  declared in code per node), resolution policy
  (label-based or exact), and owner group (see FR-036).
- **FR-002**: Version-controlled template files MUST be the single canonical source; the
  registry is a mirror. No prompt text for in-scope families may remain as a string
  literal in application code.
- **FR-003**: The system MUST support a prompt-source setting with values `git` and
  `langfuse`; the default when unset MUST be `git`.
- **FR-004**: Each resolved prompt MUST expose: identifier, version, a content hash
  computed over a canonical serialisation (messages + config), and the actual source used
  (`git`, `langfuse`, `git-fallback`, `git-fallback-unapproved`, `git-fallback-revoked`).
- **FR-005**: The content hash MUST be stable: identical content yields an identical hash
  regardless of source, key ordering, or line endings.
- **FR-006**: Registry fetches MUST be cached in-process for a configurable time window,
  and MUST fall back to the version-controlled copy on any fetch error, timeout, or
  malformed response. Caching is provided by the registry client's own cache
  (stale-while-revalidate: the first resolve after expiry may still return the previous version
  while it refreshes); the project adds no second cache.

**Fail-closed variables (Principle IV)**

- **FR-007**: Mandatory template variables MUST be declared by the calling code, not by
  template or registry configuration.
- **FR-008**: The dining intent prompt MUST declare the dietary restriction vocabulary and
  dietary severity vocabulary as mandatory variables, with values injected from the code-
  defined enumerations.
- **FR-009**: On every resolution from any source and any label, if a mandatory variable
  is absent from the template, or the template references a placeholder for which no value was supplied, the
  system MUST reject that content, serve the version-controlled copy, record a rejection
  reason on telemetry, and log a warning.
- **FR-009a**: The same fail-closed handling MUST apply to a template that references a
  variable the call site did not declare, and to prompt configuration containing any key other
  than `temperature` and `response_format`.
- **FR-009b**: Substitution MUST be a single pass over the template's own placeholders. Runtime
  values (guest text, retrieved context, judge inputs) are inserted verbatim, are never rescanned
  or re-expanded, and MAY contain `{{…}}`; a placeholder counts as unrendered only if the template
  references it and no value was supplied, checked on the template's placeholder set before
  substitution.
- **FR-010**: If the version-controlled copy itself fails the mandatory-variable check,
  resolution MUST raise, and an automated test MUST prevent that state from shipping.
- **FR-011**: Code-injected variable values MUST take precedence over caller-supplied
  values of the same name.
- **FR-012**: Model output validation for the dining intent MUST remain in code and
  unchanged in strictness.

**Tenant-agnostic templates (Principle IV; two jurisdictions)**

- **FR-012a**: Every prompt MUST be a single tenant-agnostic template. Per-tenant template
  variants, base-plus-tenant-partial composition, and server-side selection of a template by
  tenant are out of scope; adding any of them requires its own ADR.
- **FR-012b**: Tenant-specific content MUST enter a template only through a closed,
  code-owned typed tenant model (display name, venue names, enumerations), populated
  server-side from the token-derived tenant. Its values MUST be length-bounded and
  validated. It MUST NOT carry free-form paragraphs, and no such variable may be sourced
  from registry or CMS free text.
- **FR-012c**: A contract test MUST assert the complete set of tenant variables each
  template uses, so that adding a tenant variable requires a code change and review.
- **FR-012d**: Template text MUST NOT contain tenant identifiers or jurisdiction or
  regulator names; this is enforced by the same lint as FR-028e.
- **FR-012e**: No in-scope prompt MAY be asked to generate regulator-dictated wording
  (responsible-gambling disclosures, age language, jurisdiction statements). Such wording
  MUST be fixed text appended by code after generation, selected by the token-derived
  tenant (consistent with ADR-034). Today no in-scope prompt produces guest-facing text;
  this requirement guards prompts added later.

**Runtime allow-list (production enforcement)**

- **FR-013**: A lock step MUST produce a lock artifact mapping each prompt identifier to
  (a) the set of approved content hashes and (b) a list of revoked hashes (tombstones); it
  MUST accumulate previously approved hashes and MUST be shipped with the application
  build, including the container image. The lock MUST NOT gain a hash that is neither already approved on the base branch nor
  the hash of a template (or pinned judge template) in the same change; "base" is the merge-base
  with the target branch (identical in pull requests and the merge queue), and with no base lock
  exactly the head template hashes are allowed.
- **FR-013a**: Each revocation entry MUST record the hash, prompt identifier, reason, date,
  and a link to the incident or PR. Entries MUST never be deleted; CI MUST reject any
  change that removes an entry or shrinks the revoked list.
- **FR-014**: Content resolved from the registry whose hash is not in the approved set MUST
  NOT be served; the version-controlled copy MUST be served
  with source `git-fallback-unapproved` and a warning logged.
- **FR-014a**: A revoked hash MUST be refused at runtime even if it is also in the
  approved set, whatever label or source it arrived by; the version-controlled copy MUST
  be served with source `git-fallback-revoked` and a warning logged. `promote` MUST refuse
  to promote a revoked hash.
- **FR-014b**: A revoked hash MUST never leave the system without a servable prompt. CI MUST
  fail any change under which a revoked hash equals the hash of a version-controlled template
  (the bundled fallback) or of a pinned judge template; this check is deterministic and needs no
  registry access. When the registry's `production` label points at a revoked hash the drift
  check MUST fail and report the promotion of a replacement as pending. Revocation is therefore atomic with its replacement because the
  replacement is the version-controlled template itself.
- **FR-014c**: Revocation takes effect by an expedited deploy of a new build carrying the
  updated lock; the lock is immutable per build and there is no runtime channel for the
  revoked list. ADR-052 and the operational runbook MUST state a propagation target and
  MUST state plainly that revocation is not instantaneous: until the expedited deploy
  completes, running instances may continue to serve the revoked version from cache or from
  the bundled copy.
- **FR-015**: Allow-list enforcement MUST always be on; there is no relaxed or staging-serving
  mode. A registry version whose hash is not locked is never served by the application,
  whatever its label. Anyone wanting to run unlocked wording locally uses the
  version-controlled source.
*FR-015a was folded into FR-009 and FR-014a on 2026-09-26.*

**Judge prompt pinning & baseline integrity**

- **FR-016**: Judge prompts are pinned by content hash and MUST always be resolved from
  version control, so that neither a registry label move nor a registry outage can change the
  judge prompt used. Registry version numbers are informational only (used for lineage when a
  registry is in use) because they differ between registries.
- **FR-016a**: Revocation (FR-013a–FR-014c) MUST apply to judge prompts exactly as to any
  other prompt. Revoking a judge prompt hash MUST invalidate any baseline recorded with it:
  the gate MUST report `BASELINE_STALE` for that baseline until the rebaseline job
  (FR-020) replaces it.
- **FR-017**: Every judge-scored metric in an eval report MUST record judge prompt
  identifier, version, and content hash alongside the existing judge provider and model.
- **FR-018**: The eval baseline MUST record the judge prompt content hashes it was
  produced under.
- **FR-019**: The eval gate MUST emit verdict FAIL with reason `BASELINE_STALE` (non-zero
  exit) when the hash of any judge prompt used by the run differs from the hash recorded in the
  baseline (prompts no metric in the run used are ignored). This is a blocking failure, not a warning.
- **FR-020**: A CI rebaseline job MUST, on a PR branch whose judge prompt hashes differ from
  the baseline's, run the merge-queue-tier evaluation and, only if it reaches a COMPLETE
  verdict, commit the regenerated baseline to that same PR branch. This automated commit is
  the only path that clears `BASELINE_STALE`; no manual or local baseline write is
  supported. ADR-045 MUST be amended to record this second automation path alongside the
  merge-queue write on `main`.
- **FR-021**: When no baseline exists, the gate MUST report the baseline as absent
  explicitly rather than treating the stale check as passed.
- **FR-021a**: The eval set behind every statistical metric that a change to an in-scope
  prompt can affect MUST be stratified so both tenants are covered; cases that today carry
  no tenant tag MUST be tagged.
- **FR-021b**: The gate MUST report those metrics per tenant, and MUST apply the absolute
  floors and the baseline-delta check per tenant as well as on the aggregate, so a wording
  change that regresses one tenant fails the gate even when the aggregate passes. The
  baseline remains one artifact per family, holding per-tenant statistics.
- **FR-021c**: A tenant stratum with fewer than a minimum number of cases (set at planning)
  MUST be reported as under-covered and MUST fail through the existing coverage-failure
  verdict rather than pass.

**Sync, promotion, drift, rollback**

- **FR-022**: A sync push command MUST create a new registry version labelled `staging`
  for each prompt whose content hash differs from the registry's latest version, and
  MUST be idempotent.
- **FR-022a**: When push moves the `staging` label off a registry version that push did not
  create (for example one authored in the registry UI), it MUST print the affected prompt and
  version numbers. Such versions are scratch: nothing imports them into Git.
- **FR-023**: A verify command MUST exit non-zero if any prompt's registry `production`
  content hash is not in the approved set or is revoked, naming each offending prompt. A
  `production` hash that is approved and not revoked but differs from the current
  version-controlled hash (a deliberate rollback) MUST NOT fail verify; it MUST be reported
  as a "rollback in effect" notice so it cannot linger unnoticed.
- **FR-024**: The PR gate MUST run verify and MUST fail if the allow-list is out of date
  with the templates.
- **FR-025**: A promote command MUST move the `production` label to the current
  `staging` version, and MUST run only after a merge-queue eval verdict of PASS.
- **FR-026**: When a shared registry is configured, a scheduled drift job MUST run verify independently of
  PR activity and alert on failure (otherwise it skips with a notice); the specification and ADR MUST state that this job detects but does
  not enforce. It MUST also fail if any registry label on any prompt — including `staging`
  — points at a revoked hash.
- **FR-027**: Rollback MUST be possible by moving the `production` label to any earlier
  version whose hash is approved and not revoked, taking effect within the cache window plus one resolve (the client serves the previous
  version once while it refreshes) without redeploy.
- **FR-028**: Local stack seeding MUST sync all prompt families to the local registry.

**Template lint (CI)**

- **FR-028e**: CI MUST reject template text containing literal values of the dietary
  restriction or severity enumerations. The value list MUST be derived from the code-defined
  enumerations at check time. Collisions with ordinary words are handled only by explicit,
  reviewed, per-token per-prompt exemptions recorded with a reason.

*FR-028a-d, FR-028f and FR-028g (registry-to-Git pull, `derived_from`, discard log, editor
credit) were withdrawn on 2026-09-26 with the deferral of the pull feature.*

**Telemetry & lineage**

- **FR-029**: Every in-scope model call (including the eval judge calls) MUST be wrapped in a generation span carrying
  project-namespaced prompt attributes (name, version, content hash, source, and
  rejection reason when applicable), emitted via the shared telemetry module, with
  attribute names defined only in that module.
- **FR-030**: Lineage MUST be emitted as `resortflow.prompt.*` attributes only. The system MUST NOT
  emit Langfuse's prompt-link attributes and MAKES NO CLAIM that the registry UI links a
  generation to its prompt version (decided 2026-09-26; ADR-052 D5, option ii).
- **FR-031**: A non-blocking automated test MUST show that constructing the registry client
  with tracing disabled does not replace the global tracer provider and does not attach a span
  processor, so the project's own exporter remains the only exporter.

**Governance & documentation**

- **FR-032**: ADR-052 MUST record decisions D1–D11 (D8 = revocation tombstones and
  expedited-deploy propagation, with the hot-path revoked-list channel recorded as a
  rejected alternative; D5 = `resortflow.prompt.*` lineage only (no registry-UI linkage claim); D9 = the registry-to-Git
  pull import, `derived_from` tracking and any unlocked-staging serving mode recorded as
  deferred future work; D10 = tenant-agnostic templates with typed
  tenant variables, per-tenant gate reporting, code-appended regulated wording, and the
  accepted loss of tenant-specific brand voice, with base-plus-partial composition recorded
  as a rejected alternative; D11 = owner-group approval that follows the policy into the
  dietary enumerations and judge prompts, with the revocation-only break-glass path) each
  with a supersedes/conflicts-with
  pointer, the Options Considered, and Consequences, and
  MUST contain the sentence: "C6's objection is addressed, not eliminated: parity is now
  enforced by CI hash-equality and a runtime hash allow-list rather than by physical
  identity of source."
- **FR-033**: Observability Deep-Dive C6 MUST be amended in place to point to ADR-052.
- **FR-034**: The registry client library version MUST be pinned to a compatible major
  version range in every place it is installed (the project dependencies and the container image).
- **FR-035**: A deliberately degraded in-scope prompt MUST cause the eval gate to fail
  (constitution CI-gate rule).
- **FR-035a**: The dining intent prompt MUST be exercised by the eval gate: deterministic
  metrics `intent_extraction_accuracy` and `dietary_recall` (no model judge) MUST be computed
  over a labelled, tenant-stratified case set, with `dietary_recall` held to a floor of 1.0
  (every restriction stated must appear in the structured payload) once its run-to-run
  stability has been measured (calibration); until then it is reported but non-blocking. Without this FR-035 would
  not hold for the dining prompt, which the current gate never exercises.

**Ownership & approval (Principle IV: approval follows the policy)**

- **FR-036**: Each template MUST declare an owner group. The approval rules covering template
  text, variable list and model-call configuration, and the compliance-relevant files of FR-042,
  MUST be generated from those declarations (FR-038), so that when branch protection requires
  Code Owner review a change cannot merge without an owner's approval. In this solo repository
  the requirement is met by declaration, generation and CI verification of coverage; switching
  on the review requirement is an organisation setting outside the repository.
- **FR-036a**: The `dining_intent_extraction` template's owner group MUST be the compliance
  owner group.
- **FR-036b**: Judge templates MUST name the eval-track owner group as their owner (not merely
  any engineer). A judge rubric that scores dietary or allergen criteria MUST instead name the
  compliance owner group as its owner, because CODEOWNERS grants approval to any one listed owner
  and so cannot express approval by both groups.
- **FR-037**: The dietary restriction and severity enumerations MUST be bound by a snapshot of
  their members checked by a contract test. The snapshot file, the tests that enforce it, and
  the lint with its exemptions MUST fall under the compliance owner rule of FR-042. A change to
  an enumeration that does not update the snapshot MUST fail CI (the snapshot test runs in the PR gate and the merge-queue gate), so a restriction cannot be
  added, removed or re-ranked on engineering review alone. Ownership of the whole model module
  that defines the enumerations is deliberately not used: it would pull unrelated model changes
  through compliance review.
- **FR-038**: The template `owner` field and the approval rules MUST NOT be able to drift:
  the rules covering prompt templates MUST be generated from the owner fields, and CI MUST
  fail if the two disagree.
- **FR-039**: Every owner group MUST have at least two named members; CI MUST fail when a
  group referenced by a template has fewer.
- **FR-040**: Revocation (only) MAY use a break-glass merge path: it may merge on approval by the incident-responder owner group (either that group or
  compliance may approve `prompts/revocations.json`) plus an incident label, without prior
  compliance approval, provided the replacement
  required by FR-014b is an already-approved, non-revoked hash (a rollback), never new
  content; CI MUST enforce that no approved hash is added in the same change. The owner
  group's after-the-fact approval is recorded manually in the tombstone's `owner_ack` and is a
  documented convention: there is no automated audit or flagging job (dropped 2026-09-26).
  Promotions and any newly approved content always require prior owner approval.
- **FR-041**: The rule for `eval/data/baseline.json` MUST be generated so that, when Code Owner
  review is required, the automated baseline commit of FR-020 needs the eval-track owner's
  re-approval.
- **FR-042**: The generated approval rules MUST use **last-match-wins** ordering so that the
  default fails safe: `prompts/` defaults to the compliance owner group (including files added
  later); `prompts/templates/eval_*.yaml` and `eval/data/baseline.json` go to the eval-track
  owner group; `agents/dining/tests/test_dietary_*.py` goes to compliance;
  `prompts/revocations.json` lists compliance and the incident-responder group, either of which
  may approve (this is what makes the break-glass merge path expressible; the `incident` PR
  label is a separate CI marker); `eval/data/intent_cases.yaml` (dietary ground truth) lists
  compliance and eval-track, either of which may approve. CODEOWNERS grants approval to any one
  listed owner, so dual (AND) approval on the dietary ground truth would need a CI review check
  and is recorded as future work, not a requirement.

### Key Entities

- **Prompt Template**: Canonical, version-controlled definition of one prompt family —
  identifier, ordered chat messages with named placeholders, model-call configuration,
  resolution policy, owning track.
- **Resolved Prompt**: The runtime result of resolving a template — identifier, version,
  content hash, source, messages, configuration, and optional rejection reason.
- **Prompt Allow-list (lock)**: Build-time artifact mapping each prompt identifier to the
  accumulated set of approved content hashes and a list of revocation tombstones.
- **Revocation Tombstone**: A never-deleted lock entry — hash, prompt identifier, reason,
  date, and link to the incident or PR — that permanently forbids serving that hash.
- **Tenant Profile**: The closed, code-owned, typed set of tenant-specific values (display
  name, venue names, enumerations) a template may reference; bounded, validated, and
  populated server-side from the token-derived tenant.
- **Registry Prompt Version**: A numbered version of a prompt in the registry, carrying
  zero or more labels (`staging`, `production`).
- **Judge Prompt Record**: The prompt identity (identifier, version, hash) attached to
  each judge-scored eval metric, extending the existing judge model record.
- **Eval Baseline**: Existing versioned baseline, extended with the judge prompt hashes it
  was produced under.
- **Owner Group**: A named group of at least two people accountable for a template (and,
  for compliance-relevant policy, for the dietary enumerations); its approval is required
  for any change, except that revocations may be approved after the fact under break-glass.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 0 prompt text literals for the three in-scope families remain in application
  code (verified by search).
- **SC-002**: 100% of in-scope model calls in a test run carry prompt name, version,
  content hash and source in their telemetry.
- **SC-003**: With the registry stopped, 100% of dining intent turns in the dining eval
  suite complete with results identical to version-control mode.
- **SC-004**: 100% of injected fault cases (missing mandatory variable, unrendered
  placeholder, unapproved hash, malformed response) result in the version-controlled copy
  being served; 0 reach the model with the faulty content. Guest or retrieved text containing
  `{{…}}` causes 0 fallbacks and 0 errors.
- **SC-005**: A one-character judge prompt change causes the PR gate to fail in 100% of
  runs until the rebaseline job has committed a regenerated baseline to the same PR branch.
- **SC-006**: A production rollback to a previously approved version takes effect within
  the configured cache window plus one resolve (default ≤ 60 seconds plus one call) with no redeploy.
- **SC-007**: Re-running sync with unchanged templates creates 0 new registry versions.
- **SC-008**: An out-of-band edit to registry production content is refused at runtime on
  the first resolution after the cache window, and detected by the scheduled drift job
  within one schedule interval.
- **SC-009**: Resolution adds no more than 5 ms median overhead per model call on a cache
  hit.
- **SC-010**: 0 revoked hashes are ever served by an instance built after the revocation
  merged; 100% of injected cases (revoked hash arriving via any label, or present in the
  bundled copy) end in fallback or a failed build, never a served revoked prompt.
- **SC-011**: 100% of changes that would leave no servable prompt (revoking the live hash
  without a replacement) or that shrink the revoked list are rejected in CI.
- **SC-012**: After a revocation merges, every serving instance refuses the revoked hash
  within the stated propagation target (see Assumptions), and the runbook states that
  revocation is not instantaneous.
- **SC-015**: 100% of templates containing a literal dietary restriction or severity value
  (without a reviewed exemption) are rejected by CI.
*SC-013 and SC-014 (pull-related) were withdrawn on 2026-09-26.*
- **SC-020**: A deliberately degraded dining intent prompt fails the eval gate in 100% of runs
  on tiers where model credentials are configured (once `dietary_recall` is calibrated to
  blocking); without credentials the intent metrics are INCOMPLETE (blocking at the merge-queue
  tier), and the golden test and the lock check still fail any change to the dining template in
  every tier. A prompt that drops one stated restriction yields `dietary_recall` below 1.0.
- **SC-016**: In test cases where a wording change lowers one tenant's metric below its
  floor while the aggregate stays above it, 100% fail the gate; 0 pass on the aggregate
  alone.
- **SC-017**: 0 templates contain tenant identifiers, jurisdiction names, or free-text
  tenant variables; 100% of templates' tenant-variable sets match their contract test.
- **SC-018**: 100% of files under `prompts/` (including files added later) resolve to an owning
  group in the generated rules; 100% of owner-field/rule disagreements, out-of-order rule blocks
  and under-sized owner groups fail CI. (Blocking a merge that lacks approval is an organisation
  setting: demonstrable, not activated, in this repository.)
- **SC-019**: 0 promotions or newly approved contents merge via the break-glass path (CI
  invariant: no approved hash added in a break-glass change).

## Assumptions

- The local registry (Langfuse v3 in docker-compose, headless-initialised project) is the
  registry of record for local development; production defaults to version-control mode
  and may opt into the registry only if a registry is deployed there.
- The cache window defaults to 60 seconds; fetch timeout defaults to 2 seconds.
- Revocation propagation target: within 60 minutes of the revocation merging, via an
  expedited deploy. This figure is a placeholder default to be confirmed at planning; the
  requirement is that a target exists, is written in ADR-052 and the runbook, and is not
  presented as instant.
- Allow-list enforcement is always on (dropped 2026-09-26: no staging-serving mode). A registry
  edit is scratch: the application never serves it unless its hash is locked.
- The four retrieval-judge user templates and the plan-quality template are modelled as
  separate prompt entries within their families (or messages within one family); either
  shape satisfies scope, and the plan chooses.
- Placeholder syntax is `{{name}}` (the registry's native syntax), replacing Python
  f-string interpolation.
- The supervisor continues to use the rule-based extractor; the model-based dining path is
  exercised via the standalone dining graph and its eval suite.
- Existing ADRs 041 (shared telemetry module), 044 (judge model pinning), 045 (eval state
  as repo files), 046 (gate tiers as workflows) and 051 (Langfuse OTLP exporter wiring)
  remain in force; this feature extends 044/046 and amends 045 by adding an automated
  in-PR rebaseline job for judge prompt changes (baseline writes stay automation-only).
  Whether that job is triggered automatically or manually is a planning decision.
- Accepted trade-off: tenant-specific brand voice cannot live in prompt text. Tone
  differences between the two brands must come from bounded tenant variables or from the
  content retrieval grounds on. Revisiting this needs its own ADR.
- The eval models and gate currently have no tenant dimension and the retrieval cases carry
  no tenant tag; adding both (Track E) is part of this feature, including rebalancing the
  dining set beyond 20/5 where needed for a meaningful Boston stratum.
- Owner group names and membership are organisational inputs (a compliance owner group and an
  eval-track owner group). This is a single-maintainer repository: the groups are documented
  synthetic groups in `owners.yaml`, the two-member rule is checked against that file, and
  CODEOWNERS is generated, but requiring Code Owner review cannot be satisfied by a single
  author, so enforcement here is by CI checks and is demonstrable rather than activated;
  ADR-052 says so.
- Compliance approval on the dietary enumerations attaches to a snapshot file plus its contract
  test rather than to the whole model module. Moving the enumerations would be a contract change
  to a frozen area (checkpoint-serialization compatibility unverified) and is not proposed.
- Registry-to-Git import (`pull`), `derived_from` base tracking, the discard log and editor
  co-author credit were specified during clarification and deferred on 2026-09-26; ADR-052 D9
  records them as future work. There is no in-app path to try registry-authored wording before
  it is locked; the registry's own playground can be used to draft it.
- US2 and US3 build on the registry, lock and adapter delivered by US1 (see tasks.md
  dependencies): the stories are independently testable but not independently buildable.
- `eval_trajectory_judge_plan_quality` is not executed by any gate tier today
  (`score_plan_quality` has no runner call site). It is still externalised, hashed and locked, but
  baseline staleness compares only the prompt ids used by the run, so changing it forces no
  rebaseline until a metric uses it.
- Directory ownership: a new cross-cutting `prompts/` area is added to the track table;
  the telemetry attribute additions go through the observability integration-commit rule:
  only the five `resortflow.prompt.*` constants are added; no `langfuse.observation.prompt.*`
  constants are added (ADR-052 D5, option ii).
