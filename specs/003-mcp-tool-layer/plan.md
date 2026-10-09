# Implementation Plan: MCP Tool Layer

**Branch**: `003-mcp-tool-layer` | **Date**: 2026-09-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/003-mcp-tool-layer/spec.md`

## Summary

Build the governed integration surface Principle II names: five independent MCP tool
registries — `dining`, `entertainment`, `stay`, `wellness`, `gaming` — deployed as one
multi-mounted process (`mcp-gateway`, the container `docker-compose.yml` already reserves)
calling Phase 1's five live simulators as their sole upstream. Every one of the 31 tools
(`contracts/tool-registry.md`) follows the frozen naming/idempotency/error-shape/scope
contract: `resortflow.<domain>.<verb>_<noun>` names, `tenant_id` derived only from a verified
caller-identity token's claims (never a tool argument), a caller-supplied idempotency key on
every mutating call, and every vendor outcome normalized to the four-class `ErrorClass`
taxonomy at the MCP boundary — no vendor envelope shape ever reaches a caller.

Two things this plan resolves that the spec deliberately left open: the entitlement
vocabulary is `read`/`hold`/`commit` per domain, tracking Principle III's reversible/
irreversible boundary rather than mutation itself (ADR-013); and realizing FR-030/FR-037's
signals (a composite tool's already-completed sub-calls; a mutating call's indeterminate
effect) required two small, additive fields on the Phase 0 frozen `tool-error.schema.json`.
That amendment was raised as a blocker rather than applied unilaterally (per the working
agreement's contract-freeze rule), confirmed by the user, and applied as **ADR-015 (Accepted)**
in this same planning session.

This feature ships zero LLM nodes and zero graph nodes — same footing as Phase 1. It is a
thin, stateless adapter layer: retry-with-jitter and a circuit breaker per upstream, a
JWT-verifying identity middleware, an entitlement check, response validation against
hand-authored Pydantic models, and span emission against the frozen telemetry vocabulary.

## Technical Context

**Language/Version**: Python 3.11+ (matches features 001/002)

**Primary Dependencies**: `mcp` (official MCP Python SDK; `mcp.server.fastmcp.FastMCP`),
`starlette` + `uvicorn` (ASGI mounting of the five `FastMCP` sub-apps, already transitive via
`fastapi`), `httpx` (already a dependency — calling the five sims), `pydantic>=2` (already a
dependency — response models), `pyjwt` (token signing/verification, new), `tenacity`
(retry-with-jitter, new), `opentelemetry-api` + `opentelemetry-sdk` (span emission against
the frozen vocabulary, new — see ADR-016). No new dependency for the circuit breaker: hand-
rolled (`research.md` D5).

**Storage**: **No durable state** — no database, no file-backed state, no migration owned by
this feature. It does hold in-process ephemeral state that dies with the process: per-vendor
circuit-breaker counters and the OAuth2 token cache. The write-ahead ledger (`hold_ledger`,
feature 001) is a different feature's table, written by a different feature's code
(`research.md` D6); this feature only ever produces the signal a future writer needs, and
persists nothing itself.

**Testing**: `pytest` + `pytest-asyncio` (already dependencies). An MCP inspector run
(`@modelcontextprotocol/inspector`) against the live gateway for schema-validity proof
(SC-001/002); `httpx`-driven integration tests against the running Phase 1 sims for the
adversarial tenant-isolation pass (SC-003/004), the entitlement matrix (SC-005), and the
fault-injection-driven classification suite (SC-006/007/016) using Phase 1's `X-Sim-Fault`
header plus the new `schema_drift` mode this plan flags as a Track A cross-track dependency
(`spec.md` Assumptions); unit tests for the circuit breaker state machine and idempotency-key
derivation in isolation (both `[CRIT]` per `docs/models.md` — never economy-tiered).

**Target Platform**: One additional Docker Compose service (`mcp-gateway`, `app` profile,
already reserved), Linux container — unchanged from the existing topology.

**Project Type**: One backend service (five independently-registered tool registries, one
process — ADR-011) plus a small internal shared library (`mcp/gateway/_shared/`) all five
domain servers import, mirroring `sims/_shared/`'s established pattern from Phase 1.

**Package layout**: Track B's directory stays `mcp/` (AGENTS.md's track table, the
constitution's file-disjoint list, and `docker-compose.yml` all point there), but the
**importable package inside it is `mcp/gateway/`** — `mcp/` itself carries no `__init__.py`.
This is not cosmetic: a top-level package named `mcp` would collide with the MCP SDK's own
`mcp` package (imported as `mcp.server.fastmcp`), and the two cannot coexist in one
environment — whichever wins, the other becomes unimportable. `fastmcp` does not avoid this,
since it imports `mcp` itself. See `research.md` D1.

**Performance Goals**: Not a throughput-sensitive phase. The one real constraint: the retry
budget (3 attempts, exponential-jitter backoff capped at 4s) plus the circuit breaker's 30s
cooldown must not make a single failing-vendor test scenario the slow part of CI — matching
Phase 1's own reasoning for compressing hold TTLs in tests.

**Constraints**:
- Every tool's declared name, entitlement, and idempotency requirement must match
  `contracts/tool-registry.md` exactly — that table is this plan's frozen output for
  `/speckit-tasks`, the same role Phase 1's `seed-data-spec.md` played for its feature.
- `tenant_id` MUST NEVER be read from a tool argument, only from `CallerIdentity` (FR-009) —
  `[CRIT]`, never economy-tiered per `docs/models.md`'s "Tenant filter construction" row
  (explicitly listing Phase 2).
- Idempotency-key derivation and validation logic is `[CRIT]` per the same table's
  "Idempotency key derivation" row (explicitly listing Phase 0 *and* 2).
- No tool may hold a vendor SDK or vendor-specific client — `httpx` calls with per-vendor
  auth handling live entirely inside `mcp/gateway/_shared/` and `mcp/gateway/<domain>/`, never
  imported by anything outside them (Principle II, ADR-005).
- The `gaming` domain server's source file MUST NOT import `_shared.idempotency` at all
  (mirroring Phase 1's `sims/casino/main.py` having "no holds import from `_shared.holds`") —
  no mutating gaming tool exists, so no idempotency key is ever needed there, and the absence
  of that import is the checkable structural property. It **does** import
  `_shared.entitlements`, since `gaming:read` still has to be enforced. Verified by static
  import inspection in a test, not by convention.

**Scale/Scope**: 5 domain servers, 31 tools total (8/7/7/6/3 per domain — none over the
8-tool cap), 15 possible `<domain>:<action>` scope strings of which 13 are declared by at
least one existing tool (`gaming:hold` and `gaming:commit` are constructible but inert, since
no such tool exists), 5 independent circuit breaker instances, one shared
identity-verification middleware.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

Evaluated against `.specify/memory/constitution.md` v1.1.0.

| Principle | Verdict | Evidence |
|---|---|---|
| **I. Ambiguity-Based Routing** | ✅ PASS | Zero LLM nodes. Every tool is deterministic: parse, validate, classify, forward. Tool *selection* by an agent is out of scope for this feature entirely (spec Assumptions) — this feature only has to stay under the tool-count threshold the principle cites (≤8/domain, ≤40 total, SC-002), which it does at 31. |
| **II. Governed Integration Surface** | ✅ PASS — *this feature's entire purpose* | `tenant_id` derived solely from a verified token claim (FR-008, ADR-012), never a caller argument (FR-009, `[CRIT]`). Every vendor error normalized to the four-class taxonomy at this exact boundary (FR-019–038). The `gaming` domain contains zero mutating tools — enforced by the tool's absence from `contracts/tool-registry.md`'s registry, checked by static import inspection (Constraints), not a prompt. |
| **III. Two-Phase Mutation** | ✅ PASS — *the entitlement design directly encodes it* | Every mutating tool requires an idempotency key derived per the frozen `(session_id, hold_id, attempt)` formula (FR-015, ADR-006 elaborated in `data-model.md`). The entitlement vocabulary (`read`/`hold`/`commit`, ADR-013) makes the reversible/irreversible boundary a structural grant, not a convention — a caller with `hold` but not `commit` cannot cross it, which is exactly where ADR-006 already places the human-approval interrupt. Composite-tool partial failure (FR-030) never self-compensates; compensation stays the saga layer's job (Phase 4–5), matching Principle III's own scoping. |
| **IV. Policy Is Code, Not Prompt** | ✅ PASS — *this feature carries the class, not the policy* | `POLICY_BLOCKED` is a class this feature's taxonomy can express (FR-019) and the frozen `403 → POLICY_BLOCKED` casino/self-exclusion mapping (`specs/001-contracts-foundation/data-model.md`) flows through this feature's classifier with no gaming-specific code (`contracts/tool-registry.md`'s `get_loyalty_status` note) — but the self-exclusion *check itself*, allergen assertion, and jurisdiction messaging remain Track G's, per spec Assumptions. This feature doesn't invent policy logic; it makes sure the one policy signal Phase 1 already emits isn't lost in translation. **That signal is verified end-to-end, not just in unit tests**: Phase 1 seeded ≥1 `self_excluded` guest per tenant precisely so a real request for that guest's offers can be shown to surface `POLICY_BLOCKED` and never offers (`tasks.md` T072) — the most regulator-relevant path in the feature does not rest on a mocked vendor response. |
| **V. Durable, Inspectable State** | ✅ PASS — *by holding no durable state, deliberately* | This feature persists nothing durable; its only state is in-process and dies with the process (breaker counters, OAuth2 token cache — Technical Context, Storage). It therefore cannot conflate conversation state with the write-ahead ledger, because it touches neither: `hold_ledger` is a different feature's table, written by different feature's code (`research.md` D6). Where this feature's own error-normalization work discovered a genuine gap in a *different* feature's frozen schema (FR-030/037 needing envelope room), it raised that as ADR-015 — confirmed and applied — rather than working around it with a database of its own. |
| **VI. Declared Model Tier** | ✅ PASS | Zero LLM-invoking nodes — runtime tier not applicable, same as Phase 1. Build-time: per `docs/models.md`, idempotency-key derivation and tenant-filter/injection logic are `[CRIT]` (Phase 2 named explicitly in both rows) and MUST NOT be tiered ECONOMY regardless of how mechanical they look; `tasks.md` tiers them `MID` or `FRONTIER`. Ordinary MCP adapter wiring (tool registration, response-model plumbing) is `MID` per the same table's explicit "MCP tool adapters" row. Sim-adjacent Pydantic response models mirroring a frozen contract are `ECONOMY`-eligible, matching the same table's "Pydantic models from a written contract" row. |

| Development Standard | Verdict | Evidence |
|---|---|---|
| Determinism first | ✅ PASS | No model call anywhere. Error classification (FR-019–038) is a pure function of vendor response shape — `[CRIT]`, tested exhaustively, never a judgment call left to a model. |
| Contract stability | ✅ **PASS — via the standard's own escape valve, not around it** | `docs/contracts/openapi/*.yaml` are unchanged by this feature. Phase 1's sims are unchanged in every respect that FR-005 governs — their frozen contract surface — with one disclosed exception carried through the integration commit: a new test-only `schema_drift` fault mode, which lives outside the frozen contract entirely per ADR-008's established precedent and is why FR-005 now carries an explicit carve-out for out-of-band test controls (Recorded Deviation 2). `mcp-tool-contract.md`'s two vendor-named examples are corrected to match its own domain enum (research.md C1 — a wording fix, not a rule change). One genuine amendment against `tool-error.schema.json` (ADR-015) was raised as a blocker rather than applied unilaterally — exactly this standard's own escape valve: "a track needs a contract change, raise it as a blocker — do not edit unilaterally." The user confirmed it; it is now applied. This plan named the one place it touched frozen bytes and got sign-off before doing so, rather than being silently compliant by never needing to ask. |
| Every decision documented | ✅ PASS | Six ADRs obligated — see the table below. All six Accepted. |
| Test before advance | ✅ PASS | Contract-schema-validity via MCP inspector (SC-001/002), the mandatory tenant-isolation and saga-permutation test categories this project requires (SC-003/004, SC-008), plus five more success-criteria-driven suites (entitlement matrix, error taxonomy, resilience, gaming read-only, observability). |
| Observability not optional | ✅ PASS — *resolved, not deferred* | ADR-016 reconciles the two true-but-apparently-conflicting statements already on record (`observability/attributes.py`'s "emission is feature 007's job" vs. Phase 1's own plan's "spans for calls to these sims are emitted by the MCP adapters that make them") by splitting *producing a correctly-shaped span* (this feature, now) from *wiring a production backend* (Feature 007, later). FR-027/028 are genuinely testable this phase, not asserted against a tracer that doesn't exist. |
| No real client names | ✅ PASS | Meridian Resorts International, `MGV`, `MHB` only — matches every frozen contract and this feature's own new artifacts. |

**Gate result: PASS.** ADR-015's schema amendment was this plan's single point of contact
with a frozen Phase 0 artifact; it was raised as a blocker, confirmed by the user, and applied
to `docs/contracts/schemas/tool-error.schema.json` within this planning session — see
"Recorded deviations" below and the ADR itself. **Re-checked after Phase 1 design**
(`research.md`, `data-model.md`, `contracts/`, `quickstart.md`) and again after the schema
amendment was applied: no new violations surfaced; the two findings that emerged during
design (the `mcp-tool-contract.md` example inconsistency, and `hold_ledger`'s lack of room
for an unresolved state) both strengthened this feature's own Contract Stability and
Durable-State verdicts rather than weakening them — the first was fixed as prose, the second
was resolved by *not* touching a file this feature doesn't own, and the one file this feature
did need to touch was changed only after explicit sign-off.

### Recorded deviations

1. **`tool-error.schema.json` amended (ADR-015).** FR-030/FR-037 are now fully realized: the
   schema carries `completed_sub_calls` and `effect_status`, applied per the diff in
   `contracts/tool-error-envelope-amendment.md`. This is recorded as a deviation from "Phase 0
   contracts are frozen" — a justified, disclosed, user-confirmed one — not a silent edit.
2. **A new sim fault-injection mode (`schema_drift`) is needed to test FR-035/038 but belongs
   to Track A's already-shipped Phase 1 code**, not this feature's `mcp/` track (spec
   Assumptions, carried into `research.md`). Scheduled through the integration commit the
   working agreement already reserves for exactly this situation — not silently assumed to
   already exist, and not built unilaterally into `sims/` by this feature.

## Project Structure

### Documentation (this feature)

```text
specs/003-mcp-tool-layer/
├── plan.md                                    # This file
├── research.md                                # Phase 0 output — 8 decisions, 2 corrections
├── data-model.md                              # Phase 1 output — entities, envelope amendment, breaker state machine
├── quickstart.md                              # Phase 1 output — 7 runnable validation scenarios
├── contracts/                                 # Phase 1 output
│   ├── tool-registry.md                       # The 31-tool roster — frozen output for /speckit-tasks
│   ├── caller-identity-contract.md            # Token claim shape, issuer, verification rules
│   └── tool-error-envelope-amendment.md       # Diff applied to tool-error.schema.json (ADR-015, Accepted)
├── checklists/
│   └── requirements.md                        # 16/16 passing (re-validated during /speckit-clarify)
└── tasks.md                                    # Created by /speckit-tasks, not by this command
```

### Source Code (repository root)

```text
mcp/                                    # Track B — this feature's entire runtime surface
├── Dockerfile                          # Repo-root build context; COPY observability + COPY mcp/gateway
├── gateway/                            # THE importable package (mcp/ itself has no __init__.py — see Package layout)
│   ├── __init__.py
│   ├── main.py                         # Entrypoint: builds the gateway ASGI app, uvicorn on :9100 (ADR-011)
│   ├── settings.py                     # Sim base URLs, vendor credentials, MCP_DEV_SIGNING_KEY
│   ├── _shared/                        # Internal library imported by all five domain servers
│   │   ├── __init__.py
│   │   ├── identity.py                 # LocalTokenIssuer, verify_token, CallerIdentity (ADR-012)
│   │   ├── middleware.py               # The single auth chokepoint ahead of all five sub-apps
│   │   ├── tenancy.py                  # Server-side X-Tenant-Id injection from the verified claim
│   │   ├── entitlements.py             # read/hold/commit check, applied uniformly at registration (ADR-013)
│   │   ├── idempotency.py              # Presence/shape validation of the caller-supplied key; composite sub-call derivation
│   │   ├── errors.py                   # NormalizedFailure construction, FR-033 fallback, response-validation classification (ADR-014)
│   │   ├── resilience.py               # tenacity retry wrapper + hand-rolled per-upstream circuit breaker (research.md D5)
│   │   ├── upstream.py                 # One httpx client per vendor, wired through tenancy/retry/breaker/validation
│   │   ├── vendor_auth.py              # The five divergent vendor auth schemes + OAuth2 token cache
│   │   ├── registry.py                 # Tool registration: name regex, entitlement, idempotency, tracing, composites
│   │   └── tracing.py                  # Bare tracer using observability/attributes.py's vocabulary only (ADR-016)
│   ├── dining/
│   │   ├── server.py                   # FastMCP() instance + 8 tools (contracts/tool-registry.md)
│   │   └── models.py                   # Pydantic response models mirroring dining-sim.openapi.yaml
│   ├── entertainment/
│   │   ├── server.py                   # 7 tools; poll-only fulfillment via get_order_status (no webhook ingress)
│   │   └── models.py
│   ├── stay/
│   │   ├── server.py                   # 7 tools
│   │   └── models.py
│   ├── wellness/
│   │   ├── server.py                   # 6 tools
│   │   └── models.py
│   └── gaming/
│       ├── server.py                   # 3 tools; MUST NOT import _shared.idempotency at all (no mutating tool exists)
│       └── models.py
└── tests/                              # Track B's own tests — kept inside mcp/, mirroring sims/tests/
    ├── contract/                       # Registry schema validity + naming, one pass per domain
    ├── integration/                    # tenant isolation, entitlement matrix, idempotent retry, breaker, error taxonomy
    ├── unit/                           # classification, idempotency derivation, breaker state machine, tracing
    └── fixtures/

docs/contracts/openapi/                 # Frozen inputs — unchanged by this feature
docs/contracts/mcp-tool-contract.md     # Two example tokens corrected (wording fix — research.md C1)
docs/contracts/schemas/tool-error.schema.json  # AMENDED — completed_sub_calls, effect_status added (ADR-015)
docker-compose.yml                      # AMENDED — mcp-gateway build corrected to context: . + dockerfile: mcp/Dockerfile
pyproject.toml                          # AMENDED — new deps; packages.find where=[".","mcp"]; mypy files + mypy_path
Makefile                                # AMENDED — gateway target; MCP_DEV_SIGNING_KEY generated by `make seed`
.env.example                            # AMENDED — sim base URLs, vendor credentials, signing key
sims/_shared/faults.py                  # Track A, integration commit — new `schema_drift` mode (deviation 2)
docs/adr/                               # 011–016 added by this feature, all Accepted
```

**Structure Decision**: All runtime code lives under `mcp/`, matching Track B's ownership in
`AGENTS.md`. Tests live at `mcp/tests/`, mirroring Phase 1's `sims/tests/` precedent rather
than the shared top-level `tests/` directory Track C already populated — same file-disjoint
reasoning Phase 1's plan gave for its own structure. `_shared/` exists for the same reason
`sims/_shared/` did: five independent re-implementations of identity verification,
entitlement checking, retry/breaker logic, and error normalization would mean five chances to
drift from each other on exactly the logic this feature's `[CRIT]` tags cover.

Like Phase 1, this feature does touch cross-cutting files, and they are enumerated in the tree
above rather than discovered during implementation: `docker-compose.yml` (the `mcp-gateway`
service still carried Phase 0's placeholder `context: ./mcp`, which cannot build — the image
must `COPY observability/` from the repo root for ADR-016's tracer, so the context becomes `.`
with an explicit `dockerfile: mcp/Dockerfile`, exactly the correction Phase 1 had to make for
its five sim services), `pyproject.toml` (dependencies plus the two-root `packages.find`
needed by the `mcp/gateway/` layout), `Makefile` and `.env.example` (gateway target and dev
signing key), and `sims/_shared/faults.py` — Track A's file, scheduled through the integration
commit the working agreement reserves, never edited unilaterally by this track.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations requiring justification under the Complexity Tracking table's terms — the one
non-trivial item (ADR-015's frozen-schema amendment) is not a *violation* of Contract
Stability, it's that standard's own prescribed path followed explicitly (raise, get
confirmation, then edit) rather than skipped. This section is intentionally empty for the
same reason Phase 1's was.

## Architecture Decision Records *(mandatory — ResortFlow project override)*

Applying the significance bar: a decision qualifies if it constrains a later feature, could
reasonably have gone the other way, would be breaking to reverse, or trades one project value
against another. All six below meet it — five originate in this plan, one (013) formalizes a
decision reached with the user during `/speckit-clarify` but not yet written up, which still
constrains every domain server this project ever adds and so still earns its own record.

### Plan completion gate

| ADR | Decision | Status |
|-----|----------|--------|
| [011](../../docs/adr/011-mcp-single-process-multi-mount.md) | Five domain MCP servers as one multi-mounted process, not five containers | [x] drafted / [x] complete |
| [012](../../docs/adr/012-caller-identity-local-issuer.md) | Caller identity via a local, signed-token issuer (HS256 JWT) | [x] drafted / [x] complete |
| [013](../../docs/adr/013-entitlement-reversibility-vocabulary.md) | Entitlement vocabulary is `read`/`hold`/`commit`, tracking reversibility not mutation | [x] drafted / [x] complete |
| [014](../../docs/adr/014-vendor-response-validation.md) | Vendor response validation via hand-authored Pydantic models per endpoint | [x] drafted / [x] complete |
| [015](../../docs/adr/015-tool-error-envelope-amendment.md) | Amend `tool-error.schema.json` with `completed_sub_calls` + `effect_status` | [x] drafted / [x] complete — confirmed and applied |
| [016](../../docs/adr/016-phase2-span-emission-scope.md) | This feature emits real spans on a bare tracer; production backend wiring stays Feature 007's | [x] drafted / [x] complete |

**This plan is now fully complete.** ADR-015 was the one open item; it was raised, confirmed,
and applied within this planning session, so FR-030's `completed_sub_calls` payload and
FR-037's `effect_status` signal are both realizable exactly as specified — the companion-table
fallback considered in `research.md` D6 is no longer needed.

Decisions made in this plan that did **not** meet the significance bar, and why:

- Choosing `tenacity` for retry-with-jitter and `pyjwt` for token signing — conventional
  library choices for well-understood problems, reversible without touching any interface,
  constrain no downstream feature. (research.md D5, D2's alternatives)
- The hand-rolled circuit breaker's exact parameters (5 consecutive failures, 30s cooldown) —
  tunable implementation detail, explicitly left to this plan by the spec's own Assumptions
  ("exact retry counts... failure thresholds and recovery intervals are implementation-plan
  details").
- The composite sub-call idempotency-key derivation (`f"{idempotency_key}:{slot_name}"`) —
  not a new decision, an elaboration of ADR-006's already-frozen top-level formula applied one
  level deeper (research.md D8).
- Correcting `mcp-tool-contract.md`'s two vendor-named examples to match its own domain enum —
  a wording fix to already-existing, unambiguous prose, not a contract change (research.md
  C1).
