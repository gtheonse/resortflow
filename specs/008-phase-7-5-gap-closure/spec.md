# Feature Specification: Phase 7.5 Gap Closure & Hardening

**Feature Branch**: `008-phase-7-5-gap-closure`

**Created**: 2026-09-15

**Status**: Draft

**Input**: User description: "Phase 7.5 gap closure: local-dev jwt-issuer/edge-shim decision (document only), root Dockerfile plus resortflow-orchestrator and hold-ledger-reaper containerization, Langfuse/OTel observability wiring fix end-to-end, and closing the specs/003-mcp-tool-layer backlog plus specs/005 T063 and specs/006 T056. Hardening/gap-closure phase inserted between Phase 7 and Phase 8, not new product behavior."

## Summary

This is a maintenance/hardening feature, not new product capability. A read-only audit
(triggered by testing the local UI console) found that `make up` never actually
succeeds end to end — two compose services have no build context at all, and two more
have no root `Dockerfile` to build from — and that observability wiring silently drops
every trace. The audit also surfaced a large pre-existing test/documentation backlog in
`specs/003-mcp-tool-layer` and two small already-tracked loose ends elsewhere. This
feature closes all of it so the local stack is genuinely runnable before Phase 8 (Azure
Deployment) starts.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - `make up` succeeds end to end (Priority: P1)

An operator runs `make up` on a clean checkout and gets a fully running local stack,
with no service failing to build because its source doesn't exist.

**Why this priority**: Every other story in this feature — and the entirety of Phase 8
— depends on the local stack actually running. This is the blocking prerequisite.

**Independent Test**: Run `docker compose --profile sims --profile app up -d` on a
clean checkout and confirm every declared service reaches `Up`/healthy, with none
failing on a missing build context or Dockerfile.

**Acceptance Scenarios**:

1. **Given** a clean checkout, **When** `make up` runs, **Then** it does not attempt to
   build `jwt-issuer` or `edge-shim` (removed from the default profile, per ADR-049).
2. **Given** a clean checkout, **When** `make up` runs, **Then** `resortflow-orchestrator` and
   `hold-ledger-reaper` both build (from a new root `Dockerfile`) and start.

---

### User Story 2 - A real turn round-trips through a containerized orchestrator (Priority: P1)

A caller sends a guest request to the running stack and gets a real, correct response —
proving the orchestrator is a genuine service, not only an in-process dev console.

**Why this priority**: Containerizing the orchestrator with no working entrypoint would
be theater. This story is the actual proof the container does something.

**Independent Test**: `POST /v1/turns` against the running `resortflow-orchestrator` with a
real tenant/guest/text payload and confirm a correct final JSON response, including a
`session_id`.

**Acceptance Scenarios**:

1. **Given** the stack is up and seeded, **When** a new-thread turn is posted to
   `/v1/turns`, **Then** the response is a single JSON object (no SSE) containing the
   turn's outcome and its `session_id`.
