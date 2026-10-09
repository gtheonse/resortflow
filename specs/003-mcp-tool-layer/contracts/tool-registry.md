# Contract: MCP Tool Registry

**Feature**: `003-mcp-tool-layer` | This is the concrete tool roster `/speckit-tasks` builds
against. 32 tools total (F-amendment, `specs/008-phase-7-5-gap-closure` T035: entertainment's
planned composite `get_event_details` was actually built as two separate tools), every domain
at or under the 8-tool cap (FR-004, SC-002). Naming
follows `resortflow.<domain>.<verb>_<noun>` (FR-003); entitlement is exactly one of `read` / `hold`
/ `commit` per tool (FR-012).

Legend: **Ent.** = entitlement action. **Idem.** = requires `idempotency_key`. **Comp.** =
composite (fans out to more than one vendor call).

## `dining` → `sims/dining` (8 tools)

| Tool | Ent. | Idem. | Comp. | Vendor operation(s) |
|---|---|---|---|---|
| `resortflow.dining.list_venues` | read | no | yes | `listVenues` + `listVenueEvents` (enrichment; optional, per-venue) |
| `resortflow.dining.check_availability` | read | no | no | `getAvailability` |
| `resortflow.dining.hold_table` | hold | yes | no | `createHold` |
| `resortflow.dining.release_hold` | hold | yes | no | `releaseHold` |
| `resortflow.dining.confirm_reservation` | commit | yes | no | `confirmReservation` |
| `resortflow.dining.check_dining_status` | read | no | no | `getHold` or `getReservation`, selected by a required `kind: "hold" \| "reservation"` argument — built as a single-call `if`/`elif` branch, not a registered composite (F-amendment T035) |
| `resortflow.dining.cancel_reservation` | commit | yes | no | `cancelReservation` |
| `resortflow.dining.join_waitlist` | hold | yes | no | `joinWaitlist` |

## `entertainment` → `sims/ticketing` (8 tools)

| Tool | Ent. | Idem. | Comp. | Vendor operation(s) |
|---|---|---|---|---|
| `resortflow.entertainment.search_events` | read | no | no | `searchEvents` |
| `resortflow.entertainment.get_event_detail` | read | no | no | `getEvent` (F-amendment T035: built as two separate tools, not one composite — see `check_inventory` below) |
| `resortflow.entertainment.check_inventory` | read | no | no | `getInventory` |
| `resortflow.entertainment.hold_seats` | hold | yes | no | `holdSeats` |
| `resortflow.entertainment.release_seat_hold` | hold | yes | no | `releaseSeatHold` |
| `resortflow.entertainment.purchase_tickets` | commit | yes | no | `purchaseTickets` (async fulfillment; see below) |
| `resortflow.entertainment.get_order_status` | read | no | no | `getSeatHold` or `getOrder`, selected by a required `kind: "hold" \| "order"` argument — built as a single-call `if`/`elif` branch, not a registered composite (F-amendment T035) |
| `resortflow.entertainment.request_refund` | commit | yes | no | `refundOrder` (F-amendment T035: implemented tool name is `request_refund`, not `refund_order`) |

`registerWebhook` is **not** used by this feature at all, and is not an agent-facing tool.
Async fulfillment is handled **poll-only** this phase: `purchase_tickets` returns the vendor's
`202 PENDING` outcome, and `get_order_status` polls `GET /orders/{order_id}` — Phase 1's own
documented sync-poll fallback (the Phase 1 handoff). Consuming the webhook instead would
require the gateway to expose an ingress endpoint, verify inbound HMAC, and hold correlation
state between a received callback and a pending order — durable-state concerns that belong to
the phase that owns durable state, not to a tool layer whose whole design rests on holding
none. Phase 1 already proved the webhook path end-to-end with its own harness, so nothing is
left unverified by deferring it.

## `stay` → `sims/pms` (7 tools)

