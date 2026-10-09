# Data Model: MCP Tool Layer

**Feature**: `003-mcp-tool-layer` | Derived from `spec.md`'s Key Entities, elaborated to the
level `/speckit-tasks` needs. This feature persists nothing of its own (see `research.md`
D6) — every model below is an in-process shape (Pydantic), not a table.

---

## CallerIdentity

The verified claim set produced by `mcp/gateway/_shared/identity.py` after signature/expiry
verification. Immutable for the lifetime of one request; the sole source of tenancy (FR-008).

| Field | Type | Notes |
|---|---|---|
| `sub` | `str` | Caller id (a domain subgraph, a test harness — not a guest identifier) |
| `tenant_id` | `Literal["MGV", "MHB"]` | The only two values `hold_ledger.tenant_id` and every seeded sim recognize (`domain` is a separate column on that table, constrained to the five domain names). Any other value fails verification (FR-007) rather than producing a third tenant. |
| `scopes` | `list[str]` | Each element `"<domain>:<action>"`, `action ∈ {read, hold, commit}` |
| `iat` / `exp` | `int` (unix ts) | `exp` short-lived (1h); expired tokens fail verification |

**Invariant**: nothing downstream ever constructs a `CallerIdentity` from a tool argument.
The only constructor is "verify a bearer token, or raise."

---

## Tool

A static, registration-time declaration — not a runtime record. One per entry in
`contracts/tool-registry.md`.

| Field | Type | Notes |
|---|---|---|
| `name` | `str` | `resortflow.<domain>.<verb>_<noun>`, validated against the frozen regex at registration time (fails startup, not a request, if violated) |
| `domain` | `Literal["dining","entertainment","stay","wellness","gaming"]` | |
| `entitlement_action` | `Literal["read","hold","commit"]` | Exactly one (FR-012) |
| `is_mutating` | `bool` | Derived: `entitlement_action != "read"`. Determines whether `idempotency_key` is required (FR-015/016) |
| `is_composite` | `bool` | Whether this tool fans out to >1 vendor call |
| `sub_calls` | `list[SubCallSpec]` | Empty unless `is_composite` |
| `input_model` / `output_model` | Pydantic model classes | Published as the MCP tool's JSON Schema (FR-002) |

### `SubCallSpec` (composite tools only)

