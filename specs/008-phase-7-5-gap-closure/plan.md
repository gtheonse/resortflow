# Implementation Plan: Phase 7.5 Gap Closure & Hardening

**Branch**: `008-phase-7-5-gap-closure` | **Date**: 2026-09-15 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/008-phase-7-5-gap-closure/spec.md`

## Summary

A read-only audit (prompted by testing the local UI console, feature "008" by commit
message but never given a `specs/008-*` folder — a deviation this feature does not
repeat) found `make up` was never actually reachable end to end: `jwt-issuer`/
`edge-shim` declare build contexts that don't exist, and `resortflow-orchestrator`/
`hold-ledger-reaper` need a root `Dockerfile` that was never written
(`specs/006-policy-guardrails-hitl/tasks.md` T056 records this as an explicitly
user-confirmed open blocker). The same audit found `.env`'s
`OTEL_EXPORTER_OTLP_ENDPOINT` points at a port nothing listens on, and that
`specs/003-mcp-tool-layer` (Phase 2) has 36 of 72 tasks still unchecked with no
the Phase 2 handoff. This feature closes all of it: documents (doesn't build) a
local jwt-issuer/edge-shim replacement, ships a root `Dockerfile` + a new production
`graph/api.py` orchestrator entrypoint, fixes the Langfuse/OTel exporter wiring with
headless project provisioning, and finishes specs/003's backlog plus two adjacent
already-tracked loose ends (specs/005 T063, specs/006 T056). Three ADRs (049–051) are
written; none of this is new guest-facing product behavior.

## Technical Context

**Language/Version**: Python 3.11+ (unchanged, matches `graph/`, `agents/`,
`mcp/gateway/`).

**Primary Dependencies**: No new dependency families — reuses `fastapi`/`uvicorn`
(already used by `ui/app.py`/`mcp/gateway`), `langgraph-checkpoint-postgres`
(already used by `ui/runtime.py`), `opentelemetry-exporter-otlp-proto-http`
(already used by `observability/telemetry.py`). New Dockerfile-only additions:
`spacy` + `en_core_web_sm` model (specs/006 T056/ADR-031), and pre-cached
`sentence-transformers` model weights for `rag/retriever.py`'s reranker (found during
this feature's own planning, not in the original gap list).

**Storage**: Postgres unchanged — `graph/api.py` reuses the existing
`agents.dining.graph.postgres_checkpointer()` + `graph.ledger.PostgresHoldLedger()`
wiring, lifted from `ui/runtime.py` into a new `graph/runtime.py` so production code
doesn't depend on dev-console code.

**Testing**: pytest. New `graph/tests/test_api.py` (FastAPI `TestClient`, in-memory
checkpointer — no Postgres needed for this unit level). specs/003's backlog closure
adds ~20 new files under `mcp/tests/{contract,integration,unit}/`.

**Target Platform**: Local docker-compose, unchanged. This feature adds the repo's
first root `Dockerfile` (previously only `mcp/Dockerfile` and five sim Dockerfiles
existed) and is the direct prerequisite for Phase 8's Container Apps deployment.

**Cross-cutting note**: This feature touches Track F (`infra/*`, `docker/*` in spirit
— the root `Dockerfile` lives at repo root, not `docker/`, matching how `mcp/Dockerfile`
lives under `mcp/` rather than `docker/`), the cross-cutting `observability/` module,
and re-touches Track B's (`mcp/`) test backlog — which is why it's its own phase rather
than folded into any single track's continuation (`AGENTS.md`'s track-ownership table).

## ADRs

- **ADR-049** — No standalone jwt-issuer/edge-shim for local dev; formalizes ADR-012's
  in-process identity issuer as the permanent answer.
- **ADR-050** — Shared root Docker image for resortflow-orchestrator/hold-ledger-reaper;
  `graph/api.py` as the production entrypoint.
- **ADR-051** — Langfuse-aware OTLP exporter configuration + headless provisioning.

## Speckit process note

The `.claude/skills/speckit-*` skills were not available in the environment this feature
was authored in. Per this feature's own fallback plan, `spec.md`/`plan.md`/`tasks.md`
were produced by running `.specify/scripts/powershell/create-new-feature.ps1` for
directory/branch scaffolding and hand-authoring content to match
`specs/007-eval-observability-gate`'s reference shape, rather than via the skills
directly. Disclosed here per the project rule that a skipped workflow step is recorded
rather than skipped silently.
