# Phase 1 Data Model: Evaluation & Observability Gate

Entities below extend spec.md's Key Entities section with concrete fields,
relationships, and state transitions. Storage is versioned repo files under
`eval/data/` (research.md R7) — these are the shapes serialized to those files
and to per-run report JSON, not a new database schema.

## Evaluation Run

One execution of a Gate Tier's suite.

| Field | Type | Notes |
|---|---|---|
| `run_id` | string | Unique per run (e.g. commit SHA + tier + timestamp) |
| `tier` | enum: `pr` \| `merge_queue` \| `nightly` | FR-019 |
| `code_ref` | string | Git commit/ref the run executed against |
| `started_at` / `completed_at` | timestamp | |
| `verdict` | enum: `PASS` \| `FAIL` \| `INCOMPLETE` | See Run Verdict below |
| `judge_model` | Judge Model Record | Pinned model used for this run's LLM-judge scoring |
| `metric_scores` | list of Metric Score | |
| `saga_case_results` | list of (Saga Failure Case id → pass/fail) | Only for `merge_queue`/`pr`-scoped subset |
| `budget_flags` | list of Cost/Latency Budget exceedances | FR-005 |
| `active_exemptions` | list of Coverage Exemption ids | Snapshot at run time, SC-009 |
| `not_evaluated_cases` | list of case ids | Populated only when `verdict = INCOMPLETE`, FR-022 |

**Relationships**: has many Metric Score; references one Judge Model Record;
may become eligible as a Baseline (only if `tier = merge_queue` and
`verdict = COMPLETE`-equivalent, i.e. `PASS` or `FAIL`, never `INCOMPLETE`).

## Run Verdict

Not a stored entity on its own — the enum on Evaluation Run — but its transition
rules are load-bearing enough to state explicitly:

```
        all required cases complete
              ┌─────────────┐
   start ────▶│  evaluating │
              └──────┬──────┘
                      │
        ┌─────────────┼─────────────────┐
        │             │                 │
        ▼             ▼                 ▼
   all gates OK   any gate fails   dependency unreachable
        │             │            after FR-021 retries
        ▼             ▼                 ▼
      PASS          FAIL           INCOMPLETE
```

- `PASS` and `FAIL` are only reachable when every required case actually
  completed.
- `INCOMPLETE` is reachable from any point once a required dependency fails
  FR-021's retry budget; it MUST NOT be conflated with `FAIL` (a real quality
  regression) or silently reported as `PASS` (FR-022).
- Whether `INCOMPLETE` blocks the merge is read from environment configuration
  at report time (FR-023), not encoded in the verdict itself — the verdict is
  the same value in every environment; only the blocking *policy* differs.

## Gate Tier

| Field | Type | Notes |
|---|---|---|
| `name` | enum: `pr` \| `merge_queue` \| `nightly` | |
| `time_target` | duration \| null | `~10 min` for `pr`; no fixed ceiling for `merge_queue`/`nightly` (FR-019, Assumptions) |
| `blocking` | bool | `pr`/`merge_queue` block merge; `nightly` never does |
| `scope` | structured | Which categories/subsets run at this tier — see FR-019's per-tier bullet list |

Not persisted as a row; this is configuration, not run-time state.

## Metric Score

| Field | Type | Notes |
|---|---|---|
| `metric_name` | enum | `context_recall`, `context_precision`, `faithfulness`, `answer_relevance`, `tool_selection`, `plan_quality`, `unnecessary_step_rate`, `cost`, `latency` |
| `domain` | enum | dining / stay / entertainment / wellness / gaming |
| `conversation_id` | string | Links to Conversation Record |
| `step_id` | string \| null | Null for conversation-level metrics |
| `gate_type` | enum: `binary` \| `statistical` | Determines which FR-007 branch applies |
| `raw_values` | list of float | 1 value (PR tier) or 3 values (merge-queue tier, FR-007a) |
| `mean` | float | Computed from `raw_values` |
| `variance_adjusted` | float \| null | `mean - stdev`; null when `raw_values` has fewer than 2 samples (PR tier) |
| `judge_model` | Judge Model Record | |

## Judge Model Record

| Field | Type | Notes |
|---|---|---|
| `provider` | string | e.g. `azure-openai` — selected via configuration, never hardcoded in a scorer (FR-013a, ADR-044) |
| `model_id` | string | e.g. `gpt-4.1-2026-XX-XX` — a pinned deployment, never `latest` (FR-009) |
| `tier` | enum | Reuses existing Constitution Principle VI tiers; judge is always `frontier` (research.md R5) |

