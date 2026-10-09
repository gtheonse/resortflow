# Phase 0 Research: Evaluation & Observability Gate

Every item below was resolved by reading the actual repository state, not by
assumption. Sources are cited inline. None of Technical Context's fields in
`plan.md` carry `NEEDS CLARIFICATION` after this pass.

## R1. Current state baseline (what exists vs. what this feature builds)

**Finding**: This feature has almost no prior art to extend and several pieces of
scattered prior art to consolidate.

- `make eval` today chains 4 domain runners (`agents/{dining,entertainment,stay,
  wellness}/eval/runner.py`) that load YAML cases and check fixed boolean/enum
  assertions — **zero LLM-judge usage, zero retrieval-quality scoring**. This is
  the pattern to extend for trajectory scoring, not the pattern for retrieval
  scoring.
- `eval/` at repo root is empty (`.gitkeep` only). No earlier phase's
  `research.md`/`data-model.md` sketches what belongs there.
- Saga-failure coverage is real but scattered across
  `agents/tests/test_partial_failure_recovery*.py`, `test_commit_verification.py`,
  `test_conflict_detection.py`, and per-domain `test_graph.py` files — no formal,
  named, cross-domain permutation matrix exists.
- The 40-turn adversarial script (`agents/gaming/eval/adversarial/`) exists and is
  excluded from `make eval` today via a separate `make eval-adversarial` target,
  precisely because of the latency problem FR-020 targets.
- **No CI exists at all** (`.github/workflows/` does not exist). This feature
  introduces CI for the first time.
- `observability/attributes.py` (frozen attribute vocabulary) and
  `observability/redaction.py` (Presidio-based, fully built) exist; `redaction.py`'s
  own docstring says "Phase 7 owns the actual emission pipeline." **No
  `telemetry.py` exists anywhere.** Two independent, ad-hoc tracers exist instead:
  `agents/dining/tracing.py` (Langfuse, dining-only) and
  `mcp/gateway/_shared/tracing.py` (bare OTel, gateway-only, explicitly scoped as
  "Feature 007's job" for OTLP/dual-backend wiring). Neither sets `gen_ai.system`
  today — the hardcoded `"az.ai.openai"` value ADR-039 flags is doc-only
  (`docs/ResortFlow-Observability-Tracing-Deep-Dive.md:105`), not yet materialized in
  code. This feature must avoid introducing it, not remediate an existing instance.
- `docker-compose.yml` already runs `langfuse` (image `langfuse/langfuse:3`) +
  `clickhouse` in the default service list. Langfuse 3.x exposes a native OTLP
  ingestion endpoint (`/api/public/otel`), so no separate collector service is
  needed locally.
- No runtime model-tier config module exists (`docs/model-tiering-runtime.md` does
  not exist as a file). Model selection today is plain env vars read directly by
  node code (e.g. `agents/dining/nodes/intent_parser.py:275`,
  `MODEL_CHEAP` defaulting to `gpt-4.1-mini`).
- Tool entitlements are declared via `@register_tool(domain=..., 
  entitlement_action="read"|"hold"|"commit", ...)` at each tool's definition site
  (`mcp/gateway/_shared/registry.py:40-157`), consistently across the 5 domain
  gateway modules — no central manifest, but a reliably diff-parseable pattern.
- The `X-Sim-Fault` mechanism (`sims/_shared/faults.py`) already produces a
  structurally distinct signal for "deliberately injected fault" (a well-formed
  error envelope with a recognizable code) vs. genuine unreachability (a
  transport-level exception, no envelope) — this is the FR-024 discriminator, and
  it requires no new mechanism.

## R2. Coverage-requirement detection (FR-009a)

**Decision**: Detect required-coverage triggers via git-diff analysis against
existing structural signals, per capability type — no new central registry.

| Capability | Detection method |
|---|---|
| `commit`/`hold` entitlement tool | Regex/AST scan of the diff for added/changed `@register_tool(...)` decorator calls carrying `entitlement_action="commit"` or `"hold"` |
| Tenant-scoped corpus | Path glob on the diff against `content/corpus/{tenant}/*.yaml` |
| Saga participant / compensation path | Path+function-boundary heuristic: diff touches `graph/supervisor.py` within the `Domain` enum, `_SAGA_COMMIT_ORDER`, `DEFAULT_TOOLS_FACTORY`, or the `recovery()` function body |
| Policy rule | File-granularity heuristic: diff touches any of `policy/{gaming,dietary,injection,distress,jurisdiction}.py`, excluding `tests/` |

