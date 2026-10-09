# ADR-008 — Fault-injection and reset kept outside the frozen OpenAPI contract surface

**Status**: Accepted
**Date**: 2026-09-10
**Feature**: 002-simulation-ecosystem

## Context

This feature must give every sim a deterministic, on-demand fault-injection mechanism
(spec.md FR-005/FR-013) and a way to reset state to the seeded baseline (FR-014, added via
`/speckit-clarify`). Neither capability exists in any real vendor's API — they are test/ops
concerns invented for this project. The five OpenAPI files that define each sim's actual
vendor-facing surface are frozen (feature 001's working agreement: "Phase 0 contracts are
frozen... a track needing a contract change raises it as a blocker, does not edit
unilaterally"). Where these two capabilities live relative to that frozen surface is a real
choice with consequences for the contract test suite (FR-011), which is supposed to prove
each sim matches its frozen spec — a test/ops endpoint appearing inside that surface would
either need to be part of the frozen contract (which it was never meant to be) or would need
the test suite to specially exclude it anyway.

## Options Considered

**1. Formally add `X-Sim-Fault`/`X-Sim-Latency` as declared parameters and a `/reset` path to
each OpenAPI file.** Makes the contracts fully self-documenting — anyone reading the YAML
sees the complete real surface, test controls included. But it means editing five frozen
Phase 0 artifacts in a Phase 1 feature, which the working agreement explicitly forbids doing
unilaterally; it would also make every future contract-conformance run implicitly validate
test/ops behavior as if it were vendor behavior, which is a category error — a real vendor's
contract would never include a chaos-injection header.

**2. A separate control-plane microservice sitting in front of all five sims,
proxying/injecting faults and handling resets centrally.** Cleanly separates test/ops
concerns from vendor simulation in a different service boundary, and could be a single place
to document all fault-injection behavior. But it adds a sixth service, a network hop on every
request, and a routing/coordination problem (which downstream sim does a given fault apply
to, and how does the proxy stay synchronized with five independently-changing services) that
a per-request header avoids by construction.

**3. Headers and an out-of-band path, documented separately, outside the frozen contracts.**
`X-Sim-Fault`/`X-Sim-Latency` are read as ordinary HTTP headers by shared middleware; every
frozen spec already mentions them only in free-text `description` prose, never as formal
parameters, so this follows a convention feature 001 already set rather than establishing a
new one. Reset lives at a path prefix (`/__sim__/...`) the contract-conformance suite is
configured to ignore.

## Decision

**Headers plus an out-of-band path, per option 3**, documented in
`specs/002-simulation-ecosystem/contracts/sim-control-plane.md` as its own interface
contract — real, versioned as far as downstream tracks are concerned, just not part of the
vendor-facing OpenAPI surface.

## Consequences

**Made easier.** The frozen contracts stay exactly what they were designed to be: a
faithful, editable-only-by-blocker record of what a real vendor's API looks like. The
contract-conformance suite (FR-011) tests exactly that surface, with no special-casing needed
inside the schemathesis run itself. Adding a new fault mode or extending the reset behavior
later never requires touching a frozen file or raising a contract-change blocker.

**Made harder.** Two places to look instead of one — a reader auditing "everything this sim
does" has to check both the OpenAPI file and `sim-control-plane.md`. Mitigated by making the
latter a first-class, permanent contracts document rather than an implementation detail
buried in code.

**Foreclosed.** No future feature can casually blur the line between "what the vendor does"
and "what our test harness does" by adding a test-only header or path directly into a frozen
spec — that would need its own ADR explicitly reversing this one.
