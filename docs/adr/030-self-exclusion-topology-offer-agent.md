# ADR-030 — Self-exclusion enforcement topology and `OFFER_AGENT` mounting

**Status**: Accepted
**Date**: 2026-09-12
**Feature**: 006-policy-guardrails-hitl

## Context

ADR-020 (feature 005) left `OFFER_AGENT` reserved but uncompiled, explicitly because its
self-exclusion precondition depends on a Policy Engine that feature deferred to this one,
and explicitly named this feature as the expected owner of both the precondition and
`OFFER_AGENT`'s mounting: "No future feature may wire `OFFER_AGENT` live without also
having a real Policy Engine self-exclusion gate in front of it." Separately,
`agents/gaming/nodes/lookup.py` today calls `check_rg_status` only when the guest's
intent is classified `rg_status` — the other two intents (`floor_games`,
`loyalty_status`) call their vendor tool directly, with no self-exclusion check at all.
That is a live compliance gap: the constitution requires the check "before *any*
gaming-domain tool executes... on every turn, regardless of topic," and today two of
three gaming tools skip it entirely.

`OFFER_AGENT`'s originally-imagined substance (per ADR-020's quote) is "contextual
upgrade/package suggestions from Snowflake feature store" — a real recommendation-engine
integration that does not exist anywhere in this project (`mcp/gateway/` has no `offers`
domain module) and is not named as an in-scope deliverable by this phase's own roster
bullet, which reads "offer-registry restriction," not "build an offer engine."

## Options Considered

**For enforcement placement:**

**1. Enforce self-exclusion in MCP gateway middleware, globally per domain.** Attractive
for uniformity — `mcp/gateway/_shared/middleware.py` already runs on every gaming-domain
call. Rejected as the sole mechanism: `OFFER_AGENT`'s tool call
(`get_loyalty_status`) lives in the `gaming` domain today, but the requirement is
conceptual ("before the Offer Agent runs at all"), not "before any gaming-domain tool" —
a future, genuinely separate `offers` domain would fall outside gaming-scoped middleware
entirely, silently losing the guarantee. The check belongs to the Policy Engine, called
explicitly by name at each site that needs it, not implicitly inherited from domain
routing.

**2. A single `require_gaming_clear` function (`policy/gaming.py`), called explicitly
from both the gaming subgraph's `lookup` node (all three intents) and `OFFER_AGENT`'s
node body, first line.** One function, two explicit call sites, no implicit inheritance.

**For `OFFER_AGENT`'s scope:**

**1. Build the full Snowflake-feature-store-backed recommendation substance now.**
Rejected: unbounded scope increase (a new vendor contract, a new sim, a new MCP domain,
a new retrieval/ranking design) with no mandate in this phase's roster bullets or exit
criteria, which are about restriction, not recommendation quality.

**2. Mount `OFFER_AGENT` narrowly, reusing the already-real `get_loyalty_status`
composite call as its sole data source, and defer a genuine offer-catalog integration.**
Satisfies ADR-020's actual requirement (the node exists, is gated, is structurally
incapable of a gaming-linked offer) without inventing scope this phase was never asked
to carry.

## Decision

**Enforcement, Option 2.** `require_gaming_clear(tools, guest_id=guest_id)` is the one
function both call sites use. `agents/gaming/nodes/lookup.py`'s dispatch calls it before
branching on `intent_kind` (closing the two-of-three-intents gap). The new `OFFER_AGENT`
node calls it as its first action, every invocation — no caching across turns (FR-016).

**`OFFER_AGENT` scope, Option 2.** `OFFER_AGENT` is compiled this feature with exactly
one tool call in its body, `GamingTools.get_loyalty_status` — already real, already a
composite of profile + optional non-gaming-comp offers, already gateway-enforced to
respect 403 → `POLICY_BLOCKED`. Structural exclusion of any gaming-linked offer tool is
documented and tested per `contracts/offer-agent-tool-registry-contract.md`. A genuine
offer-recommendation/feature-store integration is recorded as an explicit open item for a
future feature, not attempted here.

## Consequences

**Made easier.** Both gaming-domain call sites for self-exclusion share one
implementation, so a future change to the check (e.g., additional vendor fields) updates
both automatically. `OFFER_AGENT` ships this feature at all, resolving a two-feature-old
reservation, without waiting on an unrelated vendor-integration project.

**Made harder.** `OFFER_AGENT`'s guest-facing value is limited this phase (loyalty/comp
offers only) — a deliberate, documented limitation, not a hidden one.

**Foreclosed.** No future feature may add a gaming-linked-offer tool to `OFFER_AGENT`'s
registry without also revisiting this ADR — the structural exclusion is the guarantee,
and extending the registry is exactly the change that would need to re-justify it. No
future feature may call a gaming-domain vendor tool without going through
`require_gaming_clear` first without a new ADR explicitly re-opening this enforcement
topology.