**Rationale**: The first two are already reliably enumerable from existing code
structure (a consistent decorator, a consistent directory layout) — a new
manifest would duplicate a source of truth that already exists and could drift
from it. The latter two are **not** structurally enumerable today: `policy/`'s
`__init__.py` states outright that policy rules are deliberately "ordinary,
unit-testable functions" with "no shared `evaluate(rule_set, context)` indirection
layer" (ADR-028) — introducing a registry there would reopen a decision Phase 6
already made deliberately. File/function-path heuristics are coarser (they can
over-trigger on an unrelated change to the same file) but that coarseness is the
same fail-closed conservatism FR-009a already requires — a false positive costs an
exemption filing; a false negative would ship an uncovered commit-capable surface.

**Alternatives considered**:
- *New central capability manifest* (a YAML file every commit/hold tool, saga
  participant, and policy rule must register in). Rejected: duplicates the
  decorator/file structure that already exists, adds a second source of truth
  that can drift, and directly reverses ADR-028's explicit no-registry policy
  design for no compensating benefit.
- *AST-level function-granularity diffing for policy/saga* (detect exactly which
  function changed, not just which file). Rejected for this phase: meaningfully
  more implementation complexity for a precision gain that mostly reduces
  false-positive exemption filings, not false negatives — the coarser
  file-granularity heuristic is the safer default to ship first, with the
  finer-grained version left as a documented future enhancement.

## R3. Shared, cloud-agnostic telemetry module

**Decision**: Introduce `observability/telemetry.py` as the single shared module
implementing `start_agent_span()`/`set_payload()` (per
`docs/ResortFlow-Observability-Tracing-Deep-Dive.md` §3, adjusted to this repo's actual
`observability/` path rather than the doc's stale `resortflow/observability/` path).
`agents/dining/tracing.py` and `mcp/gateway/_shared/tracing.py` become thin
call-sites into it; every other domain (entertainment, stay, wellness, gaming) and
`graph/supervisor.py` gain their first span emission through the same module,
closing the constitution's already-violated "every external call and every graph
node MUST emit" requirement. The exporter target is selected purely by an
environment variable pointing at an OTLP endpoint (Langfuse's native OTLP
ingestion locally, any OTLP-speaking collector elsewhere) — no vendor SDK import
in the module itself, satisfying FR-014/FR-015. No new `otel-collector` service is
added to `docker-compose.yml`: Langfuse 3.x's built-in OTLP endpoint makes one
unnecessary for local dev, and the same env-var swap mechanism is what makes a
different destination possible elsewhere.

**Rationale**: Two independent, ad-hoc tracers already diverging (different
attribute sets, different libraries) is exactly the drift the Deep Dive doc's
"one module is the only place an attribute name appears" design exists to
prevent, and it is already listed as this phase's responsibility by both
tracers' own docstrings.

**Alternatives considered**:
- *Leave the two existing tracers as-is and add a third for the remaining
  domains.* Rejected: entrenches the exact drift risk (three inconsistent
  attribute sets instead of two) the constitution's Development Standards
  section already prohibits.
- *A dedicated OTel Collector service in `docker-compose.yml`.* Rejected for
  local dev (Langfuse's native OTLP endpoint already does the job with one fewer
  moving part); left as a documented option for a production target that needs
  fan-out to multiple backends simultaneously, which is out of this phase's scope.

## R4. Retrieval and trajectory scoring implementation

**Decision**: Hand-rolled, pinned-judge-model scorers for retrieval-quality
metrics (context recall, context precision, faithfulness, answer relevance),
called through `eval/judge.py`'s provider-agnostic client (R5) rather than
built directly against a cloud-specific SDK. Agent
trajectory correctness (tool selection, unnecessary-step rate) is scored
**deterministically** — a diff between the actual tool-call sequence in a
Conversation Record and a reference sequence — with the pinned judge model used
only for "plan quality" cases that have more than one valid reference path. No
third-party eval library (`ragas`, `deepeval`) is added as a dependency.

**Rationale**: A third-party eval library brings its own judge-model abstraction,
which would fight directly with FR-009/FR-010's requirements (a specific, pinned,
named judge model per metric, verified stronger than the generator) — reconciling
two judge-selection mechanisms is more complexity than writing the four RAGAS-style
prompts directly. It also keeps the dependency footprint aligned with the
project's existing minimal-new-dependency pattern (Phase 6 added exactly one new
runtime library, Presidio, for a capability nothing in-repo already did — this
phase's judge-scoring capability is a small, well-understood set of prompts, not
a comparable gap). Constitution Principle I (DETERMINISM FIRST) directly favors
deterministic tool-sequence comparison over an LLM judge wherever a reference
sequence exists.

