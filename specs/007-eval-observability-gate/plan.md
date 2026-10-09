# Implementation Plan: Evaluation & Observability Gate

**Branch**: `007-eval-observability-gate` | **Date**: 2026-09-13 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/007-eval-observability-gate/spec.md`

## Summary

Build ResortFlow's first CI-blocking evaluation gate and its first shared,
cloud-agnostic telemetry module — together, "measured, not asserted" for every
agent domain. Research found almost nothing to extend and several things to
consolidate: `make eval` today is 4 assertion-only domain runners with zero
LLM-judge or retrieval-quality scoring; saga-failure coverage is real but
scattered across a dozen test files with no formal permutation matrix; no CI
exists at all (`.github/workflows/` is absent); and observability is two
independent, already-diverging ad-hoc tracers (`agents/dining/tracing.py`,
`mcp/gateway/_shared/tracing.py`) plus a fully-built-but-never-wired-in
redaction module (`observability/redaction.py`), with the constitution's
"every node emits both `gen_ai.*` and `langfuse.*`" requirement currently
unmet outside dining. This feature closes both gaps at once: a new `eval/`
package (Track E's first real code) implementing the three-tier gate
(PR/merge-queue/nightly) the spec's clarify session fully specified — binary
vs. statistical gates, 3×-repeat variance-adjusted floor checks kept
statistically separate from raw-mean baseline-delta checks, automatic
diff-derived coverage requirements with a time-boxed exemption lifecycle — and
a new `observability/telemetry.py` module that every domain (not just dining)
finally emits spans through, with the exporter target selected purely by
config so neither the gate's verdict nor the traces it produces depend on any
cloud provider's proprietary observability/evaluation platform — the judge
model's LLM call is the one expected exception, and it too is built behind a
provider-agnostic client (`/speckit-analyze` finding I1, ADR-044 amended) so
switching providers later is a config change, not a rewrite. Seven decisions
met the ADR significance bar; all seven (040-046) are recorded in `docs/adr/`.

## Technical Context

None of the following carries `NEEDS CLARIFICATION` — every unknown was
resolved against the actual repository state in `research.md`.

**Language/Version**: Python 3.11+ (matches `agents/`, `graph/`, `mcp/gateway/`,
unchanged).

**Primary Dependencies**: `opentelemetry-api`/`opentelemetry-sdk` (already a
dependency — reused, not new), plus one new addition:
`opentelemetry-exporter-otlp` (generic OTLP export, ADR-041 — no vendor SDK).
No new eval-specific library: retrieval/trajectory scoring is hand-rolled via a
minimal, provider-agnostic OpenAI-compatible chat-completions client
(`eval/judge.py`), rather than adopting `ragas`/`deepeval` (ADR-043) or calling
the Azure OpenAI SDK directly (ADR-044, amended) — Azure OpenAI is the only
provider actually configured in this build, but the client itself does not
hardcode it. `pytest`/`hypothesis` (existing, reused for the harness's own
tests and for saga-permutation-style property tests).

**Storage**: PostgreSQL unchanged — no new table, no new use of the
checkpointer for eval bookkeeping (ADR-045). New: versioned repo files under
`eval/data/` (`baseline.json`, `exemptions.yaml`, `reports/<run-id>.json`),
updated only by the merge-queue tier's automated commit on `main`.

**Testing**: pytest, extending the existing per-domain `eval/runner.py` +
`test_cases.yaml` pattern for trajectory scoring; the saga suite wraps
existing, already-passing pytest tests (`agents/tests/test_partial_failure_
recovery*.py` and siblings) behind a named permutation registry rather than
rewriting them (research.md R9). `hypothesis` (existing dev-dependency)
available for saga-permutation generation if useful.

**Target Platform**: Linux server / GitHub Actions runners (new — this
feature introduces this repository's first CI, ADR-046) plus local
docker-compose, unchanged. No new `docker-compose.yml` service: Langfuse 3.x's
existing native OTLP ingestion endpoint makes a separate OTel Collector
unnecessary for local dev (research.md R3).

**Project Type**: Multi-service backend, unchanged. First feature to put real
code in `eval/` (Track E, currently `.gitkeep` only) and the first to add
`.github/workflows/`.

**Performance Goals**: PR tier under ~10 minutes (FR-019/SC-010); per-turn
evaluation latency measurably below the ~90s/turn baseline observed in Phase 6
adversarial testing, for both blocking tiers (FR-020/SC-011).

**Constraints**: Zero dependency on any specific cloud provider's proprietary
observability/evaluation platform required to reach a PASS/FAIL/INCOMPLETE
verdict — the judge-model LLM call itself is exempted and expected (FR-013, as
amended); judge model MUST be a pinned, non-`latest` identifier, selected
through a provider-agnostic client (FR-009, FR-013a); `gen_ai.system` and any
other vendor-flavored telemetry value MUST come from configuration, never a
literal (FR-015, closing the Tier-1 defect ADR-039 flagged before it ever
reaches actual code — the same treatment FR-013a now gives the judge client).

**Scale/Scope**: 5 agent domains scored for retrieval/trajectory quality; 3
transactional domains (dining, stay, entertainment) in the saga-correctness
suite (gaming excluded — read-only, per spec Assumptions). See Project
Structure below for the exact file list.

## Constitution Check

*Gate: MUST pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Ambiguity-Based Routing | **PASS** | The judge model is invoked only for genuinely qualitative questions (faithfulness, relevance, plan-quality-with-multiple-valid-paths); tool-selection correctness and unnecessary-step rate are scored by deterministic sequence comparison against a reference trace (ADR-043) — the same "arithmetic where arithmetic will do" discipline the conflict checker already applies. None of this runs in the live guest-facing request path; it is build/CI-time tooling. |
| II. Governed Integration Surface | **PASS** | The eval harness introduces no new agent tool; saga cases exercise the *existing*, already-MCP-mediated sim endpoints (including the existing `X-Sim-Fault` mechanism, unmodified) rather than a new bypass. Judge-model calls go through a new, minimal provider-agnostic client (`eval/judge.py`, ADR-044 amended) rather than reusing other nodes' direct Azure-SDK pattern — a deliberate deviation from "reuse what exists" specifically to avoid hardcoding a vendor SDK into this feature's own cloud-agnostic surface (FR-013a). |
| III. Two-Phase Mutation | **PASS** | This feature performs no live guest mutations and adds no new mutation path. The saga suite formalizes reporting over *existing* hold/confirm/compensate test coverage (research.md R9); it does not change saga behavior. |
| IV. Policy Is Code, Not Prompt | **PASS — this feature is one of its enforcement arms** | FR-009a's Tier 2 coverage requirement fail-closes any `policy/*.py` change lacking eval coverage, at the CI layer — complementary to, and reinforcing, Phase 6's runtime enforcement, not a re-implementation of it. |
| V. Durable, Inspectable State | **PASS — this feature's literal purpose** | The Conversation Record (FR-012) is built from the *existing* checkpointer plus the new telemetry module's span data — a read-oriented projection, not a second mutable state store beside the encrypted checkpointer (ADR-045's explicit boundary). Baseline/exemption history stored as versioned repo files extends "durable, inspectable" to the CI-state layer with git itself as the audit trail. |
| VI. Declared Model Tier | **PASS, extends existing tiering** | The judge model is pinned via a new `MODEL_JUDGE` env var mirroring the existing `MODEL_CHEAP` pattern; its tier is the *existing* frontier tier ADR-010 already reserves for judge/planner/policy paths — no new tiering scheme, no new "weaker than generator" ranking system (ADR-044). |

No Constitution violation requires a Complexity Tracking entry — see below.

## Project Structure

### Documentation (this feature)

```text
specs/007-eval-observability-gate/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/           # Phase 1 output
│   ├── eval-report-schema.md
│   ├── telemetry-module-contract.md
│   ├── coverage-exemption-schema.md
│   └── ci-tier-triggers-contract.md
└── tasks.md             # Phase 2 output (/speckit-tasks — not yet generated)
```

### Source Code (repository root)

```text
eval/                                # Track E — NEW, first real code (was .gitkeep only)
├── __init__.py
├── judge.py                         # Provider-agnostic, pinned judge-model client
│                                     #   (MODEL_JUDGE, OpenAI-compatible interface, ADR-044 amended)
├── retrieval_scorer.py              # Hand-rolled RAGAS-style prompts (ADR-043):
│                                     #   context recall/precision/faithfulness/relevance
├── trajectory_scorer.py             # Deterministic tool-selection/step-count diffing;
│                                     #   judge model used only for ambiguous plan-quality cases
├── saga_suite.py                    # Named Saga Failure Case registry wrapping existing
│                                     #   pytest tests (research.md R9) — no test rewrites
├── cost_latency.py                  # Per-step budget comparison (FR-004/005)
├── coverage.py                      # FR-009a diff-heuristic detection (ADR-040) +
│                                     #   Coverage Exemption lifecycle (FR-025-029)
├── baseline.py                      # Baseline load/store/eligibility, BASELINE_INVALID
│                                     #   handling (FR-007c, ADR-042)
├── gate.py                          # Binary/statistical gate combinator, verdict computation
│                                     #   per contracts/eval-report-schema.md
├── drift.py                         # Nightly-tier multi-run baseline-drift reporting
│                                     #   (FR-019 nightly bullet, ADR-042 Consequences)
├── report.py                        # Consolidated report assembly (JSON + human-readable),
│                                     #   PII-masked at assembly time (FR-018)
├── runner.py                        # `python -m eval.runner --tier pr|merge_queue|nightly`
│                                     #   (FR-006/019) — the one command CI and laptops both call
├── cli.py                           # `python -m eval.cli show-conversation --id ...` (FR-011)
├── data/                            # NEW — versioned repo state (ADR-045)
│   ├── baseline.json                #   updated only by merge-queue tier on main
│   ├── exemptions.yaml              #   see contracts/coverage-exemption-schema.md
│   ├── budgets.yaml                 #   cost/latency budget thresholds (FR-004/005)
│   └── reports/                     #   one file per Evaluation Run
└── tests/                           # NEW — unit tests for the harness's own logic
                                      #   (gate math, coverage/exemption rules, telemetry
                                      #   contract conformance, saga registry mapping)

observability/
├── __init__.py                      # UNCHANGED
├── attributes.py                    # UNCHANGED — already-frozen vocabulary, now finally used everywhere
├── redaction.py                     # UNCHANGED — already built; this feature is what finally calls it
└── telemetry.py                     # NEW — start_agent_span/set_payload, the ONLY module
                                      #   that sets a span attribute anywhere in the repo (ADR-041)

agents/
├── dining/tracing.py                # AMENDED — becomes a thin wrapper over observability.telemetry
├── entertainment/, stay/, wellness/, gaming/
│                                     # AMENDED — first span emission ever, via observability.telemetry
├── {dining,entertainment,stay,wellness}/eval/
│                                     # AMENDED — existing runner.py extended to feed
│                                     #   eval.trajectory_scorer instead of only local assertions
└── gaming/eval/adversarial/runner.py
                                      # AMENDED — extended to also feed eval.trajectory_scorer
                                      #   on its existing turn set, so gaming isn't the one
                                      #   domain excluded from SC-001's five-domain coverage

mcp/gateway/_shared/tracing.py       # AMENDED — becomes a thin wrapper over observability.telemetry

graph/supervisor.py                  # AMENDED — first supervisor-level span emission,
                                      #   via observability.telemetry (previously untraced)

.github/workflows/                   # NEW — this repo's first CI (ADR-046)
├── pr-gate.yml                      #   pull_request → eval.runner --tier pr
├── merge-queue-gate.yml             #   merge_group → eval.runner --tier merge_queue
│                                     #   (only this workflow writes eval/data/baseline.json)
└── nightly.yml                      #   schedule → eval.runner --tier nightly (never blocks)

pyproject.toml                       # AMENDED — opentelemetry-exporter-otlp added;
                                      #   explicitly NOT adding ragas/deepeval (ADR-043)

docker-compose.yml                   # UNCHANGED — Langfuse's existing native OTLP endpoint
                                      #   is the local exporter target; no new service (ADR-041)
```

**Structure Decision**: `eval/` (Track E) is this feature's primary, new
surface — the first code Track E has ever owned, mirroring how Phase 6 gave
Track G (`policy/`) its first real code. `observability/` gains one new
module rather than becoming a new track. `agents/` and `graph/` (Track C)
carry the span-emission amendments every domain needs; `mcp/gateway/` (Track
B) has its existing tracer become a thin wrapper, no new tool or domain.
`.github/workflows/` and `pyproject.toml`/`docker-compose.yml` (Track F) carry
the new CI and dependency. No new `sims/` endpoint and no new MCP domain are
introduced — the saga suite exercises existing endpoints and the existing
`X-Sim-Fault` mechanism verbatim.

## Complexity Tracking

*No entries.* Every Constitution gate above passed. The one deliberately
deferred capability — large-payload externalization to blob storage, part of
the Deep Dive doc's fuller telemetry design — is not a Constitution violation
requiring justification; it is scoped out explicitly in ADR-041's Consequences
and `telemetry-module-contract.md`, with truncation-plus-marker-attribute as
this phase's interim behavior.

## Architecture Decision Records *(mandatory — ResortFlow project override)*

Seven decisions in this plan meet the significance bar (constrains a later
feature; a competent engineer could reasonably have chosen differently;
reversing it later would be breaking; or it trades one stated project value
against another). Each is fully drafted at `docs/adr/`:

### Plan completion gate

| ADR | Decision | Status |
|---|---|---|
| [040](../../docs/adr/040-coverage-requirement-detection-via-diff-heuristics.md) | Coverage requirements are detected via git-diff heuristics tailored per capability type (decorator scan, path glob, file/function-path heuristics) — no new central manifest, respecting ADR-028's explicit no-registry policy design | [x] drafted |
| [041](../../docs/adr/041-shared-cloud-agnostic-telemetry-module.md) | One shared `observability/telemetry.py` module, exporter selected by standard OTel env var, no vendor SDK — consolidates two diverging ad-hoc tracers and closes the constitution's already-unmet "every node emits" requirement | [x] drafted |
| [042](../../docs/adr/042-statistical-gate-methodology.md) | Statistical gates use two distinct statistics: variance-adjusted score against a fixed floor, raw mean-vs-mean against a variance-aware margin for baseline delta — never the same adjusted value on both sides | [x] drafted |
| [043](../../docs/adr/043-hand-rolled-eval-scorers-over-third-party-library.md) | Retrieval-quality metrics are hand-rolled against the pinned-judge client; trajectory correctness is deterministic sequence diffing; no `ragas`/`deepeval` dependency adopted | [x] drafted |
| [044](../../docs/adr/044-judge-model-pinning-extends-existing-tiering.md) | Judge pinning extends the existing `MODEL_CHEAP`-style env-var pattern (`MODEL_JUDGE`); judge strength reuses the existing frontier tier; the client itself is provider-agnostic (OpenAI-compatible interface), closing ADR-039's deferred LLM-provider-abstraction gap for this one client (amended 2026-09-14) | [x] drafted |
| [045](../../docs/adr/045-eval-state-as-versioned-repo-files.md) | Evaluation Run history, Baseline, and Coverage Exemptions are versioned repo files under `eval/data/`, not a new database or CI-platform-native storage | [x] drafted |
| [046](../../docs/adr/046-gate-tiers-as-github-actions-workflows.md) | The three gate tiers are thin GitHub Actions workflow wrappers around one platform-neutral `eval.runner` command — this repo's first CI | [x] drafted |

Every ADR above records its context, options, decision, and consequences.