| Tool | Ent. | Idem. | Comp. | Vendor operation(s) |
|---|---|---|---|---|
| `resortflow.stay.search_rooms` | read | no | no | `searchRooms` |
| `resortflow.stay.hold_room` | hold | yes | no | `holdRoom` |
| `resortflow.stay.release_room_hold` | hold | yes | no | `releaseRoomHold` |
| `resortflow.stay.confirm_room_reservation` | commit | yes | no | `confirmRoomReservation` |
| `resortflow.stay.get_stay_details` | read | no | yes | `getStay` + `getFolio` + `getUpgradeOffers` |
| `resortflow.stay.request_late_checkout` | hold | yes | no | `requestLateCheckout` (reversible amendment to an existing stay — see note) |
| `resortflow.stay.create_service_request` | hold | yes | no | `createServiceRequest` |

**Note on `request_late_checkout`'s entitlement**: `hold` here means "reversible mutation,"
generalizing beyond the `Hold` resource specifically (per `data-model.md`'s Entitlement key
entity) — a late-checkout request can be changed again before it takes effect, unlike
confirming a room reservation.

## `wellness` → `sims/amenities` (6 tools)

| Tool | Ent. | Idem. | Comp. | Vendor operation(s) |
|---|---|---|---|---|
| `resortflow.wellness.list_services` | read | no | no | `listServices` |
| `resortflow.wellness.check_service_availability` | read | no | no | `getServiceAvailability` |
| `resortflow.wellness.hold_amenity_slot` | hold | yes | no | `holdAmenitySlot` |
| `resortflow.wellness.release_amenity_hold` | hold | yes | no | `releaseAmenityHold` |
| `resortflow.wellness.confirm_amenity_booking` | commit | yes | no | `confirmAmenityBooking` |
| `resortflow.wellness.cancel_amenity_booking` | commit | yes | no | `cancelAmenityBooking` |

The frozen `amenities-sim.openapi.yaml` publishes no `GET` for an existing booking — this
roster doesn't invent one.

## `gaming` → `sims/casino` (3 tools — read-only by construction)

| Tool | Ent. | Idem. | Comp. | Vendor operation(s) |
|---|---|---|---|---|
| `resortflow.gaming.check_rg_status` | read | no | no | `getResponsibleGamingStatus` |
| `resortflow.gaming.get_loyalty_status` | read | no | yes | `getLoyaltyProfile` + `getLoyaltyOffers` |
| `resortflow.gaming.list_casino_floor` | read | no | yes | `listFloorGames` + `listTournaments` |

Every tool name uses an allowed gaming verb (`check_`/`get_`/`list_`, per
`mcp-tool-contract.md`'s naming rule for this domain). No tool in this table declares `hold`
or `commit`, and no `create_*`/`confirm_*`/`cancel_*` tool exists in this file at all — the
guarantee FR-026/SC-011 require is this table's absence of such a row, not a check inside a
tool body.

`get_loyalty_status`'s `getLoyaltyOffers` sub-call is the frozen `403 → POLICY_BLOCKED` case
(`specs/001-contracts-foundation/data-model.md`, "Offers suppressed — responsible gaming flag
active") — the one architecturally load-bearing failure mode in this entire domain, and it
falls out of FR-019/FR-033's existing classification logic with no gaming-specific code.

---

## Roster totals

| Domain | Tools | `read` | `hold` | `commit` |
|---|---|---|---|---|
| dining | 8 | 3 | 3 | 2 |
| entertainment | 8 | 4 | 2 | 2 |
| stay | 7 | 2 | 4 | 1 |
| wellness | 6 | 2 | 2 | 2 |
| gaming | 3 | 3 | 0 | 0 |
| **Total** | **32** | 14 | 11 | 7 |

15 `<domain>:<action>` scope strings are constructible (5 domains × 3 actions), of which **13
are the declared entitlement of at least one existing tool**. The two that are not —
`gaming:hold` and `gaming:commit` — are constructible but inert: a token could carry them, and
no tool would ever check for them, because no mutating gaming tool exists to check.

*(These figures are asserted against the live registry by a test, so this table cannot drift
from the code — see `tasks.md` T035.)*
