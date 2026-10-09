# Interface Contract: Seed Data Specification

**Feature**: `002-simulation-ecosystem` | Permanent home: this file (Track A, `sims/`)

Consumed by `make seed` (via each sim's `seed.py`) and by anyone writing a test or demo
scenario against a known-populated environment. Exact counts, matching spec.md FR-008/FR-009
and `research.md` item 5.

## Per-tenant totals

| Entity | Sim | Total | MGV | MHB |
|---|---|---|---|---|
| Venues | dining | 8 | 5 | 3 |
| Events | ticketing | 40 | 24 | 16 |
| Rooms (as room offers) | pms | 200 | 120 | 80 |
| Spa/amenity slots | amenities | 60 | 36 | 24 |
| Guest profiles | shared (referenced by all sims) | 50 | 30 | 20 |

Casino has no seeded inventory of its own — no holds, no bookings — only loyalty and
responsible-gaming status records keyed to the 50 shared guest profiles (see below).

## Guest profiles

50 total (30 MGV, 20 MHB), each with:
- `guest_id`, `tenant_id`, `display_name`, `phone`, `email` (synthetic, no real PII)
- A casino loyalty record: `tier` (distributed across `MEMBER`–`CHAIRMAN`, weighted toward
  lower tiers), `points_balance`, `comp_dollars`
- A responsible-gaming status record: `self_excluded` (a small minority, e.g. 2–3 of 50, to
  exercise the `offers_permitted: false` / suppressed-offers path), `exclusion_scope`,
  `limit_flags`, `offers_permitted`, `gaming_content_permitted`, `mandated_message` matching
  the guest's tenant jurisdiction (`NV` for MGV, `MA` for MHB, per `casino-sim.openapi.yaml`)

At least one seeded guest per tenant MUST have `self_excluded: true` — otherwise the
suppressed-offers path (`403` on `/loyalty/{guest_id}/offers`, `offers_permitted: false` on
the status check) has no seeded data to exercise in the contract test suite or a future
policy-feature demo.

## Venues (dining)

8 total (5 MGV, 3 MHB), spanning a range of `cuisine`, `price_band` (`$`–`$$$$`), and
`supported_dietary` values wide enough that every `DietaryRestriction` enum value
(`shellfish` through `low_sodium`) is supported by at least one venue per tenant, so a
dietary-filtering test scenario always has a matching venue.

## Events (ticketing)

40 total (24 MGV, 16 MHB), mixing `on_property: true/false` (a minority city-wide, to
exercise the `scope` query parameter) and covering every `classification` enum value at
least once per tenant. At least one event per tenant is seeded with `status: sold_out` and
one with `status: cancelled`, so those paths are testable without needing fault injection.

## Rooms (pms, as room offers)

200 total (120 MGV, 80 MHB) across all four `room_class` values (`STANDARD`, `DELUXE`,
`SUITE`, `VILLA`), weighted toward `STANDARD`/`DELUXE` (realistic inventory shape — a resort
has far more standard rooms than villas). `AVAILABLE_UNITS` per offer is set so total
capacity matches the 200/tenant-split figure, not 200 independently-available units per
offer row.

## Spa/amenity slots (amenities)

60 total (36 MGV, 24 MHB) distributed across `category` (`spa`, `fitness`, `cabana`, `golf`)
and multiple `practitioner_id`s per service, since availability is practitioner-constrained
per the frozen contract — a service with only one practitioner cannot demonstrate the
"practitioner taken" `409` path realistically.

## Regeneration

Reset (`contracts/sim-control-plane.md`) restores exactly this baseline — same counts, same
deterministic seed (a fixed random seed per `seed.py`, not wall-clock-dependent), so a reset
sim is byte-for-byte comparable across runs for test assertions that check exact counts
(spec.md SC-007/SC-008).
