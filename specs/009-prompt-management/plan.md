# Implementation Plan: Prompt Management — Git-First Prompt Registry with Langfuse Mirror

**Branch**: `009-prompt-management` (git branch in use: `prompt-management-langfuse`) | **Date**: 2026-09-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/009-prompt-management/spec.md`

## Summary

Move the three hard-coded prompt families (six templates) out of Python into version-controlled
YAML that stays canonical, mirror them into the already-running Langfuse as a versioned
registry, and stamp every in-scope model call with the identity (name, version, sha256, source)
of the prompt it used. Production can only ever serve content whose hash is in a build-time lock
and not on a revocation list; anything else falls back to the bundled Git copy. Variables the
policy depends on (dietary vocabularies) are injected by code and fail closed. Judge prompts
are pinned by content hash and a judge-prompt change is a hard `BASELINE_STALE` gate failure
cleared only by an automated rebaseline commit on the same PR. Approval follows the policy: owner
groups gate template changes, the dietary enumerations and the judge prompts.

**Technical approach (from research.md):**

- A new cross-cutting `prompts/` package owns schema, canonical hashing, resolution, the lock,
  lint, and a single CLI (`python -m prompts.sync`). It is the **only** place `langfuse` is
  imported (ADR-053), preserving Principle II's "no agent holds a vendor SDK".
- The Langfuse SDK 4.15.x is used for the **prompt API only**, constructed with
  `tracing_enabled=False`, so ADR-051's raw OTel exporter remains the sole span exporter and
  nothing is double-exported (research R-02).
- Templates are compiled by our own strict `{{name}}` compiler for **both** sources, so Git and
  registry paths render identically and unrendered/undeclared variables are detectable (R-04).
- Lineage is emitted as `resortflow.prompt.*` attributes **only** (ADR-052 D5, option ii, decided
  2026-09-26): no Langfuse prompt-link keys, no propagation span processor, and no claim that the
  Langfuse UI links a generation to its prompt version (research R-03).
- Two facts found during research reshape the spec and are recorded as amendments below
  (S-1 … S-7): the registry exposes **no author and no hash**; and today's gate never exercises
  the dining intent prompt, so FR-035 needs a new deterministic intent-extraction metric.

## Technical Context

**Language/Version**: Python ≥ 3.11 (`pyproject.toml`; CI pins 3.11)

**Primary Dependencies**: `langfuse>=4.15,<5` (currently listed unpinned; installed 4.15.2),
`pyyaml`, `pydantic>=2` (all already declared), `opentelemetry-*` (already declared). No new
runtime dependency.

**Storage**: Files — `prompts/templates/*.yaml`, `prompts/prompts.lock.json`,
`prompts/revocations.json`, `prompts/owners.yaml`, `eval/data/baseline.json` (extended).
Optional runtime registry: Langfuse (`langfuse/langfuse:3` in compose; SDK v4 supports
self-hosted ≥ 3.63.0 — research R-01). No new database.

**Testing**: `pytest` (+ `hypothesis` for hash-stability properties, already a dev dependency).
Registry behaviour tested against a fake adapter; one opt-in integration marker runs against
the local Langfuse (quickstart scenarios).

**Target Platform**: Linux CI + Docker (production image bakes the lock); Windows/macOS local dev.

**Project Type**: Library + CLI inside the existing monorepo (cross-cutting package).

**Performance Goals**: resolve ≤ 5 ms median on a cache hit (SC-009); fetch timeout 2 s;
cache TTL 60 s (SDK default, stale-while-revalidate — R-01).

**Constraints**: offline-capable with `PROMPT_SOURCE=git` (default); a Langfuse or telemetry
outage MUST NOT fail a turn; attribute names only in `observability/`; no real client names.

**Scale/Scope**: 6 templates / 3 families; 2 tenants; 7 CLI subcommands (validate, push, lock,
verify, promote, revoke, codeowners); 3 workflows added, 2 modified. Registry-to-Git
`pull` is deferred (2026-09-26).

**Unresolved unknowns**: none. (The former T001 spike was withdrawn on 2026-09-26: ADR-052 D5 is
decided as option (ii), and the one remaining SDK question — that a tracing-disabled client is inert —
is a non-blocking unit test, FR-031.)

## Constitution Check

*GATE: passed before Phase 0; re-checked after Phase 1 design (below).*

| Principle / standard | Assessment | Evidence |
|---|---|---|
| I. Ambiguity-based routing | **Pass.** No LLM added to any flow; existing model call sites unchanged in intent. | The plan changes *where the prompt text lives*, not which flows call a model. Rule-based fallback (`RuleBasedIntentExtractor`) untouched. |
| II. Governed integration surface | **Pass, with a guard.** Agents never import a vendor SDK; `langfuse` is imported only under `prompts/`. | A repo test fails if `import langfuse` appears outside `prompts/` (FR-002 corollary, ADR-053). The registry is configuration infrastructure, not an agent tool. |
| III. Two-phase mutation | **N/A.** No guest-facing external mutation. Registry writes are developer/CI tooling. | `push`/`promote` are CLI operations, never reachable from a graph node. |
| IV. Policy is code, not prompt | **Pass — this is the feature's core.** | Dietary vocabularies injected from `DietaryRestriction`/`DietarySeverity`; required vars declared at the call site; missing/unrendered → reject → Git copy (FR-007–FR-012); literal-enum lint (FR-028e); jurisdiction wording never generated (FR-012e, ADR-034); tenant data only via closed typed model (FR-012b). |
| V. Durable, inspectable state | **Pass.** | Prompt name/version/sha256/source are span attributes on every in-scope call; Git history + the lock make any historical hash reproducible. No `ResortFlowState` key is added (would need an ADR-023-style amendment; deliberately avoided). |
| VI. Declared model tier | **Pass, after amendment S-1.** Runtime tier stays static per node, declared in code. `model_tier` is **not** a prompt-config key. | A registry-editable tier would allow silent runtime demotion, which Principle VI forbids. Config keys are limited to `temperature` and `response_format`. |
| Dev standard: EVERY DECISION DOCUMENTED | **Pass.** | ADR table below; ADR-052 drafted with this plan. |
| Dev standard: TEST BEFORE ADVANCE | **Pass.** | Saga permutations not touched; tenant-isolation and fail-closed tests are mandatory categories here (tasks). |
| Dev standard: OBSERVABILITY NOT OPTIONAL / no attribute names outside module | **Pass; B-1 resolved.** | Exactly five `resortflow.prompt.*` constants are added, in `observability/attributes.py` and nowhere else; no `langfuse.observation.prompt.*` constants. |
| Dev standard: NO REAL CLIENT NAMES | **Pass.** | Only MRI / `MGV` / `MHB`. |
| Workflow: file-disjoint tracks | **Pass with an integration commit.** | Cross-cutting edits (observability, AGENTS.md track table, CODEOWNERS) land first in one commit. |
| Workflow: CI gate — degraded prompt MUST fail CI | **Pass, requires S-5.** | Dining prompt is not exercised by today's gate; new `intent_extraction_accuracy` / `dietary_recall` metrics close that. |
| Workflow: spec-kit loop, `analyze` mandatory | **Pass.** | Next: `/speckit-tasks` → `/speckit-analyze`. |

**Post-design re-check:** unchanged — no violations; **Complexity Tracking** below is empty by
design. The one thing that *looks* like a Principle II tension (a vendor SDK in the codebase) is
resolved by confinement to `prompts/`, not waived.

## Decisions taken here (deferred items from clarification)

| Deferred item | Decision | Why (details: research.md) |
|---|---|---|
| **Rebaseline job trigger** | **Label-triggered** (`rebaseline` PR label) plus `workflow_dispatch`. The PR gate's `BASELINE_STALE` failure message tells the author to add the label. | A merge-queue-tier run is a full 3× judge run — real token cost. Auto-running on every push to a WIP judge-prompt PR wastes it, and an explicit label keeps intent visible. Rejected: auto on path change (cost); manual-only (loses the guided path). |
| **Bot commit & retrigger** | Commit with a **fine-grained PAT** stored as the `REBASELINE_TOKEN` secret, not `GITHUB_TOKEN` (decided 2026-09-26; no GitHub App). | Commits pushed with `GITHUB_TOKEN` do not retrigger workflows, so the PR gate would never re-run on the rebaselined branch. Loop-proof: the job is a no-op once hashes match. |
| **Push and registry-authored versions** | With `pull` deferred there is no import path, so registry edits are scratch. Push never blocks; it prints any registry version it moves `staging` off (FR-022a). The exit-4 / `--discard` / tracking-issue design is withdrawn. | Nothing can be lost that Git does not already own; the notice keeps the supersession visible without inventing a workflow for an import tool that does not exist. |
| **Judge user-template grouping** | **One chat prompt per metric** — 5 judge templates (`system`+`user` messages each), grouped into two families by name prefix. Names use underscores, never `/`. | Per-metric hashes let `BASELINE_STALE` name exactly which metric's baseline is invalid. Langfuse folder names (`a/b`) have a known prompt-trace-association bug (langfuse issue #9354). Rejected: Langfuse "prompt composability" (server-side composition breaks local hashing/lock). |

**Template inventory (final):**

| Family | Prompt ID | Policy |
|---|---|---|
| dining | `dining_intent_extraction` | label (`PROMPT_LABEL`), allow-list enforced, owner = compliance |
| eval_retrieval_judge | `eval_retrieval_judge_context_recall`, `…_context_precision`, `…_faithfulness`, `…_answer_relevance` | exact (hash), owner = eval-track (compliance as sole owner for a rubric that scores dietary/allergen criteria) |
| eval_trajectory_judge | `eval_trajectory_judge_plan_quality` | exact (hash), owner = eval-track |

## Spec amendments this plan requires

These are facts discovered during planning that contradict or refine the spec. **Status
(2026-09-26): S-1, S-2, S-5, S-6 and S-7 were applied to the spec (recorded in its
"Session 2026-09-26" clarifications); S-3 and S-4 are moot because `pull` was deferred.**

| # | Spec text | Finding | Proposed amendment |
|---|---|---|---|
| **S-1** | FR-001: template config includes "model tier" | Principle VI: runtime tier is static per node, declared in code; a registry-editable tier permits silent demotion. | Remove `model_tier` from template config. Allowed config keys: `temperature`, `response_format` only; unknown keys → reject. |
| **S-2** | FR-016/FR-017: judge prompts "resolve by exact version and content hash" | Registry version numbers differ per registry (local vs production); only the hash is portable. | Hash is authoritative. `exact`-policy prompts resolve from Git always (deterministic, CI-safe); registry version is informational, looked up by hash for lineage only. |
| **S-3** | FR-028g: co-author "where the registry exposes that identity" | The Prompt API exposes no author field. | **Moot** — `pull` and editor credit deferred. Recorded so the finding survives for the future work. |
| **S-4** | FR-028b: base retained on UI edit | Docs do not say a marker survives a UI edit. | **Moot** — `derived_from` deferred with `pull`. |
| **S-5** | FR-035 / SC: degraded prompt fails the gate | `eval.runner` never exercises `AzureIntentExtractor`; a degraded dining prompt would pass today. | Add deterministic metrics `intent_extraction_accuracy` and `dietary_recall` (no LLM judge) over a labelled case set, tenant-stratified, with floors. `dietary_recall` floor is 1.0 (Principle IV: every stated restriction must be extracted). |
| **S-6** | FR-014b: revoking the live hash needs a promoted replacement | "Currently serves" is only knowable with registry access. | Two layers: (1) deterministic CI invariant `revoked ∩ template hashes = ∅` (bundled fallback is never revoked, so a servable prompt always exists); (2) `promote`/drift check that production ∈ revoked ⇒ promotion pending. Break-glass additionally requires no new approved hashes in the same PR. |
| **S-7** | Scope table prompt IDs `eval_retrieval_judge` / `eval_trajectory_judge` | Each is a *family* of 4 / 1 templates. | IDs become the six names in the inventory above; the spec's family names remain the grouping. |

## Blockers — raised, not decided

| # | Blocker | Why it is yours to approve |
|---|---|---|
| **B-1** | **RESOLVED.** `observability/attributes.py` is a frozen Phase-0 vocabulary. Exactly five `resortflow.prompt.{name,version,sha256,source,rejected_reason}` constants are added and `specs/001-contracts-foundation/contracts/telemetry-attributes.md` is amended; `resortflow.prompt.sha256` is span-only, never a metric dimension. No `langfuse.observation.prompt.*` constants (D5, option ii). | Decided 2026-09-26. |
| **B-2** | **RESOLVED (snapshot).** Compliance approval attaches to a snapshot of the `DietaryRestriction`/`DietarySeverity` members plus their contract test, not to the whole of `graph/models.py`. No enum is moved or modified. FR-037 was reworded. | Decided 2026-09-26. |
| **B-3** | **Accepted by default** — add "Cross-cutting — Prompts → `prompts/`" to the AGENTS.md track table in the integration commit. | Not explicitly asked; veto welcome. |
| **B-4** | **RESOLVED (single maintainer).** Owner groups are documented synthetic groups in `prompts/owners.yaml`; CODEOWNERS is generated; the two-member rule is checked against `owners.yaml`. "Require Code Owner review" cannot be satisfied by a single author, so enforcement is by CI checks and is *demonstrable, not activated* — ADR-052 says so. | Decided 2026-09-26. |
| **B-5** | **CLOSED (withdrawn 2026-09-26).** ADR-052 D5 was pre-decided as option (ii) (`resortflow.prompt.*` lineage only), so the blocking spike is gone. It is replaced by a non-blocking unit test that a tracing-disabled Langfuse client does not touch the global tracer provider (FR-031). | Decided 2026-09-26. |

## Project Structure

### Documentation (this feature)

```text
specs/009-prompt-management/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── prompt-template-schema.md
│   ├── lock-and-revocations.md
│   ├── prompts-cli.md
│   ├── registry-runtime.md
│   ├── telemetry-prompt-attributes.md
│   └── ci-workflows.md
├── checklists/requirements.md
└── tasks.md            # /speckit-tasks — not created here
```

### Source code (repository root `resortflow/`)

```text
prompts/                              # NEW cross-cutting package (B-3)
├── __init__.py
├── schema.py                         # PromptTemplate (pydantic) + config-key allow-list + variable extraction
├── canonical.py                      # canonical serialization + sha256 over messages + config
├── registry.py                       # resolve(), ResolvedPrompt, modes, fail-closed checks
├── compiler.py                       # strict {{name}} renderer shared by both sources
├── langfuse_adapter.py               # ONLY file importing langfuse (tracing_enabled=False)
├── tenant.py                         # TenantProfile — closed, typed, bounded
├── lint.py                           # dietary-enum literals, tenant/regulator identifiers, exemptions
├── lock.py                           # lock + revocations load/verify/diff invariants
├── codeowners.py                     # generate/check from template owner fields
├── sync.py                           # CLI: validate|push|lock|verify|promote|revoke|codeowners
├── templates/                        # 6 YAML templates (canonical)
├── owners.yaml                       # owner groups → team slug + ≥2 named members
├── snapshots/dietary_enums.json      # member snapshot of DietaryRestriction/DietarySeverity (compliance-owned, FR-037)
├── lint_exemptions.yaml              # reviewed, per-token per-prompt, with reason
├── prompts.lock.json                 # approved hashes (append-only)
├── revocations.json                  # tombstones (append-only)
└── tests/

observability/                        # B-1 — integration commit only
├── attributes.py                     # + 5 resortflow.prompt.* constants (no langfuse.* constants)
└── telemetry.py                      # + start_generation_span() only (no propagation processor)

agents/dining/nodes/intent_parser.py  # _SCHEMA_INSTRUCTIONS → registry.resolve(); declares required vars in code
agents/dining/tests/                  # contract tests: template vars ↔ enums; enum snapshot (FR-037)

eval/
├── retrieval_scorer.py               # 4 templates via registry (exact)
├── trajectory_scorer.py              # 1 template via registry (exact)
├── intent_scorer.py                  # NEW: intent_extraction_accuracy, dietary_recall (deterministic)
├── judge.py / models.py              # JudgeModelRecord + prompt fields; MetricScore.tenant_id
├── baseline.py / gate.py             # judge_prompt_hashes; BASELINE_STALE; per-tenant floors/deltas; coverage-by-tenant
├── runner.py                         # --check-baseline-prompts; tenant reporting
└── data/{retrieval_cases.yaml, intent_cases.yaml}   # tenant-tagged; rebalanced

.github/
├── CODEOWNERS                        # generated, last-match-wins; default /prompts/ -> compliance (FR-042)
└── workflows/{pr-gate.yml (mod), merge-queue-gate.yml (mod), rebaseline.yml, prompt-sync.yml, prompt-drift.yml}

docs/adr/052-git-first-prompt-registry-langfuse-mirror.md   # drafted with this plan (Proposed)
Makefile / .env.example / pyproject.toml / Dockerfile / docker-compose.yml   # prompts-sync target; PROMPT_* vars; langfuse pin; COPY prompts + PROMPT_*/LANGFUSE_HOST env for resortflow-orchestrator (T005a)
```

**Structure Decision:** a single new cross-cutting package plus surgical edits in tracks C, E and
F. Everything that touches a vendor SDK, a hash, or the lock is inside `prompts/`; call sites see
only `resolve(...) -> ResolvedPrompt`. Track-disjointness for `/speckit-tasks`: `prompts/` (new),
`eval/` (E), `agents/dining/` (C), `.github/`+`Makefile` (F) are disjoint after the integration
commit; `[P]` markers are valid across them, never within `prompts/`'s tightly-coupled core.

## Phase ordering (input to `/speckit-tasks`)

| Phase | Content | Gate |
|---|---|---|
| **0 — (withdrawn)** | ~~T001 spike~~ — D5 pre-decided (option ii); replaced by the non-blocking tracing-disabled test (FR-031) in Phase 2 | — |
| **1 — Integration commit** | Five `resortflow.prompt.*` constants + amend the 001 contract; AGENTS.md row (B-3); `langfuse` pin; compose image tag pin; `.env.example` | B-1 resolved; B-3 accepted by default |
| **2 — Core (US1, P1)** | `prompts/` schema, canonical, registry, tenant, lint(core); dining migration; fail-closed tests; telemetry generation span | Fail-closed matrix green |
| **3 — Judges + gate (US2, P2)** | judge templates via registry; `JudgeModelRecord`/baseline extension; `BASELINE_STALE`; `intent_scorer`; tenant stratification; rebalanced cases | Degraded-prompt check fails the gate |
| **4 — Sync, lock, revocation (US3, P3)** | `sync.py`, lock, revocations, promote, drift; workflows | Timing-hole scenario passes |
| **5 — Ownership (FR-036–041)** | Generated CODEOWNERS, `owners.yaml` checks, enum-snapshot ownership, break-glass merge-path invariant (L6) | Enforcement is CI-only in this solo repo (demonstrable, not activated). There is no audit job |

Every task must declare a build-time model tier (Principle VI). Tasks touching evaluation logic,
tenant isolation or policy enforcement are `[CRIT]` per `docs/models.md`.

## Risk register

| ID | Risk | Mitigation / owner of the answer |
|---|---|---|
| **R-A** | *(Withdrawn 2026-09-26 — no Langfuse UI-linkage claim is made, so nothing depends on the link rendering.)* | — |
| **R-B** | *(Withdrawn 2026-09-26 — `derived_from` survival is moot with `pull` deferred.)* | — |
| **R-C** | `Langfuse(tracing_enabled=False)` is process-singleton-per-public-key; a later `Langfuse()` with tracing on in the same process would inherit that state. | Only `prompts/langfuse_adapter.py` constructs a client; test asserts no other `langfuse` import; ADR-053. |
| **R-D** | CODEOWNERS cannot express "engineering + incident label, owner ack later" (FR-040). | Revocations live in their own file owned by *both* groups; the owner's after-the-fact ack is a documented manual convention (no audit job — dropped 2026-09-26); CI invariant L6 still prevents new content via break-glass. |
| **R-E** | Per-tenant floors on thin strata are noisy (dining 20 MGV / 5 MHB; retrieval untagged). | FR-021c coverage failure below a minimum: `pr=2`, `merge_queue=5`, `nightly=5` cases/tenant/metric (tunable); rebalance data in Phase 3. |
| **R-F** | Literal-enum lint collides with everyday words (`preference`, `egg`, `fish`). | Exemptions file: per-token, per-prompt, with reason, reviewed by the compliance owner. |
| **R-G** | Bot commits with `GITHUB_TOKEN` do not retrigger workflows. | Fine-grained PAT in `REBASELINE_TOKEN` (Decisions table). |
| **R-H** | `langfuse/langfuse:3` is a floating tag. | Pin to a tested `3.x` tag in compose during the integration commit. |
| **R-I** | In a solo repository, ownership and break-glass rules cannot be enforced by review, so they read as ceremony. | ADR-052 states the limit plainly; the mechanism is exercised by tests with fake reviewers and synthetic owner groups, and is described as demonstrable rather than activated. |
| **R-J** | `dietary_recall` at floor 1.0 on a nondeterministic model can block every merge or train the team to ignore it (floor uses mean − 1σ over 3 repeats). | T053a measures run-to-run miss rate before the floor becomes blocking; until then the metric is report-only (FR-035a). |
| **R-K** | The root `Dockerfile` copies an explicit directory list; without `COPY prompts` the orchestrator and reaper fail at import once US1 lands. | T005a adds the COPY and the `langfuse` pin; T040 builds and starts the image before US1 is called done. |

## Complexity Tracking

No constitution violations to justify. Deliberate simplifications recorded instead: no
composability, no folders, no runtime revocation channel, no per-tenant variants, no `ResortFlowState`
change.

## Architecture Decision Records *(mandatory — ResortFlow project override)*

| ADR | Decision | Status |
|-----|----------|--------|
| [052](../../docs/adr/052-git-first-prompt-registry-langfuse-mirror.md) | Git-first prompt registry mirrored to Langfuse; D1–D11 (source of truth, runtime allow-list, fail-closed variables, judge pinning + `BASELINE_STALE`, `resortflow.prompt.*` lineage only, label promotion, future work, revocation, pull deferred, tenant-agnostic templates, ownership + break-glass merge path). Supersedes Deep-Dive C6. | [x] drafted / [x] accepted 2026-09-26 (D5 decided) |
| 053 | Registry client boundary: SDK confined to `prompts/` with `tracing_enabled=False`; own `{{}}` compiler for both sources; canonical hash definition; `exact` prompts resolve from Git; no folders / no composability. | [ ] drafted / [ ] complete — drafted as an early task |
| 054 | Rebaseline automation: label-triggered PR-branch job, fine-grained PAT, loop-proofing; amends ADR-045 (second automation writer) and ADR-046 (extra workflow). | [ ] drafted / [ ] complete — drafted with the workflow task |

Amendments (not new ADRs): ADR-045 (addendum: PR-branch baseline writes), ADR-044 (addendum:
judge *prompt* pinning alongside judge model), ADR-042 (addendum: per-tenant floors, minimum-case coverage, new dining metrics), Deep-Dive C6 (pointer to ADR-052; FR-033), spec
001 `telemetry-attributes.md` (B-1).

## Validation log

### US1 checkpoint (T040) — 2026-09-27

| Check | Result |
|---|---|
| `pytest` US1 suites (`prompts/tests`, dietary contract + golden, extended intent-parser tests, import boundary, prompt telemetry) | **176 passed, 1 skipped** (the skip is the deliberate T050a placeholder, US2) |
| Full CI-equivalent run: `pytest prompts/tests agents/dining/tests eval/tests tests/test_langfuse_import_boundary.py tests/test_prompt_telemetry.py -q -m "not integration"` (T038a gate condition) | **257 passed, 1 skipped, 5 xfailed, 3 failed.** The 3 failures are the same 3 that fail on the untouched baseline (`eval/tests/test_telemetry.py::test_set_payload_*`): this Windows machine's Application Control policy blocks a spaCy DLL. The CI step downloads `en_core_web_sm` and runs on Linux. Baseline before this feature: 106 passed, 3 failed. |
| `mypy` (strict) on `prompts` and `observability` | clean (14 files) |
| `ruff` on all files owned by this feature | clean (pre-existing findings in `telemetry.py`/`graph/api.py` untouched) |
| `python -m prompts.sync validate` | exit 0 on the real template; exit 1 on a forbidden `model` config key; exit 1 with 3 findings on literal dietary values + a tenant id |
| SC-001: `grep _SCHEMA_INSTRUCTIONS agents/` | empty (only a docstring mention inside the golden test) |
| SC-009: median `resolve()` overhead | **0.42 ms** median, 0.83 ms p99, against a 5 ms budget |
| Quickstart Scenario 0 (tracing-disabled client is inert, real SDK) | passes |
| Quickstart Scenario 1 (byte-identical migration, lineage span) | passes (golden + stubbed-client span test) |
| Quickstart Scenario 3 (fail-closed matrix) | passes (automated). The manual Langfuse-UI edit variant was not run: no Langfuse. |
| Quickstart Scenario 2 (registry mode against a live Langfuse) | **not run** — no Langfuse credentials wired up for this exercise; the local Langfuse container never received a project token |
| Container: build and start `resortflow-orchestrator`, healthcheck | **Passes** (2026-09-27, after the Docker daemon came back up). First build surfaced a real gap: T005a's own instruction to pin `langfuse` in the Dockerfile's `pip install` line had not actually been applied — the line still read bare `langfuse`. Fixed, then rebuilt clean (`--no-cache`, after a transient pip hash-mismatch on the first pinned attempt turned out to be a cache/network glitch, not a real conflict — a plain retry succeeded) and recreated the container from the new image: `docker inspect --format '{{.State.Health.Status}}'` reports `healthy` within ~20s. |

### US2 checkpoint (T062) — 2026-09-27

| Check | Result |
|---|---|
| `pytest eval/tests prompts/tests` | **270 passed, 5 xfailed, 3 failed.** Same 3 environmental spaCy-DLL failures as the US1 baseline (`test_telemetry.py::test_set_payload_*`); nothing new. |
| `mypy eval prompts` (strict) | clean (26 files) |
| `ruff` on every file this story touched | clean |
| Quickstart Scenario 9 (tenants, SC-016) | `pytest eval/tests -k tenant` — **28 passed** (`test_tenant_gate.py`, `test_baseline_stale.py`'s tenant-adjacent cases). A wording change that drops MHB below its floor while the aggregate stays above fails the gate (`METRIC_FAILURE`, not masked by the aggregate); an MHB stratum below the minimum-case threshold reports a coverage failure, never a pass. |
| Quickstart Scenario 5 (judge-prompt change is a hard gate) | Validated via `prompts/tests/test_baseline_stale.py::test_a_one_character_edit_to_a_real_judge_template_makes_the_baseline_stale`, which does exactly step 1 for real (edits a copy of `eval_retrieval_judge_faithfulness.yaml` by one character and re-resolves it) and asserts the resulting hash differs and reports `BASELINE_STALE`; `eval.runner.main`'s stderr hint (prompt id + "add the `rebaseline` label") is asserted in `test_the_runner_exits_1_and_prints_the_rebaseline_hint_on_a_stale_baseline`. **Not run**: the actual `python -m eval.runner --tier pr` CLI invocation end-to-end (would call the live Foundry judge configured in `.env`, and step 3 needs a real PR + the `rebaseline.yml` workflow, GitHub-only). |
| Quickstart Scenario 10 (a degraded prompt fails CI, FR-035) | Validated offline via `eval/tests/test_degraded_prompt_gate.py`: a stub model client keyed on whether the rendered system prompt still asks for dietary data reproduces the failure mode directly; `dietary_recall` drops from 1.0 to 0.0 and the gate verdict goes to FAIL when the metric is blocking (`DIETARY_RECALL_BLOCKING`, currently `False` pending T053a's calibration — see below). **Not run**: the live-credential `☁` variant against the real Foundry judge. |
| SC-002 (100% of in-scope judge calls carry prompt lineage) | `eval/judge.py`'s `JudgeClient.complete` now requires `prompt: ResolvedPrompt` as a non-optional keyword; `mypy eval` (strict) would fail on any call site that omitted it. All four call sites (the four retrieval metrics via `_score_with_judge`, and `score_plan_quality`) pass it. |
| SC-018 / T053a | **Done** (2026-09-27, live Foundry judge). First pass: 20 runs, deterministic (sigma 0.0000), but 2 of 24 cases missed a stated restriction on every run — a genuine, systematic finding, not a flake: both cases stated an unqualified severity ("shellfish allergy", no qualifier), and the real judge consistently resolves that to `allergy_severe` (the safer default under Principle IV), while the fixture had assumed the rule-based extractor's own `allergy_moderate` default. Fixed the two ambiguous fixture cases (one now says "moderate shellfish allergy" explicitly; the other's expected severity was corrected to `allergy_severe`), confirmed with 5 more runs: `dietary_recall` 1.0000, sigma 0.0000, zero misses. `DIETARY_RECALL_BLOCKING` flipped to `True`. Full writeup in research.md R-16. |
| Full suite re-check after T053a | `pytest eval/tests prompts/tests` → **273 passed, 5 xfailed, 0 failed.** The 3 `test_telemetry.py` spaCy-DLL failures noted throughout this log turned out to be transient (this machine's Application Control policy), not a real baseline gap — re-run in isolation now passes cleanly (6/6). |

### Findings during T062

* **`_score_intent` (T053/T056) is a live-network call by default** — this repository's `.env` carries real Azure/Foundry credentials for local development, so wiring `AzureIntentExtractor` into `eval.runner`'s tiers made two previously-pure unit tests in `eval/tests/test_runner_scoring.py` start silently hitting a live endpoint on every run (visible as the suite taking ~2–3 minutes instead of seconds). Fixed with an autouse fixture that forces `default_intent_extractor` to raise `IntentExtractorUnavailableError` in that file, matching the existing convention that `build_default_judge_client` is always monkeypatched there; the assertion that no cases were `NOT_EVALUATED` was corrected to expect `["dining_intent_cases"]`, since that is now the correct behaviour with no credentials.
* **The Dockerfile's own T005a instruction had not been applied**: `pip install` still had a bare `langfuse` despite T005a's text saying to pin it. Fixed as part of confirming the healthcheck (T040).

### Findings during implementation

* **Hypothesis found a real bug in the canonical hash (fixed).** Newline normalisation was a single-pass CRLF -> LF replacement, which is not idempotent (CR CR LF), so an LF checkout and a CRLF checkout of the same content could hash differently, violating FR-005. Now universal newlines (CRLF and lone CR -> LF). No hashes existed yet, so nothing to migrate. Regression tests: `prompts/tests/test_canonical_newlines.py`. ADR-053 and research R-05 updated.
* **The Langfuse client is a per-public-key process singleton** (ADR-053's named sharp edge, now observed): a test that shut a client down and then reused the key hung on the second `shutdown()`. `test_adapter_tracing_disabled.py` now uses one key per test and a bounded daemon-thread shutdown.
* **`langfuse/langfuse:3` pinned to `3.225.11`** for both images (digest-identical to the floating tag; compared via Docker Hub because the daemon was offline).
* **CI needs the spaCy model** for three existing tests; the new unit-test step downloads `en_core_web_sm`.
