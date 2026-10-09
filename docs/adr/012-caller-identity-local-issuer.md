# ADR-012 — Caller identity via a local, signed-token issuer

**Status**: Accepted
**Date**: 2026-09-11
**Feature**: 003-mcp-tool-layer

## Context

`docs/contracts/mcp-tool-contract.md` requires `tenant_id` be "injected server-side from the
validated token claim" and scopes "checked against caller entitlements" — but feature 001
never specified what produces or verifies that token, deliberately leaving it to whichever
feature first needed a real caller. This feature is the first: SC-003/004's exit criterion
("tenant-crossing call impossible by construction, proved by test") is only provable if a
caller genuinely cannot assert its own tenancy — which requires *some* verification mechanism
to exist, not just a convention.

## Options Considered

**1. Opaque session token, server-side lookup table.** No cryptography needed — the gateway
holds a map from token to `{tenant_id, scopes}` it populated itself. Simplest to build, but
"verified claim" becomes "trust our own lookup, which we also wrote" — a weaker version of
the same proof, and doesn't exercise the actual claim-verification code path a real IdP
integration would need later.

**2. A signed token (JWT) from a small local issuer, HS256, verified by one shared
middleware.** The caller presents a token whose `tenant_id` and `scopes` are cryptographically
bound to the issuer's signature — a caller cannot construct a valid token naming a tenant it
wasn't issued, which is what makes "impossible by construction" true rather than "impossible
because our code currently checks for it."

**3. RS256 with an asymmetric key pair, closer to a real production IdP (JWKS endpoint, key
rotation).** More representative of what a real deployment would eventually look like, but
this is explicitly a local development fixture (spec Assumptions) with no user-facing login
flow — the extra key-management machinery proves nothing the symmetric case doesn't already
prove for the property actually under test.

## Decision

**Option 2.** A local, dev-only issuer (`mcp/_shared/identity.py`) signs HS256 JWTs carrying
`sub`, `tenant_id`, `scopes`, `iat`, `exp` (contract: `caller-identity-contract.md`). One
shared ASGI middleware, ahead of all five mounted domain servers, verifies signature and
claim shape before any tool body runs.

## Consequences

**Made easier.** Tenant-crossing tests have exactly one thing to attack (mint a token, try
to lie in an argument, try a tampered signature) rather than five domain-specific auth paths
to independently audit. A future real IdP integration only needs to keep producing tokens
with this claim shape — the verification middleware, the entitlement check, and every tool
downstream are unaffected by what mints the token.

**Made harder.** None of the production concerns a real IdP would carry — key rotation,
revocation, multi-issuer trust — exist here. This is deliberate and disclosed, not an
oversight: building them now would be scope creep into infrastructure work no later phase has
asked for yet.

**Foreclosed.** Nothing structural — the claim shape is the only thing downstream code binds
to, and it's stable independent of how the token gets signed or who signs it.