| Field | Type | Notes |
|---|---|---|
| `slot_name` | `str` | Fixed at definition time, e.g. `"hold_room"`. Never derived from a response. Used to build the per-sub-call idempotency key (research.md D8). |
| `vendor_operation` | `str` | The frozen OpenAPI `operationId` this slot calls |
| `required_for_success` | `bool` | If `true` and this slot fails, the whole composite fails per FR-030; if `false`, a failure here is an enrichment miss, not a composite failure (e.g., an optional "upcoming events" enrichment on `list_venues` failing doesn't fail the venue list itself — this distinction is what keeps FR-030 from over-firing on every composite read) |

---

## NormalizedFailure

The envelope every unsuccessful tool call returns, per
`docs/contracts/schemas/tool-error.schema.json`. This feature is the schema's sole producer.

| Field | Type | Source |
|---|---|---|
| `error_class` | `RETRYABLE \| TERMINAL \| REQUIRES_HUMAN \| POLICY_BLOCKED` | FR-019/020/033/034/035 classification logic — `[CRIT]` per `docs/models.md` |
| `domain` | one of the five | Caller's own domain server |
| `detail` | `str`, redacted | Never guest-identifying (FR-022) |
| `vendor_code` / `http_status` | `str \| None` / `int \| None` | Preserved diagnostics (FR-021) |
| `retry_after_seconds` | `int \| None` | Present only when `error_class == RETRYABLE` and the vendor supplied one |
| `idempotency_key` | `str` | Required when `error_class == RETRYABLE` (schema's existing `allOf` rule) |
| `trace_id` | `str \| None` | Correlates to the span this call emitted (FR-027, research.md D7) |
| `completed_sub_calls` | `list[CompletedSubCall] \| None` **(new field, this feature)** | Populated only for a composite tool's partial failure (FR-030) |
| `effect_status` | `Literal["unresolved"] \| None` **(new field, this feature — applied)** | Populated only when FR-032 or FR-035-on-a-mutating-call applies (FR-037) |

### `CompletedSubCall` (new nested shape, part of the `completed_sub_calls` amendment)

| Field | Type | Notes |
|---|---|---|
| `slot_name` | `str` | Matches `SubCallSpec.slot_name` |
| `vendor_resource_id` | `str` | What the vendor assigned (a `hold_id`, a `booking_id`) |
| `idempotency_key` | `str` | This slot's own derived key, for reconciliation |

### Schema amendment this data model required — applied

`tool-error.schema.json` declared `"additionalProperties": false` over a fixed field list
with none of `completed_sub_calls` or `effect_status`. Both were additive, optional, and
backward-compatible — no existing required field or enum value changed — but per `research.md`
D6, editing a Phase 0 frozen file was flagged rather than performed silently. **ADR-015
(Accepted)** carries the exact JSON Schema diff, confirmed by the user and applied to
`docs/contracts/schemas/tool-error.schema.json` within this planning session — this data
model's `completed_sub_calls` and `effect_status` fields now exist in the live schema exactly
as specified above.

---

## UpstreamProtectionState

Per-vendor, in-process, non-persistent (research.md D1/D5). Five instances, one per upstream.

```
CLOSED --[5 consecutive failures]--> OPEN --[30s elapsed]--> HALF_OPEN
HALF_OPEN --[probe succeeds]--> CLOSED
HALF_OPEN --[probe fails]--> OPEN (cooldown resets)
```

| Field | Type | Notes |
|---|---|---|
| `upstream` | one of the five vendor names | Keys the breaker instance |
| `state` | `CLOSED \| OPEN \| HALF_OPEN` | |
| `consecutive_failures` | `int` | Reset to 0 on any success |
| `opened_at` | `datetime \| None` | Set on transition to `OPEN`; drives the 30s cooldown |

**Invariant (FR-031)**: while `state == OPEN`, every call to that upstream is refused
immediately regardless of its `idempotency_key` — no lookup against known keys ever
participates in the block/allow decision.

A schema-invalid or unparseable response body (FR-038) increments `consecutive_failures`
identically to a `5xx` — the breaker's failure counter and the error classifier are two
different consumers of the same underlying "did this call come back healthy" fact, not two
independently-tracked concepts.

---

## Idempotency key derivation (elaborating ADR-006, not redefining it)

```
idempotency_key = f"resortflow:{session_id}:{hold_id}:{attempt}"      # caller-supplied, verbatim
```

The caller (eventually, a Phase 4–5 graph node; today, a test harness) computes this once and
passes it as a single tool argument — the MCP layer never reconstructs it from separate
`session_id`/`hold_id`/`attempt` arguments, and never sees those three values independently.
For a composite tool:

```
sub_call_key(slot_name) = f"{idempotency_key}:{slot_name}"
```

`slot_name` comes from `SubCallSpec`, fixed at tool-definition time — deterministic across
every retry of the whole composite, which is exactly the property the spec's carried-forward
note (Q1) required.

---

## ToolInvocationRecord

One emitted per call (FR-027), as an OpenTelemetry span (research.md D7) — not a stored row.
Attribute names below are the module constants from `observability/attributes.py`, never
string literals.

| Span attribute | Source |
|---|---|
| `resortflow.node` (bounded metric dimension) | The tool's `name` |
| `resortflow.domain` (bounded) | The tool's `domain` |
| `tenant_id` (bounded) | `CallerIdentity.tenant_id` |
| `session_id` (unbounded — span attribute only) | Caller-supplied |
| `idempotency_key` | Present only for mutating tools |
| `outcome` (bounded) | `success \| <error_class>` |
| `trace_id` / `span_id` | OTel-generated; also copied onto `NormalizedFailure.trace_id` on failure |

No guest-identifying field is ever added to this record (FR-029) — the attribute vocabulary
is closed (`observability/attributes.py`'s frozen set), which makes this structurally true
rather than a discipline someone has to remember.

---

## State transitions summary (for tasks.md test design)

| Entity | States | Who moves it |
|---|---|---|
| `UpstreamProtectionState` | `CLOSED → OPEN → HALF_OPEN → {CLOSED, OPEN}` | This feature's breaker, per call outcome |
| `NormalizedFailure.error_class` | One of four, total, immutable once returned | This feature's classifier, at the moment a vendor response is received |
| `NormalizedFailure.effect_status` | absent → `"unresolved"` (never reverses within this feature) | This feature sets it; only a future ledger/reconciler (Phase 4–5) ever resolves it away |

This feature introduces no entity with a multi-step lifecycle of its own — every stateful
lifecycle here (`Hold`, `Reservation`, `Booking`) belongs to Phase 1's simulators, which
already own and test their own state machines.
