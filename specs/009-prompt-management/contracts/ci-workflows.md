# Contract: CI workflows

Extends `specs/007-eval-observability-gate/contracts/ci-tier-triggers-contract.md` (ADR-046):
workflows decide *when*; all logic is in `prompts/` and `eval/`.

## Modified

### `pr-gate.yml` (`pull_request`)

Adds steps **before** `python -m eval.runner --tier pr`:

| Step | Command | Fails when |
|---|---|---|
| Validate templates | `python -m prompts.sync validate` | V1–V9 |
| Unit tests | `pytest <feature test list> -q -m "not integration"` (list in T038a: `prompts/tests`, `agents/dining/tests`, `eval/tests`, the two root `tests/` files — directory-level, so later stories' tests are picked up automatically) | any failure — this is what makes the FR-037 snapshot test, the dietary contract/golden tests and the failure matrix fail CI |
| Lock invariants | `python -m prompts.sync lock --check` (base = merge-base with `origin/main`) | any invariant L1–L7, L2b |
| CODEOWNERS/owners | `python -m prompts.sync codeowners --check` | drift or group < 2 members |
| Registry drift (best effort) | `python -m prompts.sync verify` | exit 3 downgraded to a warning |
| Baseline stale hint | (inside `eval.runner`) | `BASELINE_STALE` prints "add the `rebaseline` label" |

### `merge-queue-gate.yml` (`merge_group`)

Behaviour unchanged except two added steps before the gate: the PR gate's unit-test step (T038a) and `python -m prompts.sync lock --check
--base ${{ github.event.merge_group.base_sha }}`, so the lock invariants behave identically in pull
requests and in the merge queue. Still the only writer of `baseline.json` on `main`. No promote
here — promotion must follow the merge, not precede it.

## New

### `prompt-sync.yml` - `push` to `main` touching `prompts/templates/**`

1. `prompts.sync push` (staging). It never blocks; any registry version it moves `staging` off is
   printed in the job log (FR-022a).
2. `prompts.sync promote --require-pass ${{ github.sha }}` - succeeds only if that commit's
   `merge-queue-gate` check concluded `success` (merge-queue branches fast-forward `main`, so the
   SHA matches; verified with the check-runs API).

Permissions: read contents, `checks: read`; registry credentials as secrets. **When the `LANGFUSE_HOST` secret is empty (no shared registry — the only registry is the local docker one) this job exits 0 with a notice instead of failing; read the secret into an env var and gate later steps on a step output, because the `secrets` context is not available in `if:` conditions.** (The earlier exit-4 /
`discard` dispatch input / tracking-issue design was withdrawn with `pull`, 2026-09-26.)

### `rebaseline.yml` — `pull_request: [labeled]` (label `rebaseline`) + `workflow_dispatch`

1. Check out the PR head ref with `REBASELINE_TOKEN` (a fine-grained PAT).
2. `python -m eval.runner --tier merge_queue` (Azure judge secrets; eligibility rule unchanged).
3. If `eval/data/baseline.json` changed **and** the run was COMPLETE → commit as the bot identity,
   push to the PR branch. Otherwise fail visibly (a non-COMPLETE run never commits).
4. `concurrency: rebaseline-${{ github.event.pull_request.number }}`, cancel-in-progress.

Loop-proof: after the commit the judge-prompt hashes match the baseline, so a further run
changes nothing. The token-authenticated push fires `synchronize`, re-running the PR gate and (with
"dismiss stale approvals") requiring the eval-track owner to re-approve (FR-041). Fork PRs are
out of scope (no secrets).

### `prompt-drift.yml` — `schedule` (hourly) + `workflow_dispatch`

`prompts.sync verify` against the shared registry. **Alerting
only** — it opens or updates an incident issue and does not change anything (FR-026). It exits 0 with a notice when the `LANGFUSE_HOST` secret is empty, so an unconfigured repository gets no hourly failure or alert. The spec and
ADR-052 state that runtime allow-list enforcement, not this job, is the guarantee.

## Org prerequisites (blocker B-4 - resolved: single maintainer)

This is a solo repository, so the ownership and break-glass rules are **demonstrable, not
activated**:

- Owner groups are documented synthetic groups in `prompts/owners.yaml`; the two-member rule is
  checked against that file.
- `.github/CODEOWNERS` is generated from template owner fields, but "require Code Owner review"
  cannot be satisfied by a single author, so it is not switched on.
- `REBASELINE_TOKEN` is a fine-grained PAT.
- FR-036-FR-041 are enforced by the CI checks above and exercised in tests with fake reviewers.
  ADR-052 D11 states this limit plainly.
