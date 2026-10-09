# Quickstart: Contracts Foundation

**Feature**: `001-contracts-foundation` | Validates the acceptance scenarios in `spec.md`

This is a validation guide, not implementation code. Full source lives under `graph/`,
`observability/`, `infra/sql/` and `tests/` once `/speckit-implement` runs.

## Prerequisites

- Python 3.11+
- Docker (for the ledger DDL check; optional for the rest)
- Repository root: the directory containing `pyproject.toml`

## Setup

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
```

**Note**: `pyproject.toml` targets `requires-python>=3.11` per `plan.md`'s Technical
Context. `py -3.13` above is a local-environment choice (3.11 was not installed on the
machine this feature was implemented on) — any interpreter satisfying `>=3.11` works.

## Validate: User Story 1 — parallel writes survive

```powershell
.\.venv\Scripts\python -m pytest tests/test_reducers.py -v -k "concurrent"
```

**Expected**: three simulated writers each contribute one proposal and one hold in the same
step; all three are present afterward; a stale write reporting a confirmed hold as pending
does not regress it; replaying an identical write is a no-op.

## Validate: User Story 2 — crash-window recovery

Requires Docker — `docker compose up -d postgres` and apply
`infra/sql/001_hold_ledger.sql` first (see "Validate: the ledger" below); the test skips
cleanly rather than failing when Postgres is unavailable.

```powershell
.\.venv\Scripts\python -m pytest tests/test_ledger.py -v
```

**Expected**: an intent recorded with no outcome is discoverable and releasable; a vendor
response with no prior intent joins by idempotency key; an orphaned `HELD` row with no
active session is identifiable for release; two attempts with the same idempotency key
produce one entry.

## Validate: User Story 3 — total, deterministic classification

```powershell
.\.venv\Scripts\python -m pytest tests/test_errors.py -v
```

**Expected**: every code in `data-model.md`'s error taxonomy table classifies to exactly
one of the four dispositions; the casino `403` responsible-gaming suppression classifies as
`POLICY_BLOCKED`, never `RETRYABLE`; an unrecognised code still resolves deterministically
by its HTTP status.

## Validate: full contract suite

```powershell
.\.venv\Scripts\python -m mypy graph observability     # strict, must be clean
.\.venv\Scripts\python -m pytest tests -q               # all seven modules
.\.venv\Scripts\python -m ruff check graph observability tests
.\.venv\Scripts\python -m openapi_spec_validator docs/contracts/openapi/*.yaml
```

## Validate: the ledger (requires Docker)

```powershell
docker compose config -q
docker compose up -d postgres
docker compose exec -T postgres psql -U resortflow -d resortflow -f /dev/stdin < infra/sql/001_hold_ledger.sql
docker compose exec -T postgres psql -U resortflow -d resortflow -c "\d hold_ledger"
docker compose down
```

**Expected**: the table creates with the `status` check constraint, the three indexes, and
the `updated_at` trigger, all visible in `\d hold_ledger` output.

## Validate: the plan-template override took effect

```powershell
.\.specify\scripts\powershell\resolve-template.ps1 plan-template -Json |
    ConvertFrom-Json | Select-Object -ExpandProperty TEMPLATE_CONTENT |
    Select-String -Pattern 'Architecture Decision Records'
```

**Expected**: a match. Confirms `.specify/templates/overrides/plan-template.md` — not the
stock template — is what every later feature's `/speckit-plan` resolves to.

## Done when

All of the above pass, all five OpenAPI specs validate, all seven ADRs (001–006, 019) exist.
