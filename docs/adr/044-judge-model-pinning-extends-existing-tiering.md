# ADR-044 — Judge model pinning extends the existing tiering scheme, behind a provider-agnostic client

**Status**: Accepted
**Date**: 2026-09-13 (amended 2026-09-14, `/speckit-analyze` finding I1)
**Feature**: 007-eval-observability-gate

## Context

FR-009 requires every metric to name a specific, version-pinned judge model
(never a `latest`/floating alias); FR-010 requires that judge model to never be
weaker than the model it judges. Research found no runtime model-tier config
module exists anywhere in the codebase — model selection today is plain
environment variables read directly by node code (e.g. `MODEL_CHEAP`,
defaulting to `gpt-4.1-mini`, in `agents/dining/nodes/intent_parser.py`).
Constitution Principle VI (Declared Model Tier) and ADR-010 already establish a
tiering scheme in which "the judge, planner and policy paths... begin at the
frontier tier and stay there," with no dynamic demotion permitted.

**Amendment context**: `/speckit-analyze` (2026-09-14) found that this feature's
own FR-013/SC-006 ("no live cloud service calls required to reach a verdict")
directly contradicted the judge model requiring a live LLM call, and that the
plan's originally-assumed implementation (call the Azure OpenAI SDK directly,
matching every other node) would have hardcoded exactly the kind of
cloud-provider dependency `docs/ResortFlow-Cloud-Portability-Review.md` and ADR-039
already named — under "the missing LLM-provider abstraction" — as a deferred
Tier-1 gap. Since this feature is introducing `eval/judge.py` as new code
regardless, it is the natural, low-cost occasion to close that specific,
already-documented gap for the judge client, without taking on a project-wide
LLM-client migration.

## Options Considered

**1. A new central model-tier configuration module** (e.g.
`graph/model_tiers.py`) mapping tier names to pinned identifiers project-wide,
introduced as part of this feature. Advantage: a single, authoritative,
inspectable source for every tier's pinned model, closing a gap that exists
project-wide, not just for the judge. Disadvantage: every other node still
reads its own env var directly today; introducing a central module used only
by the judge would create an inconsistent half-migration, and a project-wide
migration is a larger, cross-cutting change well beyond this feature's scope.

**2. Extend the existing env-var pattern** with a `MODEL_JUDGE` variable,
mirroring `MODEL_CHEAP`'s shape exactly, defaulting to a pinned deployment name.
The judge's tier is the existing frontier tier (ADR-010); FR-010's
"not weaker than the generator" guarantee falls out of the existing tier
ordering rather than a new comparison the plan would otherwise need to invent.
Advantage: minimal new surface, consistent with how every other model
selection already works in this codebase, ties judge strength to a governance
mechanism the constitution already owns. Disadvantage: does not close the
broader "no central tier config module exists anywhere" gap — that remains
true after this feature ships, for every tier, not just the judge.

**3. A bespoke ranking/comparison table specific to eval-judge selection**,
independent of the existing tier scheme, encoding numeric "strength" scores
per model. Advantage: could in principle support finer-grained comparisons
than a coarse tier ordering. Disadvantage: a second, parallel notion of model
strength alongside the constitution's own tiering scheme, which
`/speckit-analyze` cannot check against the same rules it already applies to
Principle VI — introduces exactly the kind of un-declared, un-checkable
judgment call Principle VI exists to eliminate.

**4. (Amendment) Call the Azure OpenAI SDK directly in `eval/judge.py`**,
matching every other node's current pattern exactly. Advantage: zero new
abstraction, perfectly consistent with today's codebase. Disadvantage:
hardcodes a specific cloud provider's SDK into brand-new code, in a feature
whose own FR-013a now requires the judge's LLM provider to be swappable via
configuration — and it is exactly the "missing LLM-provider abstraction"
ADR-039's cloud portability review already flagged as a known, deferred gap.
Building a fourth Azure-SDK call site here would need revisiting later anyway.

## Decision

**Adopt Option 2, with Option 4 rejected on amendment.** `MODEL_JUDGE` extends
the existing env-var pattern for pinning; judge strength is the existing
frontier tier; no new tiering scheme or central config module is introduced by
this feature. `eval/judge.py` itself is built behind a minimal,
provider-agnostic interface (an OpenAI-compatible chat-completions call, with
provider/endpoint/auth read from configuration) rather than calling the Azure
OpenAI SDK directly — closing ADR-039's deferred Tier-1 finding for this one
new client, without a project-wide LLM-client migration. Azure OpenAI remains
the only provider actually configured in this build (spec.md Assumptions).

## Consequences

**Made easier.** Judge pinning reuses a pattern every engineer on this project
already recognizes (`MODEL_CHEAP`'s shape), and judge-strength verification is
free — it is the same tier ordering the constitution and `/speckit-analyze`
already enforce. Switching the judge's LLM provider later (SC-017) is a
configuration change, not a rewrite of every scorer that calls `eval/judge.py`.

**Made harder.** The project-wide absence of a central model-tier config
module is still unresolved — a future audit of "which model is each node
actually pinned to" still requires grepping env-var defaults across most
files. This feature closes the provider-abstraction gap for exactly one client
(the judge); every other node's direct Azure-SDK call is untouched.

**Foreclosed.** This feature does not attempt the broader model-tier-config
consolidation, and does not migrate any existing node off its direct
Azure-OpenAI-client call — both are named here as candidate future work, not
undertaken now, so a reader does not mistake either absence for an oversight
this plan failed to notice.

## Addendum — Feature 009 (2026-09-27)

Judge **prompts** are now pinned alongside the judge **model**, by content hash
(`prompts.canonical.template_sha256`, ADR-053), resolved from Git only (`resolution: exact` —
registry version numbers are not portable across registries, so only the hash is comparable;
research R-06). `JudgeModelRecord` gained `prompt_id`/`prompt_sha256`/`prompt_version` (T048),
recorded on every judge-scored `MetricScore` next to the provider and model (FR-017).

This is deliberately **distinct** from this ADR's existing `BASELINE_INVALID` (a judge *model*
mismatch, which suspends the baseline-delta check but never fails the run by itself): a judge
*prompt* hash mismatch is `BASELINE_STALE` (`eval/gate.py`, ADR-054's contract, research R-10) — a
new hard FAIL, because every historical score under the old prompt was measuring something that no
longer exists, which a model-only mismatch is not. The two verdicts share a root cause (something
about the judge changed since the baseline was captured) but warrant different responses: a model
upgrade needs the delta check suspended until re-baselined at leisure; a prompt edit needs the merge
blocked until the baseline is regenerated in the same change.
