# Data Model: Tiered Intent Routing

**Feature**: `010-tiered-intent-routing` | **Date**: 2026-09-28 | **Research**: [research.md](research.md)

New types live in `graph/models.py` (state-carried) or `graph/routing/` (not state-carried).
Every state-carried type is added to `agents/dining/serde.py::_ALLOWED_MSGPACK_TYPES`
(research R-02).

---

## Enumerations

### `RequestKind` (`graph/models.py`, `StrEnum`) — state-carried

| Value | Meaning | Routes to |
|---|---|---|
| `book` | new booking | `DETERMINISTIC` (1 service) / `SUPERVISOR` (≥ 2) |
| `lookup` | question about this conversation's bookings | `BOOKING_LOOKUP` |
| `modify` | change or cancel an existing booking | `MODIFY_REQUEST` → `HUMAN_HANDOFF` |
| `faq` | general property question | `KNOWLEDGE_QA` |
| `out_of_scope` | not a resort service | `DECLINE` |
| `smalltalk` | greeting / acknowledgement | `SMALLTALK` |

"Undecided" is **not** a kind. It is `RoutingDecision.kind is None`, which routes to `CLARIFY`.

**Gaming rule** (FR-028): a decision whose services are exactly `[gaming]` and whose kind is
`book`, `lookup` or `faq` routes to `DETERMINISTIC`, which runs the read-only gaming standalone
graph as it does today. No kind reaches a gaming mutation, because none exists.

### `RoutingTier` (`IntEnum`): `0` (tolerant vocabulary), `1` (kind rules + carry-over), `2` (model).

### `Tier2Outcome` (`StrEnum`): `not_called`, `decided`, `invalid`, `timeout`, `unavailable`.

---

## `RoutingDecision` (`graph/models.py`, `ResortFlowModel`) — state-carried

| Field | Type | Rule |
|---|---|---|
| `kind` | `RequestKind \| None` | `None` = undecided → `CLARIFY` |
| `services` | `list[Domain]` | sorted and unique; empty for `out_of_scope`, `smalltalk`, and `faq` without a service |
| `secondary_kind` | `RequestKind \| None` | only `book` when `kind == lookup` (FR-020); otherwise `None` |
| `secondary_services` | `list[Domain]` | services of the secondary clause |
| `tier` | `RoutingTier` | the tier that decided (or last tried) |
| `confidence` | `float` | `[0.0, 1.0]`; high = `≥ 0.7` (`HIGH_CONFIDENCE`) |
| `ambiguous` | `bool` | a typo equidistant from ≥ 2 services (FR-005) |
| `carried_over` | `bool` | decided by the follow-up rule (FR-008a) |
| `focus_keys` | `list[str]` | booking identities (`idempotency_key`) this turn refers to: records matched by a lookup, or the booking a carry-over / modify points at. Empty otherwise |
| `tier2_outcome` | `Tier2Outcome` | `not_called` unless tier 2 was reached |
| `decided_at` | `datetime` | UTC; informational only, never used for turn scoping (R-12) |

**Invariants** (validated on construction):

- `kind is None` ⇒ `confidence < HIGH_CONFIDENCE` and `services == []`.
- `kind in {out_of_scope, smalltalk}` ⇒ `services == []`.
- `secondary_kind is not None` ⇒ `kind == lookup` and `secondary_kind == book`.
- `tier == 2` ⇒ `tier2_outcome != not_called`.
- `Domain.GAMING in services` ⇒ `kind != modify`, or else the decision routes to the hand-off,
  never to a gaming tool.
- No field holds tenant, guest or message text (FR-012, FR-029).

## `TurnBaseline` (`graph/models.py`, `ResortFlowModel`) — state-carried

| Field | Type | Meaning |
|---|---|---|
| `confirmation_keys` | `list[str]` | sorted unique `idempotency_key`s of `confirmations` at turn start |
| `hold_statuses` | `dict[str, HoldStatus]` | `idempotency_key` → status at turn start |
| `turn_index` | `int` | 1 + the previous baseline's `turn_index` (1 on the first routed turn). Not used for reporting in 010; it is the turn identity the follow-up turn-stamped event log (ADR-058, option b) will stamp on `SagaEvent.detail["turn"]` |

