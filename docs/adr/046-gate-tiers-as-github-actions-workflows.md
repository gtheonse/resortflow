# ADR-046 — Gate tiers implemented as GitHub Actions workflows

**Status**: Accepted
**Date**: 2026-09-13
**Feature**: 007-eval-observability-gate

## Context

This repository has no CI at all today — `.github/workflows/` does not exist.
FR-019 requires three gate tiers (PR, merge-queue, nightly), each triggered at
a different point in the merge workflow. This feature must introduce CI for
the first time while keeping the gate logic itself free of any CI-platform or
cloud dependency (FR-013, SC-006).

## Options Considered

**1. GitHub Actions workflows**, one per tier, each a thin trigger wrapper
invoking `python -m eval.runner --tier <tier>` — `pull_request` for the PR
tier, `merge_group` for the merge-queue tier, `schedule` for nightly.
Advantage: this is a GitHub-hosted repository already; GitHub Actions is the
natural, zero-additional-setup choice, and its native `merge_group` event maps
directly onto the spec's own "merge queue" tier concept. Disadvantage:
introduces a real dependency on one CI platform's trigger semantics, even
though the gate logic underneath stays portable.

**2. Defer the CI platform choice indefinitely** — ship the tiered `eval.runner`
command with no workflow files at all, leaving "wire it into some CI" as
explicitly out of scope for this phase. Advantage: maximum platform neutrality;
no CI-specific YAML to maintain. Disadvantage: the spec's own tiering answer
(clarify Q1) explicitly describes a "PR gate" and a "merge queue" — concepts
that need a real trigger to exist as anything more than a command a human
remembers to run; FR-019 would be unimplementable in any observable sense this
phase, for a portability gain that isn't real (the runner command is already
platform-neutral regardless of whether a workflow file invokes it).

**3. A platform-agnostic CI abstraction layer** (e.g., a custom orchestrator
that generates workflow files for multiple CI platforms from one
specification). Advantage: in principle, works everywhere. Disadvantage:
building and maintaining a meta-CI generator is disproportionate effort for a
single-repository project with one actual CI platform in use; the portability
concern is already satisfied more simply by keeping trigger wrappers thin.

## Decision

**Adopt Option 1.** Three GitHub Actions workflow files, each a thin wrapper
around the same `eval.runner` command; all actual gate logic — scoring, gate
evaluation, verdict computation — lives in `eval/` and is invoked identically
whether run in CI or on a laptop.

## Consequences

**Made easier.** The spec's tiering requirement becomes real and observable
(a PR shows a required status check; the merge queue actually gates on it);
GitHub's native `merge_group` event is exactly the trigger the "merge-queue
tier" concept needs, with no custom queue-management code to write.

**Made harder.** A future migration to a different CI platform means rewriting
three workflow YAML files and their exit-code/environment-variable wiring
(`ci-tier-triggers-contract.md`) — real, if bounded, migration work.

**Foreclosed.** This does not attempt to be usable unmodified on a non-GitHub
CI platform today — that would require the meta-CI abstraction of Option 3,
which this ADR explicitly rejects as disproportionate for a single-platform
project.
