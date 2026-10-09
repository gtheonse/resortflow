# Phase 0 Research: MCP Tool Layer

**Feature**: `003-mcp-tool-layer` | Input: `spec.md`'s NEEDS CLARIFICATION list is empty
(fully resolved during `/speckit-clarify`), so this document resolves *implementation*
unknowns the spec deliberately left as plan-level — plus corrections discovered while reading
frozen inputs closely, and two blocking issues found during the `/speckit-analyze` review of
this plan (both recorded in D1).

---

## Corrections to frozen inputs (not decisions — conforming to what's already there)

### C1. `mcp-tool-contract.md`'s naming examples use vendor names, not domain names

The contract's Naming section states `<domain> ∈ {dining, entertainment, stay, wellness,
gaming}` — matching this feature's spec and the already-materialized
`infra/sql/001_hold_ledger.sql`'s `domain` CHECK constraint (`dining, entertainment, stay,
wellness, gaming`), which is authoritative because it's a live, deployed artifact, not prose.
But the same document's Examples line reads `resortflow.ticketing.hold_seats,
resortflow.casino.check_rg_status` — `ticketing` and `casino` are *vendor* names, not domain names,
and don't appear in the enum one paragraph earlier. This is an inconsistent illustration, not
a competing rule. Treated as a documentation defect: this feature's tools are named
`resortflow.entertainment.hold_seats` and `resortflow.gaming.check_rg_status`, and the two example tokens
in `mcp-tool-contract.md` should be corrected to match its own enum — a wording fix, not a
contract change, since the enum (the actual rule) is untouched.

### C2. Domain ↔ simulator mapping (carried from spec, restated for implementers)

| MCP domain | Upstream simulator | Auth scheme (Phase 1) |
|---|---|---|
| `dining` | `sims/dining` | Static `X-Dining-Key` |
| `entertainment` | `sims/ticketing` | OAuth2 client-credentials + HMAC `X-TM-Signature` |
| `stay` | `sims/pms` | OAuth2 client-credentials |
| `wellness` | `sims/amenities` | HTTP Basic |
| `gaming` | `sims/casino` | Bearer |

---

## D1. MCP server framework and process topology

**Decision**: The official MCP Python SDK (`mcp`, which includes `mcp.server.fastmcp.FastMCP`)
via the **streamable HTTP** transport. Five independent `FastMCP` instances — one per domain,
each its own tool registry — are mounted as ASGI sub-applications inside **one** Starlette
parent app, behind one shared authentication + tracing middleware chain, served by uvicorn as
the single `mcp-gateway` service already reserved in `docker-compose.yml` (port `9100`) and in
`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md`'s topology table (`mcp-gateway :9100 — MCP servers,
one per domain`).

**Two consequences of that SDK choice, found while reviewing this plan and resolved here:**

1. **Package name collision (blocking).** The SDK's import root is `mcp`. Track B's directory
   is also `mcp/`. A top-level package named `mcp` and the installed SDK cannot coexist —
   whichever the import system reaches first, the other is unimportable, and making `mcp/` a
   namespace directory instead just means the SDK wins and our own modules become
   unimportable. Switching to the standalone `fastmcp` distribution does not help, because it
   imports `mcp` itself. **Resolution**: the directory stays `mcp/` (AGENTS.md's track table,
   the constitution's file-disjoint list and the compose service all reference it), and the
   importable package inside it is `mcp/gateway/`, with `mcp/` carrying no `__init__.py`.
   `pyproject.toml` uses `packages.find where = [".", "mcp"]` plus `mypy_path = "mcp"`.
   Renaming the track directory was the alternative; it would have meant amending two
   governance documents to solve a naming problem.
2. **Build context (blocking).** `docker-compose.yml`'s `mcp-gateway` still carried Phase 0's
   placeholder `build.context: ./mcp` with no `dockerfile:` key. That context cannot build this
   gateway, because the image must `COPY observability/` from the repo root for ADR-016's
   tracer. **Resolution**: `context: .` plus `dockerfile: mcp/Dockerfile` — precisely the
   correction Phase 1 had to apply to its own five sim services
   (the Phase 1 handoff, "Post-implementation integration fix").

**Rationale**: "One MCP server per domain" is satisfied at the *protocol* level — five
genuinely separate `FastMCP` objects, five separate tool registries, an inspector connecting
to `/dining`, `/entertainment`, etc. sees five distinct servers — without multiplying
deployable units. `docker-compose.yml` already reserves exactly one port and one service for
this; five containers would mean either changing that frozen-by-Phase-0-topology skeleton or
running five gateways behind an ad hoc proxy neither the compose file nor the plan document
anticipates. A single process also makes the per-upstream circuit breaker's state trivially
correct (in-memory, one process, no cross-replica synchronization needed) — see D5.

**Alternatives considered**:
- *Five separate containers/services.* Cleaner process isolation (one domain's crash can't
  take another down) and independently scalable, but requires editing the frozen
  `docker-compose.yml` topology and the enterprise plan's stated port allocation, and buys
  isolation this phase doesn't need (no domain server holds meaningfully different load
  characteristics from another).
- *A single `FastMCP` instance with domain-prefixed tool names instead of five instances.*
  Rejected outright — it would make "one MCP server per domain" false at the protocol level,
  not just at the deployment level, and an inspector would see one server with ~31 tools
  instead of five servers with ≤8 each, defeating SC-001/002's per-domain enumeration.

→ **ADR-011**.

---

## D2. Caller identity: local token issuer and claim shape

**Decision**: A small in-process token issuer (`mcp/gateway/_shared/identity.py`), dev-only, signs
HS256 JWTs carrying `sub` (caller id), `tenant_id` (`MGV` | `MHB`), `scopes` (a list of
`<domain>:<action>` strings), `iat`, and a short `exp` (1 hour). One shared ASGI middleware
verifies signature, expiry, and required-claim presence on every request to any of the five
mounted sub-apps before it reaches a tool handler, and injects a `CallerIdentity` into a
context variable the tenant-injection and entitlement-check logic both read. A token that
fails any check is refused with a typed error before the tool body runs (FR-007).

**Rationale**: this satisfies the spec's clarification directly and, because verification is
one middleware ahead of all five sub-apps rather than five independent per-domain checks,
"tenant-crossing call impossible by construction" has exactly one place to be proven true —
which is what SC-003/SC-004 need to be testable as a single adversarial pass rather than five
separate ones.

**Alternatives considered**:
- *Opaque session token + server-side lookup table.* Simpler (no crypto), but the "verified
  claim" story becomes "trust our own lookup," which is a weaker proof for the same exit
  criterion — already rejected during clarify.
- *RS256 asymmetric signing.* More production-realistic (a real IdP would never hand out the
  verification key), but this is explicitly a local dev fixture (spec Assumptions), and
  asymmetric key management buys nothing here — the property under test is "a caller cannot
  forge its own tenancy," which HS256 with a server-held secret already proves.

→ **ADR-012**.

---

## D3. Entitlement vocabulary and enforcement point

**Decision** (formalizing the `/speckit-clarify` outcome): every tool declares exactly one of
`read` / `hold` / `commit` for its domain. Enforcement is a single decorator/dependency
(`mcp/gateway/_shared/entitlements.py`) applied uniformly to every tool at registration time, reading
the tool's declared entitlement and the verified `CallerIdentity.scopes` from D2 — never the
tool's name, never a per-domain special case.

**Rationale**: already justified at the spec level (Q2 of `/speckit-clarify`) — the vocabulary
tracks Principle III's reversible/irreversible boundary, not mutation itself, so a domain
subgraph can be granted `read`+`hold` and structurally cannot invoke a `commit` tool no matter
what a model decides to do.

→ **ADR-013** (formalizes a decision already made with the user; recorded because it meets the
significance bar — it constrains every future domain server this project adds).

---

## D4. Vendor response validation

**Decision**: every vendor call has a **hand-authored Pydantic v2 response model** per
endpoint, mirroring that endpoint's frozen OpenAPI response schema exactly (field names,
types, required-ness). A response is processed as: parse JSON → on decode failure, `RETRYABLE`
(FR-034); `Model.model_validate(body)` → on `ValidationError`, `TERMINAL` (FR-035); on success,
the validated model is what the tool returns, so no unvalidated field can reach a caller
(FR-036 falls out of this for free — there is no code path that forwards a field without it
having passed through the model).

**Rationale**: reuses a dependency already in the project (`pydantic`, no new library), and
gives FR-037's "effect status" logic one unambiguous place to hook in on a mutating call's
`ValidationError` path (see D6). It also means the ~31 response models double as living
documentation of exactly what each tool returns — useful for the tasks that come after this
plan.

**Alternatives considered**:
- *Runtime validation directly against the frozen OpenAPI YAML* (e.g. `openapi-core`).
  Avoids hand-authoring models and can't drift from the contract by construction, but
  produces validation errors shaped around JSON Schema violations rather than Python
  objects a tool can return directly — every tool would still need to convert the validated
  dict into something typed to return over MCP, so the "no double work" advantage is smaller
  than it looks, and hand-authored models are also what makes composite tools ("compose several
  vendor calls into one guest-intent shape," FR-004) readable.

→ **ADR-014**.

---

## D5. Retry and circuit breaker mechanics

**Decision**: `tenacity` for retry with jitter (`wait_exponential_jitter`, base 0.25s, cap 4s,
3 attempts total including the first, honoring a vendor-supplied `retry_after_seconds` when
present per FR-023). A small hand-rolled per-upstream circuit breaker
(`mcp/gateway/_shared/resilience.py`) — closed → open after 5 consecutive failures → half-open after a
30s cooldown allows exactly one probe → closed on success / open again on failure. Five
breaker instances, one per upstream vendor, held in the single gateway process's memory (see
D1 — no cross-process synchronization needed).

**Rationale**: `tenacity` is the standard choice for the retry half — no reason to hand-roll
backoff-with-jitter. The breaker is hand-rolled rather than pulling in a library because its
required behavior is narrow and exactly specified (FR-024/025/031: per-vendor, uniform
blocking with **no** idempotency-key exemption), and a ~60-line state machine this precise is
easier to prove correct against SC-010 with direct unit tests than to verify a library
matches a requirement it wasn't written against (most breaker libraries default to uniform
blocking, but "prove it" is cheaper on code you wrote for exactly this contract).

Not ADR-worthy: routine library selection (`tenacity`) plus an internal implementation detail
(the breaker) that constrains no other feature's interface — a later feature calls tools
through the same MCP surface regardless of what sits behind FR-024.

---

## D6. Realizing FR-037 without touching `hold_ledger`

**Finding**: FR-037 requires that an indeterminate mutating-call outcome be distinguishable
from an ordinary terminal failure — but the natural place to *persist* that distinction,
`infra/sql/001_hold_ledger.sql`'s `hold_ledger.status`, is a **different feature's** frozen
artifact (feature 001; `CHECK (status IN ('PENDING','HELD','CONFIRMED','RELEASED','EXPIRED',
'ORPHAN_RELEASED'))`, no `UNRESOLVED` value), and per `ResortFlow-HITL-Durability-State.md` §3 /
ADR-019, the ledger is written by whatever node places and resolves a hold — the future
graph/orchestration layer (Track C, Phase 4–5), not this feature. This feature has no
database of its own (`mcp/` is a stateless tool layer; nothing in the tech stack assigns it
storage), and shouldn't invent one just to carry one bit of signal one hop.

**Decision**: this feature's obligation ends at making the signal available on the returned
failure, for whatever later calls the tool (eventually, the Phase 4–5 ledger-writing node) to
persist. That requires one additive field on the frozen `tool-error.schema.json` envelope —
`effect_status: "unresolved" | null`, optional, present only when FR-032 or FR-035-on-a-
mutating-call applies. This is a **backward-compatible amendment to a Phase 0 frozen
contract**: `additionalProperties: false` already forbids it, so the field literally cannot be
added without touching that file.

**This was raised, not assumed.** Per the working agreement ("Phase 0 contracts are frozen...
raise it as a blocker, do not edit unilaterally"), the amendment was proposed in full (ADR-015)
with the exact diff, held for explicit confirmation, and — once the user confirmed it — applied
to `docs/contracts/schemas/tool-error.schema.json`. It's the one place this plan touched bytes
another feature already froze, and it went through the constitution's prescribed path rather
than being assumed.

A second, smaller finding worth recording now so it isn't rediscovered later: extending
`hold_ledger.status` with an `UNRESOLVED` value (or equivalent) is *not* this feature's call to
make or implement — it's Track C's, when Phase 4–5 designs the reconciler. Noted here only so
that work has a paper trail back to where the need was discovered.

**Alternatives considered**:
- *A companion table owned by this feature* (e.g. `mcp_unresolved_effects`). Avoids touching
  the frozen schema, but gives the gateway a database it has no other reason to hold, and creates a
  second place (beyond the eventual `hold_ledger`) a reconciler would have to check — solving
  an ownership problem by creating a duplication problem.
- *Encode the signal in the existing free-text `detail` field.* Rejected — the constitution's
  stance on structured-over-free-text data ("never carry a dietary restriction only in free
  text") applies with equal force to a machine-actionable safety signal; parsing prose to
  decide a database write is exactly the failure mode that principle exists to prevent.

→ **ADR-015** (Accepted — confirmed and applied within this planning session; see plan.md's
Constitution Check).

---

## D7. Span emission scope for this phase

**Finding**: `observability/attributes.py`'s docstring states "emission (the tracer, the
exporter, the dual-backend callback handlers) is feature 007's responsibility," and Phase 1's
own plan reasoned "no feature before 007 emits spans" — but that same plan's Observability
gate reasoning also says, in the very next clause, "spans for calls *to* these sims are
emitted by the MCP adapters that make them" — i.e. Phase 1's own plan already assigned this
job to this feature.

**Decision**: this feature stands up a **bare, non-production tracer** — `opentelemetry-api` +
`opentelemetry-sdk` with an in-memory span processor for tests and a console exporter for
local dev visibility only. Every tool invocation emits exactly one span carrying attributes
drawn only from `observability/attributes.py` (FR-027/028), which satisfies "the MCP adapters
emit spans for calls to these sims" without building the OTLP exporter, the dual-backend
(Langfuse + Azure Monitor) callback handlers, or any production configuration — that
remains entirely Feature 007's build, matching the spec's own Assumption ("verified by
asserting on emitted attributes, not by backend delivery").

**Rationale**: this reconciles the two true statements above rather than picking one and
ignoring the other — Feature 007 owns *how spans leave the process and where they land*;
this feature owns *that a span exists at all for every vendor call it makes, shaped
correctly*. Building the real backend now would be scope creep into Feature 007; building
nothing would leave Phase 1's own plan's claim unfulfilled and make FR-027 untestable except
by mocking a tracer that doesn't exist.

→ **ADR-016**.

---

## D8. Tool roster consolidation

Full table in `contracts/tool-registry.md`. Summary: 8 dining + 7 entertainment + 7 stay + 6
wellness + 3 gaming = **31 tools**, every domain at or under the 8-tool cap (SC-002), gaming
carrying only `check_*`/`get_*`/`list_*` verbs and zero `hold`/`commit` entitlements anywhere
in that namespace (SC-011).

**Composite-tool design rule** (not obvious from the spec alone, worth stating explicitly for
whoever builds `tasks.md` next): **a composite tool may freely bundle multiple read-only
vendor calls** (no entitlement conflict, no idempotency concern), **but a composite
*mutating* tool must resolve to exactly one entitlement and one point of commitment.** Two
vendor calls that would need different entitlements (e.g., releasing a hold vs. cancelling an
already-confirmed booking) cannot be merged into one tool even when they serve a similar
guest intent ("I don't want this anymore") — doing so would make one tool's risk level
depend on a runtime argument, which is the mode-flag anti-pattern the spec's Assumptions
already reject, wearing an entitlement disguise instead of a naming one.

**Composite idempotency-key derivation** (elaborating ADR-006, not a new decision): a
composite tool receives one caller-supplied `idempotency_key` argument, per the frozen
`derive(session_id, hold_id, attempt)` contract. For each of its N sub-calls it derives a
per-sub-call key as `f"{idempotency_key}:{slot_name}"`, where `slot_name` is a fixed string
declared by the tool's own definition (e.g., `"hold_room"`), never derived from a vendor
response. This is deterministic across retries of the whole composite by construction — the
spec's carried-forward note (Q1's amendment) is satisfied without inventing a new formula.
