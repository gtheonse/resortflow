# Tasks: Phase 7.5 Gap Closure & Hardening

**Input**: plan.md, spec.md from `/specs/008-phase-7-5-gap-closure/`

Tags: `[P]` parallelizable with other `[P]` tasks in the same group; `[Story N]` maps to
spec.md's User Story N; `[CRIT]` regulator/correctness-critical, do not under-scope.

## Phase A — Setup (blocks everything else)

- [X] T001 Create feature branch `feature/phase-7.5-gap-closure` and scaffold
  `specs/008-phase-7-5-gap-closure/` via `.specify/scripts/powershell/create-new-feature.ps1`
- [X] T002 Write `spec.md`
- [X] T003 Write `plan.md`
- [X] T004 Write this `tasks.md`

## Phase B — User Story 1: `make up` succeeds end to end (P1)

- [ ] T010 [Story 1] Remove/comment `jwt-issuer` and `edge-shim` from `docker-compose.yml`'s
  default profile, pointing at ADR-049
- [ ] T011 [Story 1] Write `docs/adr/049-local-dev-identity-and-edge-no-separate-services.md`
- [ ] T012 [Story 1] [P] Move `GraphRuntime`/`build_runtime()` from `ui/runtime.py` to new
  `graph/runtime.py`; `ui/runtime.py` re-exports for backward compatibility
- [ ] T013 [Story 1] [P] Write new root `Dockerfile` (house style: `python:3.11-slim`,
  hand-listed deps, `COPY observability graph agents rag policy infra` +
  `mcp/gateway → gateway`, `RUN python -m spacy download en_core_web_sm`, pre-cache the
  sentence-transformers reranker/embedding model(s), `CMD uvicorn graph.api:app --port 8100`)
- [ ] T014 [Story 1] Write `docs/adr/050-root-orchestrator-image-and-entrypoint.md`
- [ ] T015 [Story 1] Wire `resortflow-orchestrator`/`hold-ledger-reaper` in `docker-compose.yml`
  (env vars: `POSTGRES_URL`, `MCP_GATEWAY_URL`, `MCP_DEV_SIGNING_KEY`,
  `OTEL_EXPORTER_OTLP_ENDPOINT`, `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`, `LLM_PROVIDER`;
  add a `healthcheck:` against `/healthz`)
- [ ] T016 [Story 1] Verify: `docker compose config -q`; `docker compose --profile sims
  --profile app build`; `up -d`; `docker compose ps` all `Up`/healthy

## Phase C — User Story 2: real turn through the orchestrator (P1)

- [ ] T020 [Story 2] Write `graph/api.py` (production FastAPI entrypoint — no
  `load_dotenv()`, `graph.runtime.build_runtime()`, `DEFAULT_TOOLS_FACTORY`):
  `POST /v1/turns`, `GET /v1/threads/{id}`, `POST /v1/threads/{id}/approve`,
  `POST /v1/threads/{id}/clarify`, `GET /healthz`. Reuses `ui/app.py`'s already-debugged
  resume payload shapes exactly. Docstring discloses the `tenant_id`-in-body /
  no-edge-auth gap as deferred to Phase 8.
- [ ] T021 [Story 2] [P] Write `graph/tests/test_api.py` (new-thread → approve → resume;
  new-thread → clarify → resume; reconnect via GET) using `TestClient` +
  `MemorySaver`/`NullHoldLedger`
- [ ] T022 [Story 2] Verify: real `curl POST /v1/turns` against the running container
  round-trips correctly end to end

## Phase D — User Story 3: Langfuse trace visible (P1)

- [ ] T030 [Story 3] [P] Fix `observability/telemetry.py`'s `_build_provider()`:
  Langfuse-aware OTLP path + Basic auth + `x-langfuse-ingestion-version` header when
  `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` are set; generic `/v1/traces` path otherwise
- [ ] T031 [Story 3] [P] Add `session_id` param to `start_agent_span()`; thread
  `state.get("session_id")` through `wrap_node()`'s sync/async wrappers; update
  `specs/007-eval-observability-gate/contracts/telemetry-module-contract.md` as an
  amendment
- [ ] T032 [Story 3] Add `LANGFUSE_INIT_*` headless-provisioning env vars to
  `docker-compose.yml`'s `langfuse` service, keyed from `.env`'s
  `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`
- [ ] T033 [Story 3] Write `docs/adr/051-langfuse-otlp-exporter-wiring.md`
- [ ] T034 [Story 3] [CRIT] Verify: run one real turn, confirm the trace visually lands
  in `http://localhost:3000`'s Sessions view grouped by `session_id`, with
  `gen_ai.system`/`gen_ai.operation.name`/`langfuse.session.id` present — do not accept
  "code runs without raising" as proof (the exporter swallows failures silently)

## Phase E — User Story 4: specs/003-mcp-tool-layer backlog (P2)

- [ ] T040 [Story 4] [P] T026-T027, T035: registry-enumeration/surface-uniformity/
  registry-vs-contract tests; resolve the entertainment tool-count drift (contract says
  7, code has 8 — correct the contract doc)
- [ ] T041 [Story 4] [P] T036-T038: tenant-isolation + identity-refusal test suites
- [ ] T042 [Story 4] [P] [CRIT] T039-T042, T072: error-classification/fault-injection
  suites, self-exclusion → `POLICY_BLOCKED` test; verify entertainment tool (already
  built per feature 005) meets T041's acceptance bar rather than rebuilding
- [ ] T043 [Story 4] [P] T043-T045: idempotency test suites
- [ ] T044 [Story 4] [P] T046-T049: circuit-breaker/resilience/indeterminate-commit tests
- [ ] T045 [Story 4] [P] T050-T052: entitlement/declaration tests
- [ ] T046 [Story 4] [P] T053-T055: gaming read-only tests
- [ ] T047 [Story 4] T056-T059: tracing tests — sequenced after T030/T031 land so they
  test final `session_id` behavior, not a moving target
- [ ] T048 [Story 4] T060-T062: README/env/`make mcp` wiring
- [ ] T049 [Story 4] T063-T065: composite partial-failure test, live quickstart run,
  lint/type clean (`ruff check mcp/`, `mypy mcp/gateway`)
- [ ] T050 [Story 4] T070: write retroactive Phase 2 handoff
- [ ] T051 [Story 4] T071: raise `schema_drift` fault mode with Track A
  (`sims/_shared/faults.py`), un-skip the schema-invalid error-taxonomy cases
- [ ] T052 [Story 4] Verify: `python -m pytest mcp/tests/ -v` all green; every checkbox
  in `specs/003-mcp-tool-layer/tasks.md` checked

## Phase F — User Story 5: adjacent loose ends (P3)

- [ ] T060 [Story 5] Check off `specs/006-policy-guardrails-hitl/tasks.md` T056, pointing
  at the new Dockerfile/ADR-050; update the Phase 6 handoff's note
- [ ] T061 [Story 5] Run `specs/005-multi-agent-orchestration/quickstart.md`'s scenario
  list against the live stack; check off T063 in `specs/005-multi-agent-orchestration/tasks.md`

## Phase G — Polish & convergence

- [ ] T070 Insert "Phase 7.5 — Gap Closure & Hardening" into
  `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §13, between Phase 7 and Phase 8
- [ ] T071 Write the Phase 7.5 handoff
- [ ] T072 Full verification pass (see spec.md Success Criteria / the approved plan's
  final verification command list); `docker compose down` to close out
