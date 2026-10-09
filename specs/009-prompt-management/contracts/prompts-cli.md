# Contract: `python -m prompts.sync`

One CLI, thin wrapper over library functions in `prompts/` (mirrors ADR-046: gate logic lives in
the package, workflows only decide *when*). Registry credentials from `LANGFUSE_PUBLIC_KEY`,
`LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success / no findings |
| 1 | Policy failure (blocking): lint, invariant, drift, approval, unapproved/revoked content |
| 2 | Usage or configuration error |
| 3 | Registry unreachable (commands that need it) — classified per command, see below |

## Commands

| Command | Purpose | Key behaviour |
|---|---|---|
| `validate` | Load every template; run V1–V9 (see template contract) | Offline. Runs at process start of the orchestrator and eval runner (fail fast) and in PR CI |
| `push` | Mirror templates to the registry | Creates a new version **only if** the canonical hash differs from the registry's latest; labels it `staging`; writes an informational `commit_message`. When it moves `staging` off a version it did not create (for example one authored in the UI) it **prints** the prompt and version numbers (FR-022a); it never blocks. Idempotent |
| `lock [--check [--base <ref>] [--break-glass]]` | Regenerate `prompts.lock.json`; with `--check`, verify the lock invariants | Regenerates `approved = base lock ∪ current template hashes`, never removing entries. `--check`: exit 1 if the file differs from what would be generated, or if any invariant L1–L7 fails against `--base` (default `origin/main`, resolved to `git merge-base HEAD <ref>`; identical in PRs and the merge queue); CI passes `--break-glass` when the PR carries the `incident` label, which additionally enforces L6 |
| `verify [--json]` | Registry vs Git/lock | Exit 1 if any prompt's `production` hash ∉ approved or ∈ revoked, **or** any label on any prompt (incl. `staging`) points at a revoked hash. An approved production hash that differs from Git head is a **"rollback in effect" notice**, not a failure (FR-023). Exit 3 if unreachable |
| `promote --require-pass <sha>` | Move `production` to the current `staging` version | Refuses if the staged hash ∈ revoked or ∉ approved; requires the named commit's `merge-queue-gate` check to be `success`; uses `update_prompt` |
| `revoke --hash <sha> --reason … --ref … [--break-glass]` | Add a tombstone | Appends to `revocations.json`; enforces L4/L6 locally; `--break-glass` sets `break_glass: true` |
| `codeowners [--check]` | Generate/verify the CODEOWNERS block in **last-match-wins** order: default `/prompts/` → compliance first, then the FR-042 specific rules | `--check`: exit 1 on drift, on an out-of-order block, or on any group with < 2 members (FR-038/039) |

## Failure classification

| Command | Registry unreachable |
|---|---|
| `validate`, `lock`, `codeowners`, `revoke` | not applicable (offline) |
| `push`, `promote` | exit 3; nothing is changed |
| `verify` in PR CI | exit 3 → **warn**, not fail (no registry in PR CI); in the scheduled job → alert |

## Guarantees

- No subcommand is reachable from a graph node or from model output.
- `push` never writes to `production`; only `promote` and manual registry action can.
- Every command that changes registry state prints the affected `name@version` list and the actor.

*`pull` (registry-to-Git import) and `--discard` were specified and deferred on 2026-09-26; see ADR-052 D9.*
