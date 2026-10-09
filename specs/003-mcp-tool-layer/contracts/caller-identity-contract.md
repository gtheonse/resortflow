# Contract: Caller Identity Token

**Feature**: `003-mcp-tool-layer` | New contract this feature owns and freezes for itself —
does not amend any Phase 0 file. Governs `mcp/gateway/_shared/identity.py`.

## Shape

A JWT (HS256), verified by one shared middleware ahead of all five mounted domain servers.

```json
{
  "sub": "test-harness-1",
  "tenant_id": "MGV",
  "scopes": ["dining:read", "dining:hold", "stay:read", "stay:hold"],
  "iat": 1799600000,
  "exp": 1799603600
}
```

| Claim | Required | Rule |
|---|---|---|
| `sub` | yes | Non-empty string. Identifies the caller (a domain subgraph, a test harness) — **never** a guest identifier; guest identity lives in `ResortFlowState`, not here. |
| `tenant_id` | yes | Exactly `"MGV"` or `"MHB"`. Any other value fails verification — there is no "unknown tenant" outcome (spec Edge Cases, resolved). |
| `scopes` | yes | List of strings matching `^(dining|entertainment|stay|wellness|gaming):(read|hold|commit)$`. May be empty (a caller with zero entitlements verifies fine and is then refused by every tool it invokes — FR-013 applies uniformly). |
| `iat` / `exp` | yes | Standard JWT claims. `exp - iat` MUST NOT exceed 1 hour for tokens minted by this feature's issuer. |

## Verification rules (FR-007)

A request is refused, before any tool body executes, if:
1. No `Authorization: Bearer <token>` header is present.
2. The signature does not verify against the issuer's key.
3. `exp` has passed.
4. Any required claim (`sub`, `tenant_id`, `scopes`) is missing or malformed.
5. `tenant_id` is present but not `"MGV"` or `"MHB"`.

All five conditions produce the same caller-visible outcome: refused before dispatch. This
feature does not distinguish "bad signature" from "expired" from "unknown tenant" in what the
caller sees, deliberately — five different messages would be five things a probing caller
could use to fingerprint the verification logic.

## Issuer (`mcp/gateway/_shared/identity.py`, dev-only)

A function, not a network service: `issue_token(sub, tenant_id, scopes, ttl_seconds=3600) ->
str`. Signs with a key read from an environment variable (`MCP_DEV_SIGNING_KEY`), generated
once at `make seed` time if absent. **Explicitly not a production identity provider** (spec
Assumptions) — there is no user-facing login flow, no key rotation, no JWKS endpoint. A real
IdP integration is out of this feature's scope entirely; whatever replaces this issuer later
only needs to keep producing tokens with this same claim shape.

## What this contract does not cover

Scope *grant* policy — which caller gets which scopes — is out of scope here. This contract
only defines the shape a token carries and how it's checked; deciding what a "dining domain
subgraph" caller should actually be granted is Phase 4's concern (Track C), when callers other
than a test harness first exist.
