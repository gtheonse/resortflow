# ADR-039 — Multi-cloud abstraction boundary

**Status**: Accepted
**Date**: 2026-09-13
**Feature**: cloud-portability-review

## Context

`AGENTS.md` and `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` describe ResortFlow as
an "all-Azure" shop, and the repository's production-target prose commits to
specific Azure services throughout: Azure OpenAI, Azure AI Search, Azure
Database for PostgreSQL, Azure Storage, Azure Service Bus, Microsoft Entra
ID, Azure APIM, Azure Container Apps, Key Vault, and Microsoft Foundry
Observability. A review requested to determine whether ResortFlow could deploy to
any of AWS, GCP, or Azure (`docs/ResortFlow-Cloud-Portability-Review.md`) found the
system is not currently cloud-neutral: several artifacts are hard-wired to
Azure specifically, including all five frozen OpenAPI contracts' `servers:`
URLs, two Azure-only emulator images sitting in `docker-compose.yml`'s
*default* service list, and one hardcoded telemetry attribute value.

At the same time, two existing ADRs already contain the reasoning this
decision generalizes. ADR-003 (Qdrant vs. Azure AI Search) and ADR-004
(Postgres vs. Cosmos DB) both chose the portable, self-hostable engine over
an Azure-native managed alternative, and ADR-004 states the governing
principle directly: "if a behavior only reproduces in Azure, the abstraction
is wrong." Both ADRs applied that rule only at the **local-vs-Azure**
boundary — neither was asked to justify itself against a second or third
cloud.

## Options Considered

**1. No change — leave cloud choice as an implicit, per-ADR judgment call.**
Costs nothing today. Risks silent, uncoordinated Azure-specific choices in
future ADRs (a new managed-service dependency introduced without anyone
asking whether it forecloses AWS/GCP), since no standing rule currently
requires that question to be asked.

**2. Add "multi-cloud portability" as a seventh constitutional principle.**
Would gate every future feature's `/speckit-plan` and `/speckit-analyze`
Constitution Check. Rejected: the constitution's existing six principles
(ambiguity-based routing, governed integration surface, two-phase mutation,
policy-as-code, durable state, declared model tier) are all behavioral
invariants that every feature touches. Cloud portability is an
infrastructure-selection discipline that applies narrowly, to Track F
(`infra/`, `docker/`) and to any ADR introducing a new managed-service
dependency — forcing every unrelated feature (e.g., a policy-engine change)
to answer a portability question would be constitutional scope creep.

**3. A standalone ADR generalizing ADR-003/ADR-004's existing rule across all
three clouds, without touching the constitution.** Durable and citable at
the altitude the decision actually operates — infra and dependency
selection — without inflating the constitution's blast radius.

## Decision

**Adopt Option 3.** The standing rule, extending ADR-004's own language:

> Every subsystem choice must be justified as if a second cloud is a live
> constraint, not a hypothetical. A design where only Azure reproduces the
> behavior is a defect, regardless of whether AWS or GCP are ever actually
> deployed.

This generalizes, and does not contradict, ADR-003 and ADR-004 — both remain
Accepted. Where those two ADRs asked "does this also work outside Azure, in
local Docker," this ADR asks the same question one axis wider: "does this
also work on AWS or GCP." Concretely, this ADR records:

- `docs/ResortFlow-Cloud-Portability-Review.md` is the audit this principle
  produced, classifying every subsystem into Tier 1 (hard-wired to Azure —
  a genuine defect under this rule), Tier 2 (already portable, only the
  documented target is Azure-exclusive — a prose fix, not a defect), or
  Tier 3 (a legitimate Azure-only premium feature — explicitly exempted by
  this rule, since forcing an equivalent onto AWS/GCP would be manufacturing
  parity nobody asked for).
- Tier 1 remediation (parameterizing the OpenAPI `servers:` blocks, replacing
  the `azurite`/`servicebus-emulator` pair in the default compose topology,
  fixing the hardcoded `gen_ai.system` attribute, introducing an
  LLM-provider abstraction) is **deliberately deferred** to future,
  individually spec'd SpecKit features — not implemented by this ADR or the
  review that produced it. This ADR is the record required by `AGENTS.md`'s
  "if a step is skipped, record it in an ADR rather than proceeding
  silently" rule: the deferral is documented, not silent.
- Any future ADR introducing a new managed-service dependency must state,
  in its own Consequences section, whether that
  dependency is Tier 1, 2, or 3 under this rule — the same way ADR-003 and
  ADR-004 already reasoned about the local-vs-Azure axis.

## Consequences

**Made easier.** Future infra decisions have a named, citable standard to
apply, rather than re-deriving the portability argument from scratch each
time (as ADR-003 and ADR-004 each did independently). The Tier 1/2/3
classification gives a fast way to triage whether a proposed Azure-native
service is a real problem or just a documentation gap.

**Made harder.** Any future ADR proposing a new managed-service dependency
now has an explicit obligation to address the multi-cloud question, even with
no funded multi-cloud deployment — that's overhead
on every such ADR going forward, not just this one.

**Foreclosed.** This ADR does not itself fix any Tier 1 finding — the
hardcoded OpenAPI URLs, the Azure-only emulators in the default compose
topology, and the missing LLM-provider abstraction all remain exactly as
found until a future feature addresses them. Treating this ADR as
remediation, rather than as the record that remediation is deferred, would
be a misreading of its scope.