**Alternatives considered**:
- *Adopt `ragas`.* Rejected: its judge-model wiring goes through its own LLM
  wrapper abstraction (historically LangChain-coupled), which would need to be
  bent to also emit this project's dual-namespace telemetry and satisfy the
  pinned-version-per-run requirement — net more integration work than four
  hand-written scoring prompts, for a library whose main value (breadth of
  metrics) exceeds what this phase's four named metrics need.
- *LLM-judge everything, including trajectory correctness.* Rejected: violates
  Constitution Principle I outright for a class of check (did the agent call
  the right tool) that already has a ground-truth reference sequence to diff
  against mechanically.

## R5. Judge model pinning and provider abstraction

**Decision**: Extend the existing env-var model-selection pattern with a
`MODEL_JUDGE` variable, defaulting to a pinned, dated deployment name (never a
`latest` alias) — mirroring `MODEL_CHEAP`'s existing shape. The judge model's
tier is the existing **frontier** tier from ADR-010 / Constitution Principle VI
("the judge, planner and policy paths... begin at the frontier tier and stay
there") — no new tiering scheme is introduced. FR-010's "judge MUST NOT be
weaker than the model being judged" is enforced by the existing tier ordering: a
frontier-tier judge is by construction never weaker than an economy- or
mid-tier generator.

**Amendment (`/speckit-analyze`, 2026-09-14, finding I1)**: `eval/judge.py` MUST
additionally be built behind a minimal, provider-agnostic interface — an
OpenAI-compatible chat-completions call whose provider, endpoint, and
authentication are all read from configuration, not hardcoded to Azure OpenAI's
SDK. This is a direct, scoped closure of the "missing LLM-provider abstraction"
Tier-1 finding `docs/ResortFlow-Cloud-Portability-Review.md` and ADR-039 already
flagged and explicitly deferred — closed here **for the judge client only**;
every other existing node's direct Azure-OpenAI-client call (e.g.
`agents/dining/nodes/intent_parser.py`) is unchanged and out of this feature's
scope. Azure OpenAI remains the only provider actually configured and exercised
in this build (spec.md Assumptions) — this is a design requirement (the
*ability* to swap), not a commitment to run a second provider now, exactly
mirroring FR-014/015's already-decided treatment of the tracing exporter.

**Rationale**: Reuses an established, working pattern (env-var model selection)
instead of inventing a second model-configuration mechanism, while closing a
named, already-documented portability gap for the one new client this feature
introduces — at effectively no extra cost, since a provider-agnostic
chat-completions call is not meaningfully more code than an Azure-SDK-specific
one, and the project already accepted the equivalent design for telemetry
export (ADR-041) one section earlier in this same document.

**Alternatives considered**:
- *A new central model-tier config module* (e.g., `graph/model_tiers.py` mapping
  tier names to pinned identifiers project-wide). Rejected as out of scope: no
  such module exists anywhere yet, and every other node still reads its own env
  var directly; introducing one just for the judge model would create an
  inconsistent half-migration. Left as a candidate future refactor, noted but not
  undertaken here.
- *Call the Azure OpenAI SDK directly in `eval/judge.py`*, matching every other
  node's current pattern exactly. Rejected on amendment: this is precisely the
  Tier-1 portability gap ADR-039 already named as deferred, and building a new
  client this phase was the natural, low-cost occasion to close it for the
  judge specifically, rather than adding a fourth Azure-SDK call site that
  would need revisiting later anyway.
- *Adopt a third-party LLM-abstraction library (e.g., LiteLLM) for the provider
  swap.* Rejected: an OpenAI-compatible chat-completions call is a small,
  stable interface `eval/judge.py` can own directly; adding a library
  dependency for this narrow a need repeats ADR-043's reasoning against
  `ragas` — more integration surface than the four lines of abstraction
  actually required.

## R6. Statistical gate methodology

**Decision** (already fully specified via `/speckit-clarify`, ratified here as an
architecture decision this plan owns): binary gates (zero tolerance) and
statistical gates (threshold-based) are computed and evaluated independently.
Statistical gates run their eval set 3× per merge-queue run; the absolute-floor
check uses the variance-adjusted score (mean − 1σ) from those 3 repeats; the
baseline-delta check compares **raw means** (current vs. baseline) against a
margin of `max(fixed %, 2 × σ_baseline)`. A baseline is only eligible if it
reached a COMPLETE verdict and carries a matching pinned judge model; a mismatch
produces `BASELINE_INVALID` rather than a false regression.