Embedded on every Metric Score and on Baseline — not a separate stored
collection, to keep "every metric names its judge" (FR-009) structurally
guaranteed rather than joined at read time.

## Saga Failure Case

| Field | Type | Notes |
|---|---|---|
| `case_id` | string | Stable name, e.g. `dining.hold_ok_confirm_fails` |
| `domain` | enum | dining / stay / entertainment (FR-003 — gaming excluded, Assumptions) |
| `failing_step` | enum: `hold` \| `confirm` \| `compensate` \| `none` | `none` = the success-path case |
| `expected_outcome` | string | What correct recovery/completion looks like for this case |
| `backing_test` | string | Existing pytest node id this case wraps (research.md R9) |
| `urgency_tier` | enum: `tier_1` \| `tier_2` | For Coverage Requirement / Exemption window sizing (FR-009a/025) |

## Conversation Record

| Field | Type | Notes |
|---|---|---|
| `conversation_id` | string | |
| `tenant_id` | enum: `MGV` \| `MHB` | |
| `turns` | list of Turn | Each: input, decisions, tool calls, retrieved context, output, cost, latency |
| `source` | enum: `live` \| `evaluated` | Distinguishes a real guest conversation from one generated by the eval suite |

Shared, single source of truth for both the diagnostic view (FR-011) and eval
scoring (FR-012) — not duplicated storage. Built from the existing checkpointer
+ the new `observability/telemetry.py` span data (research.md R3), not a new
mutable state store layered beside them (Constitution Principle V).

## Cost/Latency Budget

| Field | Type | Notes |
|---|---|---|
| `scope` | (domain, step) pair | |
| `metric` | enum: `cost` \| `latency` | |
| `threshold` | float | Planning-level configuration value (spec Assumptions) |

## Baseline

| Field | Type | Notes |
|---|---|---|
| `source_run_id` | string | The Evaluation Run it was captured from |
| `judge_model` | Judge Model Record | |
| `per_metric_mean` | map[metric_name → float] | Raw means, for the delta check |
| `per_metric_stdev` | map[metric_name → float] | For the `2 × σ_baseline` margin (FR-007b) |
| `captured_at` | timestamp | |

**Eligibility rule** (FR-007c): a run may become the Baseline only if
`tier = merge_queue` and `verdict ∈ {PASS, FAIL}` (never `INCOMPLETE`). Stored at
`eval/data/baseline.json`, overwritten each time a newly eligible main-branch run
completes (research.md R7).

**Invalidation**: if a current run's `judge_model.model_id` differs from the
stored Baseline's, the delta check for every affected metric reports
`BASELINE_INVALID` instead of computing a comparison, until a new Baseline is
captured under the new judge model (FR-007c).

## Coverage Requirement

Not persisted as its own record — derived per-run from the diff (research.md
R2). Represented in a report only as: `(surface_id, urgency_tier, covered: bool)`.

| Field | Type | Notes |
|---|---|---|
| `surface_id` | string | e.g. `dining.hold_table` (tool), `graph.supervisor.recovery` (saga), `policy.dietary` (rule) |
| `urgency_tier` | enum: `tier_1` \| `tier_2` \| `other` | Drives Coverage Exemption window (FR-025) |

## Coverage Exemption

| Field | Type | Notes |
|---|---|---|
| `exemption_id` | string | |
| `surface_id` | string | The Coverage Requirement it bypasses |
| `reason` | string | Required justification text |
| `tracking_issue` | URL/string | Required link (FR-009a) |
| `created_at` | timestamp | |
| `expires_at` | timestamp | `created_at + 7d` (tier_1) / `+14d` (tier_2) / `+30d` (other) — FR-025 |
| `status` | enum: `active` \| `cleared` \| `void` | See lifecycle below |
| `request_sequence` | int | 1st, 2nd, 3rd+ request against the same `surface_id` (FR-029) |
| `adr_ref` | string \| null | Required, non-null when `request_sequence >= 3` |

**Lifecycle**:

```
   filed ──────────────▶ active
                            │
              ┌─────────────┼──────────────┐
              │                             │
   coverage gate passes            expires_at reached,
   for this surface                coverage still missing
              │                             │
              ▼                             ▼
          cleared                         void
   (FR-026 — issue close alone      (FR-027 — fail closed,
    does NOT trigger this)           no grace period, no
                                      auto-renewal)
```

A `void` exemption cannot be reactivated (FR-028) — a new exemption must be filed,
which increments `request_sequence` for that `surface_id` and, at 3, requires
`adr_ref` to be non-null before it can be `active` (FR-029).
