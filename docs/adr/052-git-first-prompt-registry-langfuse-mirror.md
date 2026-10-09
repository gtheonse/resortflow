# ADR-052 — Git-first prompt registry, mirrored to Langfuse

**Status**: Accepted — 2026-09-26 (D5 decided as option ii)
**Date**: 2026-09-25
**Feature**: 009-prompt-management
**Supersedes**: `docs/ResortFlow-Observability-Tracing-Deep-Dive.md` C6 (lines ~336–340)
**Extends / amends**: ADR-044 (judge model pinning → judge *prompt* pinning), ADR-045 (adds a second
automation writer for the baseline), ADR-046 (extra workflows), ADR-041/051 (telemetry vocabulary)

## Context

Every LLM prompt in ResortFlow is a Python string literal: the dining intent instructions
(`agents/dining/nodes/intent_parser.py`), and five inline judge templates in
`eval/retrieval_scorer.py` and `eval/trajectory_scorer.py`. That leaves three gaps: a prompt's
version is only "whatever commit was deployed"; no trace records which prompt produced a model
call; and there is no gated promotion or fast rollback independent of a code deploy. Langfuse v3
already runs locally (ADR-051) but only as an OTLP trace sink — the SDK is installed and never
imported.

The forces pull in opposite directions. Deep-Dive C6 says *don't use Langfuse prompt management
at all*, because production has nothing equivalent and prompts should go through code review
anyway. Against that, a registry gives label-based rollback, lineage, and a UI that non-engineers
(culinary, compliance) can author in. And two project rules constrain every option: the
constitution says a deliberately degraded prompt MUST fail CI, and Principle IV says a rule that
can be undone by a prompt edit is not enforced — the dietary vocabulary is compliance policy,
and the workflow is for **two jurisdictions** with different regulators.

Two facts found while planning shaped the design. Protected labels are an Enterprise/Pro feature,
so on self-hosted OSS anyone with member rights can move `production`. And a Langfuse prompt
version exposes no author and no hash, so identity and credit must be computed or supplied
locally.

## Options Considered

**1. Keep C6 — prompts in Git only, add lineage attributes.** Advantage: smallest change;
portable; every prompt change is a reviewed diff; no runtime dependency. Disadvantage: no label
rollback, no registry UI, no way for non-engineers to author or test wording in staging, and the
"trace ↔ prompt version" view is a hash we must build ourselves.

**2. Git-first hybrid: Git canonical, Langfuse a mirror and optional runtime registry, with
runtime enforcement of what may be served.** Advantage: keeps code review and the CI gate as the
only route to production; adds rollback, lineage and a staging authoring loop; production can
stay file-only. Disadvantage: the most machinery — a lock, a revocation list, sync/pull tooling,
owner-group enforcement, three workflows.

**3. Langfuse-first CMS: the UI is canonical, Git holds a snapshot.** Advantage: fastest for
domain teams, "zero PRs". Disadvantage: UI edits bypass the eval gate, which contradicts the
constitution's CI-gate rule and Principle IV; on OSS there is no label protection to compensate;
prompt version stops being tied to graph version.

## Decision

**Adopt Option 2.** Git files are the canonical prompts; Langfuse is the mirror, the lineage
store and the rollback lever; and production serves only content that a build-time lock approves.
Each decision below carries what it supersedes or conflicts with, so a later drift check on this
ADR has something concrete to test.

