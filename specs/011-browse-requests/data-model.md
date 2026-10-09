# Data Model: Browse Requests

**Feature**: `011-browse-requests` | See [plan.md](plan.md), [research.md](research.md). Everything here
is additive to feature 010's models; nothing is removed or renamed.

## `RequestKind` (extended, `graph/models.py`)

`BOOK`, `LOOKUP`, `MODIFY`, `FAQ`, `OUT_OF_SCOPE`, `SMALLTALK` **+ `BROWSE = "browse"`**. Already in the
serializer allowlist (the enum type is registered; a new member needs no new registration).

## `BrowseFilters` (new, frozen `ResortFlowModel`, `extra="forbid"`)

| Field | Type | Notes |
|---|---|---|
| `cuisine` | `str \| None` | one of the vocabulary's cuisine keys (dining) |
| `price_band` | `str \| None` | `"$"`…`"$$$$"` (dining) — words like "cheap" map to a band |
| `dietary` | `DietaryRestriction \| None` | the structured enum, never free text (hard rule); browse only *narrows* by it and never asserts safety |
| `category` | `str \| None` | wellness category (`spa`/`fitness`/`cabana`/`golf`) or event classification (`concert`/`theater`/`sports`/`comedy`) |
| `party_size` | `int \| None` | rooms/dining; rooms assume 2 when `None` and say so |

**Invariants**: all fields optional; an all-`None` value is valid ("no filters"). `dietary` accepts only
enum members. Registered in `agents/dining/serde.py::_ALLOWED_MSGPACK_TYPES`.

## `DateWindow` (new, frozen `ResortFlowModel`)

| Field | Type | Notes |
|---|---|---|
| `start` | `date` | inclusive |
| `end` | `date` | inclusive; `end >= start` |
| `source` | `Literal["stated","default","stay_period","needs_dates"]` | how it was resolved, so the reply can say it (FR-018) |

`source="needs_dates"` is the marker for "my stay" with no stay on record: the node asks for dates and uses
no window. Registered in the serializer allowlist.

## `RoutingDecision` (extended)

Adds `browse_filters: BrowseFilters | None = None` and `window: DateWindow | None = None`.

**Invariants (added to the existing validator)**:
- `browse_filters`/`window` are only meaningful when `kind == BROWSE`; other kinds must leave them `None`.
- `BROWSE` may carry any services **except** that `MODIFY`'s gaming ban is unchanged; a browse **may**
  include `gaming` (gaming-only browse routes to the gated standalone graph; a mixed browse omits gaming
  from item listing and only the overview may mention it, behind the gate).
- **`secondary_kind` relaxed**: `BOOK` is allowed as the secondary kind when the primary is `LOOKUP`
  *or* `BROWSE` (a browse+book message: "show me the restaurants and book the steakhouse for 2
  tomorrow"). Nothing else changes.
- The existing tier/confidence/undecided invariants apply unchanged.

## `PreviousTurn` (extended, `graph/routing/kinds.py`, in-process, not persisted)

Adds `filters: BrowseFilters | None` and `window: DateWindow | None`, read from the previous
`RoutingDecision` by `_compute_previous_turn` — never from message text.

## `BookingRecord` (extended, `graph/bookings.py`)

Adds `ends_at: datetime | None = None`, sourced from the matching `Proposal.ends_at`. Never set for a
service that has no end (dining, entertainment, wellness leave it `None`). `stay_period(records)` is a pure
function returning `(arrival, departure)` of the latest stay record with status `confirmed` or `held`.

## `TenantProfile` (new, code constant in `graph/routing/browse.py`, not persisted)

Where the reply's property wording and the other-property check (FR-007) come from. Keyed by the verified
`tenant_id`; never derived from message text.

| `tenant_id` | `property_name` | `region_words` (recognised in a guest message as "this property") |
|---|---|---|
| `MGV` | `Meridian Grand` | `nevada`, `las vegas`, `vegas`, `nv` |
| `MHB` | `Meridian Harbor` | `massachusetts`, `boston`, `ma` |

`detect_other_property(text, tenant_id)` returns the other tenant's `property_name` when the text contains
one of *its* `region_words` and none of the guest's own; otherwise `None`. The words are matched as whole
tokens/phrases (feature 010's tokenizer), so "ma" never matches inside another word. A hit only adds the
"I can show {property_name}'s options only" sentence; it never changes which catalogue is read.

## Catalogue items (not persisted, view types only)

| Service | Source model | Fields shown |
|---|---|---|
| Dining | `Venue` | `name`, `cuisine`, `price_band` |
| Events | `Event` | `name`, `classification`, `start_utc` (as local date+time), `price_range.min` ("from $N") |
| Wellness | `Service` | `name`, `category`, `duration_minutes`, `price` |
| Rooms | `RoomOffer` | `ROOM_DESCRIPTION`, first `NIGHTLY_RATES` amount, `AVAILABLE_UNITS > 0` only |
| Gaming | `GameFloor` (existing gaming graph) | as feature 006 already formats it |

None of these is written to state; the reply is the only output. Sold-out/cancelled events and
`AVAILABLE_UNITS == 0` rooms are excluded.

## `BrowseEvalCase` (extends `RoutingEvalCase`, `eval/data/routing_cases.yaml`)

`expected_kind: browse` plus optional `expected_filters` / `expected_window_source`; near-miss cases keep
their existing kinds (`faq`, `book`, `lookup`). Same `split ∈ {dev, holdout}` rule and per-category
per-tenant minimum as feature 010.

## `ResortFlowState`

**No key added or removed** (still 25). `routing` and `turn_baseline` are unchanged in shape apart from the
additive `RoutingDecision` fields. `GRAPH_VERSION` becomes `"5.2"`. See
[contracts/state-schema-amendment-011.md](contracts/state-schema-amendment-011.md).