Written once per router pass (a `CLARIFY` loop back to the router rewrites it, which is harmless
because nothing is booked between clarify and router). It is **not** rewritten on an approval
resume, so the commit that follows an approval counts as the same turn (R-12).

**This turn's outcomes** (a pure function `turn_outcomes(state) -> list[BookingRecord]`): records
whose confirmation key is not in `confirmation_keys`, plus records whose hold key is absent from
`hold_statuses` or whose status changed.

---

## `ResortFlowState` amendment 010 (additive, ADR-056)

| Key | Type | Default | Writer | Readers |
|---|---|---|---|---|
| `routing` | `RoutingDecision \| None` | `None` | `INTENT_ROUTER` only | `_route_from_intent_router`, `DETERMINISTIC`, `PLANNER`, `BOOKING_LOOKUP`, `MODIFY_REQUEST`, next turn's `INTENT_ROUTER` (carry-over) |
| `turn_baseline` | `TurnBaseline \| None` | `None` | `INTENT_ROUTER` only | reply composer (`RESPOND` / `DETERMINISTIC`), next turn's carry-over eligibility |

No reducer (last-write-wins; one writer per superstep). A pre-010 checkpoint lacks both keys and
reads as `None`: no carry-over. With no baseline, the reply composer cannot compute "this turn's
outcome" at all — "every record is new" would report an old confirmation as confirmed by the
current turn, which FR-031 forbids. Instead, on the **first** post-upgrade turn of an old
conversation, the composer falls back to the lookup-style presentation (`contracts/turn-replies.md`):
it lists every deduped record with its current status ("here's where things stand: …") and never
uses "confirmed this turn" / "held this turn" wording for any of them. It still dedupes by key and
still never calls a held item confirmed (FR-032). From the following turn on, `turn_baseline` is
present and normal per-turn reporting resumes.
`escalate_reason` vocabulary gains `"modify_request"` (appended). `GRAPH_VERSION` becomes `"5.1"`.

---

## `BookingRecord` (`graph/bookings.py`, not state-carried — a read-model)

Built by `booking_view(state) -> list[BookingRecord]`, joining on `idempotency_key` and
**deduplicated by it** (FR-033):

| Field | Source |
|---|---|
| `key` | `Hold.idempotency_key` (= `Proposal.hold_idempotency_key` = `Confirmation.idempotency_key`) |
| `service` | `Proposal.domain` |
| `venue` | `Proposal.title` |
| `starts_at` | `Proposal.starts_at` |
| `party_size` | `Proposal.party_size` |
| `status` | `confirmed` if any `Confirmation` has this key; else `Hold.status` |
| `hold_id` | `Hold.hold_id` (for the dining re-check) |
| `expires_at` | `Hold.expires_at`; **ignored when status is terminal** (never shown as a countdown) |
| `confirmation_code` | first `Confirmation.confirmation_id` with this key |

Records whose status is `released`, `expired` or `orphan_released` are kept in the view but
reported as "no longer held". A lookup never presents them as bookings.

## `LookupAnswer` (`graph/bookings.py`, not state-carried)

| Field | Type |
|---|---|
| `matched` | `list[BookingRecord]` |
| `all_records` | `list[BookingRecord]` (active ones, for the FR-016 "here's what is booked" list) |
| `recheck` | `dict[key, Literal["active", "not_active", "unverifiable", "error"]]` (FR-015) |

Status wording (deterministic templates in `graph/replies.py`):
confirmed → "confirmed"; held + `active` → "held, not yet confirmed — hold expires at HH:MM";
held + `not_active` → "the hold has lapsed"; held + `unverifiable`/`error` → "held — status could
not be confirmed right now".

---

## Vocabulary data (`graph/routing/vocabulary.yaml`, loaded into `graph/routing/vocabulary.py`)

