# Quickstart: validating Prompt Management end to end

A run/validation guide, not an implementation guide. Contracts: [prompt-template-schema](contracts/prompt-template-schema.md),
[lock-and-revocations](contracts/lock-and-revocations.md), [prompts-cli](contracts/prompts-cli.md),
[registry-runtime](contracts/registry-runtime.md), [telemetry-prompt-attributes](contracts/telemetry-prompt-attributes.md),
[ci-workflows](contracts/ci-workflows.md). Entities: [data-model](data-model.md).

## Prerequisites

- `make up` (Langfuse at http://localhost:3000, headless-initialised project `resortflow-local`, keys from `.env`).
- `pip install -e ".[dev]"` (installs `langfuse>=4.15,<5`).
- Azure credentials only for scenarios marked ☁ (judge / intent model calls); everything else
  runs offline with the fake clients.

## Scenario 0 - a tracing-disabled registry client is inert (non-blocking, FR-031)

`pytest prompts/tests/test_adapter_tracing_disabled.py` - no Langfuse server needed. It constructs the
adapter's client with `tracing_enabled=False` against an unreachable host and asserts: the global
tracer provider object is unchanged, no span processor was added, and the exported span set and count
are unchanged. (This replaces the withdrawn T001 spike: lineage is `resortflow.prompt.*` only, so nothing
depends on the Langfuse UI linking a generation to a prompt version.)

## Scenario 1 — US1: dining prompt migrates with no behaviour change (offline)

- `python -m prompts.sync validate` → exit 0.
- `pytest agents/dining/tests prompts/tests -k "intent or registry"` → green. The rendered system
  prompt for `PROMPT_SOURCE=git` is byte-identical to the previous `_SCHEMA_INSTRUCTIONS`
  string (golden test); `grep -rn "_SCHEMA_INSTRUCTIONS" agents/` returns nothing (SC-001).
- Run the standalone dining graph; its model-call span shows `resortflow.prompt.name=dining_intent_extraction`,
  `source=git`, a 64-hex `sha256`.

- Container: `docker compose --profile app build resortflow-orchestrator` then `up -d resortflow-orchestrator` → healthy, and `docker compose exec resortflow-orchestrator python -c "import prompts"` succeeds (the image carries `prompts/`, T005a).

## Scenario 2 — registry mode and fallback

> **Not runnable as written: US3 (`prompts.sync`) was descoped by decision (2026-09-27) and was
> not built — see the Phase 7.6 handoff.** This scenario, and Scenarios 4, 6, 7 and 8
> below, all invoke `prompts.sync` subcommands that don't exist. `PROMPT_SOURCE=langfuse`
> fallback behaviour itself (the runtime side) is still fully implemented and covered by
> `prompts/tests/test_registry_sources.py`/`test_failure_matrix.py` — only the CLI tooling that
> would push/lock/promote/verify content into the registry is missing.

1. `make seed` (runs `prompts-sync`, i.e. push + lock) with `PROMPT_SOURCE=langfuse PROMPT_LABEL=staging`.
   **Expect:** `source=langfuse` once the hash is locked; before `lock`, the same hash resolves to
   `git-fallback-unapproved` (there is no mode that serves an unlocked hash).
2. `docker compose stop langfuse` and run a turn.
   **Expect:** turn succeeds; `source=git-fallback`, `rejected_reason=fetch_error` (SC-003).
3. Re-run `prompts.sync push` twice with unchanged templates. **Expect:** zero new versions (SC-007).

## Scenario 3 - fail-closed matrix (SC-004)

`pytest prompts/tests/test_failure_matrix.py` - one case per row of the registry-runtime failure matrix.
Rows that need a *locked* faulty hash (missing/undeclared/unrendered variable, bad config) put the faulty
hash in a test lock, which models rolling back to an older approved version that lacks a variable the
call site now requires. Manually: in the Langfuse UI edit the dining prompt, delete
`{{dietary_restrictions}}`, label it `staging`, and run a turn with `PROMPT_SOURCE=langfuse
PROMPT_LABEL=staging`. **Expect:** the hash is not locked, so the application serves the Git copy with
`source=git-fallback-unapproved`; the model never receives the stripped prompt.

## Scenario 4 — the timing hole (SC-008)
> Not run — needs `prompts.sync` (US3, descoped 2026-09-27). See Scenario 2's note.


After a PASS, edit the `production` prompt text in the UI (or move the label to a never-locked
version). **Expect:** the next resolve after the cache window serves the Git copy
with `source=git-fallback-unapproved`; `prompts.sync verify` exits 1 naming the prompt.

## Scenario 5 — US2: judge prompt change is a hard gate (☁ for the real judge; fake client works offline)

1. Change one character in `eval_retrieval_judge_faithfulness.yaml`.
2. `python -m eval.runner --tier pr` → verdict **FAIL**, reason `BASELINE_STALE`, naming
   `eval_retrieval_judge_faithfulness`, exit 1, hint to add the `rebaseline` label (SC-005).
3. Add the label (or `workflow_dispatch`): the job commits a regenerated `eval/data/baseline.json`
   to the PR branch; the PR gate re-runs and the stale reason clears.
4. Eval reports now list judge prompt id/version/sha256 next to provider and model (FR-017).

## Scenario 6 — rollback and promotion
> Not run — needs `prompts.sync` (US3, descoped 2026-09-27). See Scenario 2's note.


`prompts.sync promote --require-pass <sha>` moves `production` to the staged version. Then
`update_prompt`-style rollback to an earlier approved version. **Expect:** served after the cache
window with no redeploy (SC-006); `verify` reports a "rollback in effect" notice, exit 0.

## Scenario 7 — revocation
> Not run — needs `prompts.sync` (US3, descoped 2026-09-27). See Scenario 2's note.


1. `prompts.sync revoke --hash <H> --reason … --ref …` where `H` is the current template hash.
   **Expect:** CI invariant L4 fails — the change cannot land without a non-revoked replacement.
2. Revert the template to an older approved version in the same change; add `--break-glass`.
   **Expect:** L6 passes only if no new approved hash was added; the tombstone records
   `break_glass: true`; the owner's after-the-fact ack is recorded manually (no audit job)
   (SC-010, SC-011, SC-019).
3. Point a registry label (incl. `staging`) at the revoked hash. **Expect:** runtime serves the Git
   copy with `git-fallback-revoked`; the drift job fails (FR-026).

## Scenario 8 - registry edits are scratch (FR-022a)
> Not run — needs `prompts.sync` (US3, descoped 2026-09-27). See Scenario 2's note.


1. In the Langfuse UI, edit a prompt and save it as a new version labelled `staging`.
2. Run `prompts.sync push` with unchanged templates. **Expect:** the job prints the prompt and
   version it moved `staging` off; nothing blocks.
3. Serve that UI version (`PROMPT_SOURCE=langfuse`). **Expect:** refused (`git-fallback-unapproved`)
   whatever the label.
4. Add `gluten` to a template's message text. **Expect:** CI lint (FR-028e) fails (SC-015).

## Scenario 9 — tenants (SC-016)

`pytest eval/tests -k tenant`: a fixture where a wording change drops MHB below its floor while the
aggregate stays above **fails** the gate; an MHB stratum below the minimum reports a coverage
failure, not a pass.

## Scenario 10 — a degraded prompt fails CI (FR-035) ☁

Replace the dining system prompt with a clearly worse one and run `python -m eval.runner --tier pr`.
**Expect:** FAIL on `intent_extraction_accuracy` and/or `dietary_recall` (floor 1.0).

## Definition of done for the feature

All scenarios above pass; `/speckit-analyze` finds no D-register drift; `/speckit-converge` reports
**Converged**; Phase 7.6 handoff written; ADR-052/053/054 Accepted.