**Rationale**: recorded in full in `spec.md`'s Clarifications session
(2026-09-13, Q4) — conflating the floor check's variance-adjustment with the
delta check would double-count variance and reintroduce the flakiness the
adjustment exists to remove. Documented here because it is exactly the kind of
decision Constitution's "EVERY DECISION IS DOCUMENTED" standard exists for: a
competent engineer could plausibly have used the same adjusted score on both
sides and only discovered the statistical error in production.

**Alternatives considered**: see spec.md Clarifications session Q4 for the full
option set considered interactively with the user; not re-litigated here.

## R7. State storage for Evaluation Run history, Baseline, and Coverage Exemptions

**Decision**: Store Evaluation Run reports, the current Baseline, and active
Coverage Exemptions as versioned JSON/YAML files committed to the repository
under `eval/data/` (`eval/data/baseline.json`, `eval/data/exemptions.yaml`,
`eval/data/reports/<run-id>.json`), updated by an automated commit from the
merge-queue tier after it reaches a COMPLETE verdict on `main`.

**Rationale**: No new database or service is introduced — consistent with the
constitution's Durable, Inspectable State principle applied at the CI-state
layer: a baseline that lives in git history is itself replayable and auditable
by construction (`git log` on the file is the audit trail), and it needs no new
infrastructure for CI runners (which are otherwise stateless between runs) to
read and write it. It also makes Coverage Exemptions naturally
code-review-visible: an exemption is a diff in a pull request like any other
change, which is exactly the "individually reviewable" property FR-009a/FR-025
require.

**Alternatives considered**:
- *Reuse the existing PostgreSQL checkpointer database.* Rejected: that database
  is scoped to conversation state under an explicit encrypted-serializer
  contract (Constitution Principle V); overloading it with CI/eval bookkeeping
  that has nothing to do with guest conversations blurs a boundary the
  constitution draws deliberately, and CI runners would need network access to a
  long-lived database instance, which the cloud-agnostic, zero-live-dependency
  gate requirement (FR-013) is specifically trying to avoid.
- *CI-platform-native artifact/cache storage* (e.g., GitHub Actions cache).
  Rejected: ties baseline continuity to one CI platform's proprietary storage
  mechanism, working against this feature's own cloud/platform-agnostic intent
  even though the concern here is CI-platform rather than cloud-vendor lock-in.

## R8. CI mechanism for the three gate tiers

**Decision**: GitHub Actions workflows — a `pr-gate.yml` triggered on pull
request events (PR tier), a `merge-queue-gate.yml` triggered on GitHub's
`merge_group` event (merge-queue tier), and a `nightly.yml` triggered on a
`schedule` (nightly tier). Each workflow's job is a thin trigger/orchestration
wrapper; all actual gate logic (scoring, gate-type evaluation, verdict
computation) lives in `eval/` and is invoked identically by `python -m
eval.runner --tier <tier>` whether run in CI or on a laptop, satisfying FR-013/
SC-006's requirement that the verdict computation itself carries no CI-platform
or cloud dependency.

**Rationale**: This repository has no CI at all today, and GitHub Actions is the
natural choice for a GitHub-hosted repository; the design keeps the platform
dependency confined to the thin trigger layer so a future migration to a
different CI platform only means rewriting the workflow YAML, never the gate
logic itself.

**Alternatives considered**:
- *A platform-agnostic CI abstraction from day one* (e.g., a Makefile-only
  design with no workflow files, deferring the CI platform choice indefinitely).
  Rejected: the spec's own tiering answer (Q1) explicitly describes a "PR gate"
  and a "merge queue" — concepts that need a real trigger mechanism to exist at
  all; deferring the choice would leave FR-019 unimplementable this phase for no
  portability gain, since the runner command underneath is already
  platform-neutral.

## R9. Saga suite formalization

**Decision**: Introduce `eval/saga_suite.py` as a thin registry mapping each
named Saga Failure Case (domain, failing step, expected recovery) to the existing
pytest test function(s) in `agents/tests/test_partial_failure_recovery*.py` and
friends that already exercise it, plus a small number of new cases to fill gaps
the registry surfaces. The runner executes the mapped tests and reports each
permutation individually (SC-003's "not collapsed into a single pass/fail line"),
rather than rewriting the existing, working saga tests from scratch.

**Rationale**: The existing tests are real, already passing coverage
(confirmed in Phase 6 handoff's verification section) — the gap is a formal,
named, individually-reported permutation matrix on top of them, not the
underlying test logic itself.

**Alternatives considered**:
- *Rewrite all saga tests from scratch inside `eval/`.* Rejected: would discard
  working, already-verified coverage for no correctness gain, and risks
  reintroducing bugs the existing tests already caught during Phase 6's
  convergence follow-up.