2. **Given** a turn that reaches a human-approval interrupt, **When** `/threads/{id}/approve`
   is posted with the resume payload, **Then** the turn resumes and completes correctly
   (mirrors `ui/app.py`'s already-debugged dual-key resume shape).
3. **Given** a turn that reaches a clarification interrupt, **When**
   `/threads/{id}/clarify` is posted with an answer, **Then** the turn resumes correctly.

---

### User Story 3 - A real conversation trace is visible in Langfuse (Priority: P1)

A developer runs a real turn and finds its trace in the Langfuse UI, grouped by
session, without any manual project/key setup step.

**Why this priority**: Observability that silently drops every span is worse than no
observability — it looks wired but isn't. This is the other half of "the stack actually
works," alongside Stories 1–2.

**Independent Test**: Run one real turn, open `http://localhost:3000`, and find the
trace in the Sessions view with the correct `session_id` and populated
`gen_ai.system`/`gen_ai.operation.name` attributes on its spans.

**Acceptance Scenarios**:

1. **Given** the stack is up with no manual Langfuse UI setup performed, **When**
   Langfuse starts, **Then** its project and API key pair are already provisioned
   (headless init) and match `.env`'s `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`.
2. **Given** a completed turn, **When** its trace is queried in Langfuse, **Then** the
   spans are grouped under the turn's real `session_id`, not left ungrouped.

---

### User Story 4 - specs/003-mcp-tool-layer's backlog is closed (Priority: P2)

The MCP tool layer (Phase 2) has 36 of 72 spec-kit tasks still unchecked, including a
missing handoff document. This story closes all of them.

**Why this priority**: Lower urgency than making the stack run at all, but it's a large
pre-existing gap in test coverage for a "done" phase, found by the same audit, and the
user chose to fold it in here rather than leave it open indefinitely.

**Independent Test**: `python -m pytest mcp/tests/ -v` passes in full, and every
checkbox in `specs/003-mcp-tool-layer/tasks.md` is checked.

**Acceptance Scenarios**:

1. **Given** the full mcp/tests/ suite, **When** it runs, **Then** every test
   (registry, tenant isolation, error taxonomy, idempotency, resilience, entitlements,
   gaming read-only, tracing) passes.
2. **Given** `specs/003-mcp-tool-layer/tasks.md`, **When** this feature completes,
   **Then** every task is checked and the Phase 2 handoff exists.

---

### User Story 5 - Two adjacent, already-tracked loose ends are closed (Priority: P3)

Two small open items in other phases' task lists are unblocked by this feature's own
work and get closed as a side effect.

**Why this priority**: Small, low-risk, and naturally falls out of Stories 1 and 4 —
not worth its own phase.

**Independent Test**: `specs/006-policy-guardrails-hitl/tasks.md` T056 and
`specs/005-multi-agent-orchestration/tasks.md` T063 are both checked off with evidence.

**Acceptance Scenarios**:

1. **Given** the new root `Dockerfile` (Story 1), **When** it is built, **Then** it
   vendors `en_core_web_sm` (specs/006 T056), unblocking that task.
2. **Given** the stack is up end to end (Story 1), **When**
   `specs/005-multi-agent-orchestration/quickstart.md`'s scenario list is run against
   it, **Then** every scenario's expected outcome is confirmed (specs/005 T063).

### Edge Cases

- **`tenant_id` arrives in the request body with no edge authentication** in front of
  `resortflow-orchestrator` (same as the existing dev console). This is a known, disclosed
  gap, not fixed here — deferred to Phase 8 (Entra ID / Azure APIM). See Assumptions.
- **A wrong OTLP path/auth silently drops every span** (the exporter's existing
  `except Exception: pass` guard swallows misconfiguration). Verification for Story 3
  MUST confirm a real trace visually lands in the UI, not just that code runs without
  raising.
- **`sentence-transformers`' reranker model would otherwise download from Hugging Face
  Hub at container first-use** — vendored into the image at build time instead (same
  fix pattern as spaCy), so a fresh container needs no network egress for retrieval.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `docker-compose.yml`'s default profile MUST NOT declare a build for any
  service whose source directory doesn't exist (`jwt-issuer`, `edge-shim`).
- **FR-002**: A root `Dockerfile` MUST exist and build a production entrypoint for
  `resortflow-orchestrator` (a new `graph/api.py` FastAPI app) and be reused, via a `command:`
  override, for `hold-ledger-reaper`.
- **FR-003**: The `graph/api.py` entrypoint MUST support: posting a new-thread turn,
  resuming an interrupted turn via approval (dual-key payload), resuming via
  clarification, and reconnecting to an existing thread's state — reusing the resume
  payload shapes already validated in `ui/app.py`.
- **FR-004**: `observability/telemetry.py`'s OTLP exporter MUST target Langfuse's actual
  ingestion endpoint with correct authentication when Langfuse credentials are present,
  and MUST fall back to a generic OTLP path otherwise (no regression for a non-Langfuse
  backend).
- **FR-005**: Every span MUST carry the conversation's `session_id` as an attribute so
  traces group correctly in Langfuse's Sessions view.
- **FR-006**: Langfuse MUST provision its own project/user/API-key pair headlessly on
  first boot (no manual UI click-through required for a reproducible demo).
- **FR-007**: Every task in `specs/003-mcp-tool-layer/tasks.md` MUST be completed and
  checked off, and the Phase 2 handoff MUST exist.
- **FR-008**: `specs/006-policy-guardrails-hitl/tasks.md` T056 and
  `specs/005-multi-agent-orchestration/tasks.md` T063 MUST be completed and checked off.
- **FR-009**: This feature MUST NOT rename any node in `graph/nodes.py`'s registry
  (project-wide hard rule — node names are a persistence contract).

### Key Entities

- **Production Orchestrator Entrypoint**: The new `graph/api.py` FastAPI app — the
  containerized, production-shaped counterpart to the existing dev-only `ui/app.py`
  console.
- **Root Image**: The single Docker image (from the new root `Dockerfile`) shared by
  `resortflow-orchestrator` (default `CMD`) and `hold-ledger-reaper` (`command:` override).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `docker compose --profile sims --profile app up -d` succeeds with every
  service `Up`/healthy on a clean checkout, with zero manual intervention.
- **SC-002**: A real turn posted to `/v1/turns` completes correctly and its trace is
  visually confirmed in Langfuse's UI, grouped by `session_id`.
- **SC-003**: `python -m pytest mcp/tests/ graph/tests/ -v` passes in full; `ruff check`
  and `mypy` are clean on the touched packages.
- **SC-004**: Zero open checkboxes remain in `specs/003-mcp-tool-layer/tasks.md`; the
  two named items in specs/005 and specs/006 are closed.
- **SC-005**: `docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` shows Phase 7.5 between Phase 7
  and Phase 8 without renumbering either; the Phase 7.5 handoff exists.

## Assumptions

- Real end-user authentication in front of `resortflow-orchestrator` (Entra ID / Azure APIM)
  is explicitly Phase 8 scope — this feature documents the gap (ADR-049) rather than
  building throwaway auth that Phase 8 would just replace.
- `specs/006-policy-guardrails-hitl/tasks.md` T057 and
  `specs/007-eval-observability-gate` T048 (the two 40-turn adversarial-script re-runs)
  are explicitly OUT OF SCOPE for this feature — left exactly where they are, not
  accidentally implied by proximity to T056/US4.
- The `.claude/skills/speckit-*` skills were not available in the authoring environment;
  this feature's spec-kit artifacts were produced via the underlying
  `.specify/scripts/powershell/*.ps1` automation and hand-authored content instead,
  disclosed here rather than silently substituted.
- This is infrastructure/hardening work, not new guest-facing behavior — several
  "user stories" above describe an operator/developer rather than a guest, which is
  expected for this kind of feature.
