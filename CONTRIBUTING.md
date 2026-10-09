# Contributing

Thanks for your interest in ResortFlow. This guide covers how to set up, what a good change looks
like, and the constraints that are not negotiable. [AGENTS.md](AGENTS.md) has the full project
conventions and is the reference for both people and coding agents.

## Set up

```bash
git clone https://github.com/gtheonse/resortflow.git && cd resortflow
cp .env.example .env                       # LLM variables are optional for most work
pip install -e ".[dev]"
python -m spacy download en_core_web_sm
make up && make seed                       # only needed for integration tests and the demo
```

## Before you open a pull request

```bash
ruff check .
mypy
pytest -m "not integration"                # or: make test-docker
python -m prompts.sync validate
python -m eval.runner --tier pr            # if you touched agents, prompts, policy, or retrieval
```

The evaluation suite is the merge gate: a regression in a domain's eval blocks the merge, and a
deliberately degraded prompt must fail CI.

## What a good change looks like

- **Small and scoped** to one module area. Modules are file-disjoint; cross-cutting changes go
  in their own commit.
- **Tested.** New behavior has tests. Saga failure permutations and tenant isolation are
  mandatory coverage when you touch the commit path or the gateway.
- **Recorded.** A decision that constrains later work, reverses an architectural commitment, or
  trades one project value against another gets an ADR in `docs/adr/` (Context, Options
  Considered, Decision, Consequences).
- **Traceable.** Larger features go through the spec-driven workflow described in AGENTS.md and
  live under `specs/`.

## Non-negotiables

These are enforced in code and in review. A change that breaks one needs an ADR first.

- No agent places a wager or executes a gaming transaction; the gaming domain is read-only.
- Dietary restrictions are structured data (enum plus severity), never free text alone.
- `tenant_id` comes from the verified token claim, never from model output.
- Every external mutation is hold-then-confirm; holds are revalidated on resume.
- Graph node names are a persistence contract and are not renamed in a patch release.
- The frozen API contracts in `docs/contracts/` change only by agreement, not unilaterally.
- No real client, property, or brand names, and no credentials, in the repository.

## Commit messages

Use a short imperative summary, with a body explaining *why* when the reason is not obvious
(for example `Fix hold revalidation on resume after TTL expiry`).

## Reporting security issues

Please do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
