# ADR-054 — Rebaseline automation: a label-triggered, PR-branch commit with a fine-grained PAT

**Status**: Proposed
**Date**: 2026-09-27
**Feature**: 009-prompt-management
**Extends**: ADR-045 (eval state as versioned repo files), ADR-046 (gate tiers as GitHub Actions workflows)
**Amends**: ADR-045 (a second baseline writer), ADR-044 (judge prompts are pinned alongside the judge model)

## Context

T052/research R-10 makes a judge-prompt hash change a hard gate failure, `BASELINE_STALE`: every
score the baseline recorded was measured by a prompt that no longer exists, so nothing about the
current run can be trusted against it until the baseline is regenerated. `eval.runner`'s only
existing baseline writer (ADR-045) commits on `main` from the merge-queue tier — but a PR that
*intentionally* edits a judge prompt cannot merge at all under `BASELINE_STALE` without first
clearing it, and clearing it requires a baseline commit that reflects the PR's own change, not a
future merge-queue run after the fact. The spec is explicit: regeneration happens **in the same
PR**, not as a follow-up.

That requires a second automation path: a job that runs on a PR branch (not `main`) and pushes a
commit back to it. Two things make that non-trivial. First, a commit authenticated with the
default `GITHUB_TOKEN` does not retrigger other workflows on that PR (GitHub's built-in
recursion-prevention) — so the PR gate that just failed on `BASELINE_STALE` would never re-run to
confirm the fix. Second, an automation that can push to arbitrary PR branches on trigger is a
plausible loop and privilege-escalation surface if not scoped carefully.

## Options Considered

**1. A GitHub App with narrowly scoped installation permissions.** Advantage: fine-grained,
revocable, no personal token tied to an individual account. Disadvantage: standing up an App
(registration, private key management, installation) is real operational weight for a single-maintainer
repository (ADR-052 D11's "enforcement is demonstrable, not activated" already accepts this trade-off
for CODEOWNERS) — disproportionate to what one workflow needs.

**2. A fine-grained personal access token (`REBASELINE_TOKEN`), scoped to this repository's
contents.** Advantage: a commit pushed with a PAT is treated as pushed by a user, which DOES
retrigger other workflows — solving the retrigger problem directly; setup is one repository secret.
Disadvantage: tied to an individual account (acceptable here; the runbook, T081, documents rotation);
broader default scopes than an App's granular permission model unless deliberately narrowed at
creation.

**3. Automatic trigger (every `BASELINE_STALE` failure reruns immediately), no label.** Advantage:
zero manual step. Disadvantage: a `BASELINE_STALE` failure is not always something to silently paper
over — it might be catching an *unintended* prompt edit (e.g. an accidental character change nobody
meant to ship), and auto-regenerating the baseline in that case would make the gate rubber-stamp a
mistake instead of surfacing it. A label is a deliberate "yes, this prompt change is intentional,
regenerate" signal from a human.

## Decision

**Adopt Option 2, trigger by Option 3's alternative (a label, not automatic).**
`.github/workflows/rebaseline.yml` runs on `pull_request: [labeled]` (label `rebaseline`) and
`workflow_dispatch`; it checks out the PR head ref with `REBASELINE_TOKEN`, runs
`python -m eval.runner --tier merge_queue` (the same eligibility rule as ADR-045: only a COMPLETE
verdict may become the baseline), and commits `eval/data/baseline.json` as `resortflow-eval-bot` **only
if it changed and the run was COMPLETE** — otherwise the job fails visibly rather than silently
skipping, so a genuinely broken run is never mistaken for "nothing to rebaseline."
`concurrency: rebaseline-${{ github.event.pull_request.number }}` with `cancel-in-progress` avoids
two runs racing on the same branch.

**Loop-proofing.** After the commit, the judge-prompt hashes in `baseline.json` match what the PR's
current templates hash to, so a further run of the same job changes nothing (`git diff --quiet`
short-circuits it). The token-authenticated push fires `synchronize` on the PR, which re-runs the PR
gate — and if branch protection has "dismiss stale approvals on push" enabled, it requires the
`eval_track` owner group to re-approve the regenerated baseline (FR-041), which is the review point
this repository's CODEOWNERS rules are built to enforce. Fork PRs are out of scope: a fork's workflow
run has no access to repository secrets, so `REBASELINE_TOKEN` is simply unavailable there, and the
job has nothing to authenticate with.

**No audit job.** An earlier draft paired this with a scheduled `revocation-audit` job cross-checking
every break-glass and rebaseline commit against its originating PR. That was dropped (2026-09-26):
for a solo repository, the audit trail already IS the git history of `main` and PR branches — a
scheduled job re-deriving what `git log` already shows adds a second thing to keep correct without a
counterparty who'd ever consume its output. The break-glass path (ADR-052 D2/D... revocation) remains
a documented manual convention with the L6 CI invariant (no new approved hashes) as its only automated
guard.

## Consequences

**Made easier.** A judge-prompt edit is no longer stuck behind "wait for a merge-queue run on
`main`" — the same PR that changed the prompt can clear its own `BASELINE_STALE` failure with one
label. The mechanism reuses `eval.runner`'s existing eligibility rule verbatim; no new baseline logic.

**Made harder.** A second entity (`resortflow-eval-bot`, via a PAT rather than an App) can push to
protected branches' PRs — a second thing that must be defended if the token ever needs rotating or
scoping down, on top of the existing merge-queue writer.

**Foreclosed.** Automatic, labelless rebaseline; a GitHub App; a scheduled audit job cross-checking
rebaseline/break-glass commits (all three named and rejected above, or in ADR-052 D-register).
