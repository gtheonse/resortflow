# ADR-049 — No standalone jwt-issuer/edge-shim for local dev

**Status**: Accepted
**Date**: 2026-09-15
**Feature**: 008-phase-7-5-gap-closure

**Note on numbering**: this decision is not in the ADR register at §15 of
`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md`, which planned it as its own dedicated
entry alongside "Container Apps over AKS" and "Langfuse over Application Insights" —
neither of which was ever actually written up as a numbered ADR either (the real
register jumps 016 straight to 019 in that area). This is added here, at the next
available real number after `048-guest-profile-sourced-dietary-data.md`, continuing
the register's already-non-sequential real numbering — see
`019-write-ahead-hold-ledger.md`'s own "Note on numbering" for the precedent.

## Context

`docker-compose.yml`'s default (always-up, not `sims`/`app` gated) profile has
declared a `jwt-issuer` service (build context `./infra/jwt-issuer`, "local Entra
stand-in") and an `edge-shim` service (build context `./infra/edge-shim`, "APIM
policy stand-in") since Phase 0. Neither directory has ever existed. `make up` has
therefore never actually succeeded end to end — it fails immediately trying to build
two services with no source.

A read-only audit (triggered by testing the local UI console) traced why these were
never built and found the answer isn't "nobody got to it" — it's that nothing in the
codebase ever needed them:

- `mcp/gateway/_shared/identity.py` (ADR-012, `specs/003-mcp-tool-layer/contracts/
  caller-identity-contract.md`) already does real HS256 JWT mint/verify in-process,
  keyed on `MCP_DEV_SIGNING_KEY`. Its own docstring is explicit: *"Dev-only issuer
  (research.md D2). Not a network service — a function... Explicitly not a production
  identity provider."*
- `graph/mcp_client.py` calls `identity.issue_token()` directly — a plain Python call,
  not an HTTP request to any issuer. Its own docstring states there is "no separate
  identity provider in this project" and that reusing the gateway's own dev issuer
  from the agent side is "the only self-consistent choice available."
- No code anywhere calls out to an `edge-shim`-shaped service either. There is no
  networked ingress in front of the orchestrator today for a shim to guard (Phase 7.5
  adds one — `graph/api.py` — but it, too, has no edge auth in front of it yet; see
  ADR-050's own disclosed gap).

In short: two compose services were declared for an architecture (a real networked
issuer and a real edge gateway sitting in front of a real orchestrator service) that
was never built, because every consumer that would have called them was instead
wired to mint/verify tokens in-process against a shared dev secret.

## Options Considered

**1. Build real `jwt-issuer`/`edge-shim` containers locally**, matching the compose
file's original intent exactly. Rejected: nothing consumes them, so this would be
pure architecture theater — two more services to keep running, with no code path
that calls either one, purely to match a diagram. It would also invite a second,
parallel identity mechanism to drift against `mcp/gateway/_shared/identity.py`'s
already-working one.

**2. Remove the compose entries, leave the gap silent.** Rejected: silently deleting
a documented architectural intent without a decision record is exactly the kind of
undisclosed scope-skip `AGENTS.md`'s working agreement warns against.

**3. (Chosen) Formalize the in-process issuer as the permanent local-dev answer, and
remove the two unbuildable compose entries with a pointer to this ADR.** This
matches what was actually built, closes the `make up` failure, and keeps the real
production replacements (Microsoft Entra ID for the issuer, Azure APIM for the edge
shim — both explicitly named in Phase 8's Terraform scope in
`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §13) as Phase 8's job, unaffected.

## Decision

`mcp/gateway/_shared/identity.py`'s `issue_token()`/`verify_token()` — in-process
HS256 mint/verify against `MCP_DEV_SIGNING_KEY` — are the permanent local-dev
identity answer. No standalone `jwt-issuer` or `edge-shim` service is built for local
development. `docker-compose.yml`'s `jwt-issuer` and `edge-shim` entries are removed
from the default profile, with a comment pointing here.

## Consequences

- `make up` no longer fails on two unbuildable services.
- The dev-console (`ui/app.py`) and the new production orchestrator entrypoint
  (`graph/api.py`, ADR-050) both continue to accept `tenant_id` directly in the
  request body with no edge authentication in front of them locally — an existing,
  now-doubly-disclosed gap, not newly introduced by this decision. It remains
  explicitly Phase 8 scope (Entra ID + Azure APIM), not something this ADR
  half-solves with throwaway local auth.
- If a future local feature genuinely needs a networked identity provider (for
  example, to test a real OAuth2 authorization-code flow rather than a bearer token
  a trusted caller already holds), that is a new decision superseding this one, not
  an extension of it.
