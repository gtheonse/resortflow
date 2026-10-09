# ADR-009 — Phase 1 ships its own webhook test harness rather than deferring async testing to feature 002

**Status**: Accepted
**Date**: 2026-09-10
**Feature**: 002-simulation-ecosystem

## Context

`ticketing-sim`'s purchase flow is asynchronous by frozen contract: `POST /orders` returns
`202 Accepted` with a `PENDING` order, and the terminal state arrives later via a webhook to
a caller-registered `callback_url` (`docs/contracts/openapi/ticketing-sim.openapi.yaml`).
spec.md FR-006 requires this feature to prove that fulfillment happens (SC-006: "delivered
for 100% of confirmed ticketing holds"), and the `/speckit-clarify` session added a bounded
retry-with-backoff requirement for failed deliveries. The natural production consumer of
that webhook is feature 002's MCP layer — which does not exist yet. This phase has to decide
whether to build something to receive and verify webhook deliveries now, or treat webhook
delivery as untestable until feature 002 lands.

## Options Considered

**1. Defer all webhook-delivery verification to feature 002.** No extra code in this phase.
But FR-006 and SC-006 would then be unverifiable within this feature's own exit criteria —
directly contradicting the working agreement's "tests before the next phase starts," and
leaving a two-phase gap where "async fulfillment works" is simply asserted, not proven, until
whichever later feature happens to build a webhook consumer.

**2. Rely solely on the contract's polling fallback (`GET /orders/{order_id}`) and never
actually exercise webhook delivery in tests.** The contract explicitly offers this as a
fallback path ("poll fallback if webhook is missed"), so it's tempting to just always poll.
But that only proves the *order eventually reaches a terminal state*, not that the *webhook
delivery mechanism itself* works — FR-006 is specifically about the notification being
delivered via callback, not about eventual consistency reachable by any means.

**3. Ship a minimal in-process webhook receiver as part of this feature's own test harness**
(`sims/_shared/webhook_harness.py`), used only by this feature's tests, with a separate
configurable real endpoint left available for feature 002 to point at later.

## Decision

**Build the lightweight harness now, per option 3.** It's the only option under which this
phase's own exit criteria (a scripted, unattended proof that every documented behavior works)
actually cover the async path it specifies.

## Consequences

**Made easier.** FR-006/SC-006 are provable inside this feature, with no dependency on
feature 002's timeline or design. The harness also becomes a reusable tool for exercising the
`webhook_delay` fault mode and the retry-with-backoff behavior deterministically, which
option 1 or 2 would have made much harder to test at all.

**Made harder.** A small amount of code (`sims/_shared/webhook_harness.py` plus two
test-only routes on ticketing-sim) exists purely to serve this feature's own tests and has no
production equivalent — it must be clearly marked as such (ADR-008's pattern: outside the
frozen contract surface) so nobody mistakes it for the real integration.

**Foreclosed.** Nothing — feature 002 remains free to build its own real webhook consumer
however it wants; this harness does not constrain that design, since it is explicitly a
Phase 1 test double for Phase 1's own verification, not a piece of production
infrastructure that feature 002 inherits or must be compatible with.
