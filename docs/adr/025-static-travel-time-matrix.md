# ADR-025 — A static per-tenant travel-time matrix, not a live distance service

**Status**: Accepted
**Date**: 2026-09-11
**Feature**: 005-multi-agent-orchestration

## Context

The deterministic conflict checker (`CONFLICT_CHECK`, no LLM — Constitution: DETERMINISM
FIRST) needs to know how long it takes a guest to travel between two venues in the same
proposed itinerary, to decide whether a 6:30 dinner really leaves enough time to reach an
8pm show. `graph.models.Proposal` already carries a `location_zone` field for exactly
this purpose ("Travel-time input," feature 001), but nothing yet resolves a pair of zones
into a travel-time value. Some source of truth for that value has to exist before the
conflict checker can run at all, and it is genuinely a choice with real alternatives —
not dictated by any existing frozen contract.

## Options Considered

**1. A live distance/mapping API** (e.g. a routing or distance-matrix service). Accurate
for arbitrary points on a map. Both tenants — Meridian Grand and Meridian Harbor — are
single, fixed physical properties with a small, enumerable set of internal venues
(restaurants, showrooms, spas, the casino floor). Routing every conflict check through an
external network call for a value that never changes for a given venue pair is
unjustified latency and cost, and introduces a sixth external vendor dependency into a
system already built around five deliberately simulated ones.

**2. Geo-coordinate haversine calculation** from venue latitude/longitude. More general
than needed, since venues are static and already known — and straight-line distance is a
poor proxy for actual walking time inside a building (elevators, casino-floor layout,
which floor a showroom is on), which would require a padding/fudge-factor multiplier
anyway. That undermines the constitution's own reasoning for keeping this node
deterministic in the first place ("a function gets it right 100% of the time" only holds
if the underlying distance model is actually accurate, not systematically approximate).

**3. A small static per-tenant zone-to-zone minutes matrix**, maintained as versioned
config in the repository, populated once from each property's actual known layout.

## Decision

**Option 3.** A static matrix (e.g. `graph/travel_time.py`, one table per tenant, keyed
by `(zone_a, zone_b)`), checked into version control like any other configuration. Both
properties are closed, known worlds — this is exactly the kind of arithmetic the
constitution already prefers a function over a live call for, and a live mapping service
would be solving a problem (routing between arbitrary, unknown locations) that ResortFlow does
not actually have.

## Consequences

**Made easier.** The conflict checker has zero external dependencies for this input, is
fully unit-testable with known fixed inputs and outputs, and costs effectively 0ms.
Adding a new venue to a property is a one-line config addition, not an integration.

**Made harder.** The matrix must be manually maintained whenever a property's physical
layout actually changes — a new venue opens, a walkway closes for renovation. This is a
real, ongoing data-maintenance obligation, not a one-time cost, even though it is an
operational burden rather than a code burden.

**Foreclosed.** No per-request precision beyond zone-level granularity — two venues
mapped to the same zone are always treated as zero travel time between them. This is an
accepted approximation, not a gap to eventually close with more precision, unless a
future property's layout makes zone-level granularity genuinely too coarse.