```yaml
version: 1
tolerance:            # research R-03; calibrated values written back in R-15
  exact_max_len: 3
  swap_only_len: 4
  one_edit_min_len: 5
  two_edit_min_len: 8
services:
  dining:
    words:
      - {word: restaurant, variants: [restaurants]}
      - {word: dine, variants: [dined, dining, dinner, dinners]}
      - {word: table, variants: [tables]}
    phrases: ["table for", "a bite"]
    misspellings: [resturant, restarant, dinnr]
    exclude_phrases: []
  entertainment: {...}
  wellness: {...}
  stay:
    words: [room, suite, night, checkout, housekeeping]   # `stay` alone deliberately absent (R-05)
    phrases: ["late checkout", "my stay", "extend my stay"]
    exclude_phrases: ["stay safe", "stay tuned"]
  gaming: {...}
kinds:
  modify: {words: [cancel, change, move, reschedule], phrases: ["make it", "push back", "instead of"]}
  lookup: {phrases: ["did i book", "i booked", "i reserved", "i got reserved", "my reservation", "my booking", "my itinerary", "am i booked", "have i booked"]}
  book:   {words: [book, reserve], phrases: ["get me", "i'd like", "can i get"]}
  smalltalk: {words: [hi, hello, hey, thanks, thank, ok, okay, yes, no, bye], max_tokens: 3}
  faq:    {words: [what, when, where, how, does], phrases: ["is there", "do you", "can i"]}
  followup:
    reference: {words: [it, that, this], phrases: ["is it confirmed", "what time", "which restaurant", "how many", "when is"]}
    change:    {phrases: ["make it", "change to", "move it", "instead"]}
    repeat:    {phrases: ["same again", "same for", "also for", "another one"]}
out_of_scope:
  words: [flight, flights, airline, airport, train, visa, passport, weather, uber, taxi]
  phrases: ["car rental", "rent a car", "airport transfer"]
```

**Load-time validation**: the schema (`extra="forbid"`); every word is lowercase and a single
token; no phrase is duplicated across services; and the **collision check**: no word of service A
is within the tolerance band of a word of service B (R-05). A failure raises at import, so the
process does not start (same fail-fast posture as `prompts.registry.validate_all`).

## `RoutingEvalCase` (`eval/data/routing_cases.yaml`, loaded by `eval/routing_scorer.py`)

| Field | Type |
|---|---|
| `id` | `str` (unique) |
| `tenant_id` | `Literal["MGV", "MHB"]` |
| `text` | `str` |
| `previous` | `{kind, services, had_booking: bool} \| None` (follow-up cases) |
| `expected_kind` | `RequestKind \| "undecided"` |
| `expected_services` | `list[Domain]` |
| `category` | one of the 11 categories in research R-13 |
| `requires_tier2` | `bool` |
| `split` | `Literal["dev", "holdout"]` (about 70 / 30, stratified by category and tenant) |

**Authoring order and independence**: the cases are written **before** the vocabulary, from the
spec's examples and realistic guest phrasing, never by reading `vocabulary.yaml`. Vocabulary
work may be checked against `dev` only. The gate blocks on `holdout` accuracy. A case moved from
`holdout` to `dev` must be replaced with a new holdout case in the same change.

## `TurnOutcomeSummary` (`graph/turn_summary.py`, additive field `booking_outcome`) — not state-carried

Computed from `turn_outcomes(state)`, the pending interrupt and `escalate`. Returned by
`summarize_turn` for both `graph/api.py` and the console (FR-035).

| Field | Type |
|---|---|
| `state` | `Literal["confirmed", "held", "released", "handed_off", "awaiting_input", "nothing_booked"]` — precedence: `handed_off` > `awaiting_input` > `held` > `confirmed` > `released` > `nothing_booked` |
| `items` | `list[{key, service, venue, status}]` — this turn's records only |

Receipt `failed` (the resume raised) is a transport state shown by the console as "Failed"; it
is not part of this field.

## Relevance floor data (`graph/routing/thresholds.yaml`)

`knowledge_relevance_floor: float`, plus `calibrated_on` (date) and `source` (`research.md R-16`).
Owned by Track C. `rag/eval/calibrate_floor.py` prints the value and never writes this file.

## `ClassifierOutput` (`graph/routing/classifier.py`, not state-carried)

`{"kind": RequestKind, "services": list[Domain]}`, `extra="forbid"`, `services` max 5, unique.
Anything else → `Tier2Outcome.invalid`.

---

## State transitions touched

- **Hold** (unchanged state machine): the console now renders a countdown only for `pending` and
  `held`. `confirmed`, `released`, `expired` and `orphan_released` render the final status only
  (FR-034).
- **Conversation escalation**: a new entry path `MODIFY_REQUEST` sets `escalate=True`,
  `escalate_reason="modify_request"`. Every later message goes to `HUMAN_HANDOFF` through the
  existing `_route_from_entry` rule (disclosed consequence, ADR-055).
