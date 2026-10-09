# Contract: CI Tier Triggers

**Produced by**: this feature (ADR-046), first CI this repository has.
**Files**: `.github/workflows/{pr-gate,merge-queue-gate,nightly}.yml`.

Per research.md R8, every workflow file is a thin trigger wrapper. The contract
below is what keeps the gate logic itself CI-platform-independent — anyone
running the same underlying command locally gets the identical verdict (SC-006).

| Tier | Trigger | Command | Blocking? |
|---|---|---|---|
| PR | `pull_request` (opened, synchronize) | `python -m eval.runner --tier pr` | Yes — required status check |
| Merge-queue | `merge_group` | `python -m eval.runner --tier merge_queue` | Yes — required for queue entry to complete |
| Nightly | `schedule` (cron) | `python -m eval.runner --tier nightly` | No — report-only, never gates |

## Exit code contract

`eval.runner` MUST map the report's `verdict` field to a process exit code:

- `PASS` → 0
- `FAIL` → 1
- `INCOMPLETE` → 1 if the environment variable `EVAL_INCOMPLETE_BLOCKS=true`
  (set in `merge-queue-gate.yml`, unset/`false` in local runs and
  `pr-gate.yml`'s default), else 0 with a warning printed to stdout (FR-023).

This is the **only** place environment-specific blocking policy is encoded — the
report JSON itself (`eval-report-schema.md`) never varies by environment.

## Baseline write-back

Only `merge-queue-gate.yml`, and only on a run against `main` that reaches
`verdict ∈ {PASS, FAIL}` (never `INCOMPLETE`), triggers the automated commit that
updates `eval/data/baseline.json` (research.md R7, FR-007c's eligibility rule).
PR-tier runs and nightly runs never write to the baseline file.

## Compatibility

Changing which event triggers a tier, or the exit-code mapping above, requires
an ADR — this is the seam a future CI-platform migration would need to
reproduce exactly to stay a "refactor, not a redesign."
