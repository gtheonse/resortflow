# Interface Contract: MCP Tool Standard

**Feature**: `001-contracts-foundation` | Permanent home: `docs/contracts/mcp-tool-contract.md`

Governs every tool Track B (`mcp/`) builds in feature 002. This feature freezes the
standard; feature 002 implements against it.

## Naming

`resortflow.<domain>.<verb>_<noun>`, where `<domain>` ∈ `{dining, entertainment, stay, wellness,
gaming}` and the regex is `^resortflow\.[a-z]+\.[a-z]+_[a-z_]+$`.

Examples: `resortflow.dining.create_hold`, `resortflow.dining.confirm_reservation`,
`resortflow.ticketing.hold_seats`, `resortflow.casino.check_rg_status`.

**`gaming` carries read-only verbs only** — `check_*`, `list_*`, `get_*`. No
`create_*`/`confirm_*`/`cancel_*` tool may exist in the `gaming` namespace. This is
principle II expressed structurally: the constraint is the absence of the tool from the
registry, not a prompt instruction an agent could be talked out of.

## Mandatory arguments

Every tool receives `tenant_id` — but never as a caller-supplied argument. It is injected
server-side by the MCP layer from the validated token claim before the tool body executes.
A tool implementation that reads `tenant_id` from its own argument list is a contract
violation.

## Idempotency

Every **mutating** tool (anything that is not a `check_*`/`list_*`/`get_*` read) requires an
`idempotency_key` argument, derived as:

```
idempotency_key = derive(session_id, hold_id, attempt)
```

`hold_id` here is the *client-side* identifier already present before the vendor responds —
in practice, the same value carried as `Hold.idempotency_key` in `ResortFlowState` (see
`data-model.md`). `attempt` increments only on an explicit retry after a terminal failure,
never on a timeout-and-retry of the same logical call — a timeout retry reuses the same key,
because that is what makes the retry safe.

The MCP layer and the ledger both key on this value. Vendors MUST honour it: a duplicate
`Idempotency-Key` header must be treated as a no-op that returns the original result, not a
second mutation. This is a contract obligation of the simulators built in feature 002, not
test scaffolding.

## Error shape

Every tool call normalizes its outcome to the taxonomy defined in this feature
(`ErrorClass`: `RETRYABLE | TERMINAL | REQUIRES_HUMAN | POLICY_BLOCKED`), regardless of
which of the three vendor envelope shapes the underlying call used. See `data-model.md` →
"Error taxonomy mapping" for the full code table and `docs/contracts/schemas/tool-error.schema.json`
for the machine-checkable envelope.

A tool response is never a raw vendor error. The vendor's `vendor_code`, `http_status` and
`detail` are preserved as diagnostic fields on the normalized envelope, but the field an
agent branches on is `error_class`.

## Scopes

Each tool declares a scope string of the form `<domain>:<action>`, e.g. `dining:mutate`,
`gaming:read`. A tool's scope is checked against the caller's entitlements before
invocation, independent of and in addition to the naming convention above — scope
enforcement does not rely on naming discipline alone.

## What this contract does not cover

Transport, retry/backoff policy, circuit-breaker configuration and rate-limit handling are
feature 002 concerns. This document freezes the shape of the interface, not its runtime
behaviour.