| # | Decision | Supersedes / conflicts with |
|---|---|---|
| **D1** | Git is canonical; Langfuse mirrors and may serve. **C6's objection is addressed, not eliminated: parity is now enforced by CI hash-equality and a runtime hash allow-list rather than by physical identity of source.** With `PROMPT_SOURCE=langfuse`, production takes on a network dependency and a cache-staleness window (≤ `PROMPT_CACHE_TTL_SECONDS`) that file-based prompts did not have — which is why `git` stays the production default. | Supersedes Deep-Dive C6 |
| **D2** | The runtime serves registry content only if its sha256 is in `prompts/prompts.lock.json` and not on the revocation list (strict mode); otherwise it serves the bundled Git copy. A scheduled drift job **detects** but does not **enforce** — it can be hours late; the runtime check is the guarantee. | Conflicts with "edit in UI → production, zero PRs"; anchored on constitution CI-gate rule and the absence of protected labels on OSS |
| **D3** | Variables the policy depends on are declared **at the call site in code** and injected from the code enumerations. Missing, undeclared or unrendered variables, or disallowed config keys, reject the registry version and fall back to Git, in every mode and for every label. `model_tier` is not a prompt-config key: runtime tier is static per node. | Principle IV; Principle VI |
| **D4** | Judge prompts are pinned by content hash and always resolve from Git. A judge-prompt hash change (or a revoked recorded hash) makes the gate FAIL `BASELINE_STALE` until an automated job commits a regenerated baseline to the same PR. | Extends ADR-044; amends ADR-045/046; leaves `BASELINE_INVALID` semantics unchanged |
| **D5** | Lineage is emitted as `resortflow.prompt.*` OTel attributes **only**. **Decided 2026-09-26 (option ii): no Langfuse prompt-link attributes are emitted, and this ADR makes no claim that the Langfuse UI links a generation to its prompt version.** Langfuse documents `langfuse.observation.prompt.name/version` in its OTel mapping table and the SDK sets them, but the docs do not state that raw-OTel linking renders in the UI, and the link would bind to our wrapper span rather than the instrumented call carrying usage and cost. Accepted cost: prompt identity and token cost sit on different spans and are correlated through the parent span. | Consistent with ADR-041/051; corrects the v2 SDK samples in the initial analysis |
| **D6** | Promotion is by label: `push` → `staging`; `promote` → `production` only after the merge-queue gate passes for that commit; rollback moves the label to an approved, non-revoked version. | Extends ADR-046 |
| **D7** | Canary/shadow routing and the Langfuse A/B comparison workflow are future work. | None |
| **D8** | Approved hashes are permanently revocable by an append-only tombstone list (hash, reason, date, incident/PR). A revoked hash is refused at runtime and by `promote`. CI rejects a shrunk list, and rejects any change leaving a revoked hash in the bundled templates. Revocation propagates by **expedited deploy** (the lock is immutable per image); propagation target 60 minutes (to confirm); revocation is **not instantaneous**. Applies to judge prompts. A hot-path revocation channel was rejected: a second trust source for one feature. | Extends D2 |
| **D9** | **Deferred (2026-09-26):** a registry-to-Git import (`pull`, a three-way compare against a recorded `derived_from`, discard logging, editor co-author credit) and any mode that serves unlocked registry hashes. Until then registry edits are scratch: the application never serves a hash that is not locked, and `push` prints what it supersedes. Domain teams draft; engineering transcribes and lands. | Conflicts with the initial analysis' "domain teams ship without engineering" |
| **D10** | One tenant-agnostic template per prompt. Tenant data enters only through a closed, bounded, typed profile — never free text. One hash and one baseline per family, but the gate reports and applies floors **per tenant**, and two deterministic dining metrics (`intent_extraction_accuracy`, `dietary_recall`; the latter report-only until calibrated) let a degraded dining prompt fail CI. Regulated wording is fixed text appended by code after generation (ADR-034), never requested from a model. **Accepted cost:** no tenant-specific brand voice in prompt text. Base-plus-tenant-partial composition is rejected: it is the route for jurisdiction wording into a prompt. | Principle IV; ADR-034; reverses the initial "base + tenant partial" suggestion |
| **D11** | Approval follows the policy: each template names an owner group; the dietary enumerations are bound by a member snapshot and contract test that require the compliance owner (the whole model module is deliberately not owned); judge prompts name an eval-track owner (not just any engineer), and a rubric that scores dietary/allergen criteria names compliance as its sole owner. Owner groups need ≥ 2 members. CODEOWNERS is generated in last-match-wins order with `/prompts/` defaulting to compliance, so files added later fail safe; it grants approval to any one listed owner (OR), so dual approval on the dietary ground truth would need a CI review check and is future work. The lock cannot gain a hash without its template (invariant L2b). Revocation alone has a break-glass merge path (approval by the incident-responder group or compliance, plus an incident label; the replacement must be an already-approved hash, enforced by CI); promotions and new content never do. **Limits stated honestly:** CODEOWNERS cannot express "engineering now, owner later", so the owner's after-the-fact approval is recorded manually and nothing audits it (an audit job was designed and dropped on 2026-09-26); and in this solo repository the owner groups are documented synthetic groups and enforcement is demonstrable, not activated. | Principle IV; extends D8 |

## Consequences

**Easier.** Any model call can be traced to a prompt name, version and hash; rollback is a label
move within a cache window; non-engineers can author and try wording in staging; production keeps
working with no registry at all; a degraded or changed judge prompt can no longer silently
shift baselines; the dietary vocabulary can no longer be edited out of a prompt.

**Harder.** Two new append-only files and a CLI to maintain; three new workflows and, for a real team,
an org-level setup (teams, an App or PAT, branch protection) the repository cannot create; per-tenant reporting
requires new Track E work and rebalanced datasets (dining is 20 Las Vegas to 5 Boston cases); a
judge-prompt change now costs a full merge-queue-tier evaluation run; registry mode reintroduces a
network dependency and a ≤ 60 s staleness window; revocation takes an expedited deploy, not a
switch.

**Forecloses.** UI-authored production prompts; tenant-specific prompt voice; a runtime
revocation channel; model selection through prompt config; Langfuse folders and prompt
composability. Revisiting any of these needs its own ADR.

**Negative findings worth keeping.** The registry cannot tell us who edited a version or hash its
content; protected labels are unavailable on OSS; and today's eval gate never exercised the
dining prompt at all — the new `intent_extraction_accuracy` / `dietary_recall` metrics exist
because "a degraded prompt fails CI" was, for that prompt, untrue before this feature.
