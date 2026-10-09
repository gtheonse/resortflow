# Interface Contract: Guest-facing replies and console hold display

**Feature**: `010-tiered-intent-routing` | All replies are deterministic templates in
`graph/replies.py`. No model composes any of them. English only (spec Assumptions).

## Booking reply (`_compose_respond_message`, used by `RESPOND` and `DETERMINISTIC`)

Input: `turn_outcomes(state)` (data-model), **not** the cumulative `confirmations` channel.

| This turn's outcomes | Reply |
|---|---|
| `escalate` set | unchanged: "I've connected this request with a team member (…)." |
| none, and `RELEASE` messaged | unchanged: no extra message |
| none | unchanged: "Nothing was booked for this request." |
| all confirmed | "Confirmed: <venue> — <date> at <time>, party of <n> (confirmation <code>)." one line per item |
| some held, not confirmed | per held item: "<venue> — <date> at <time> is **held, not yet confirmed**; the hold expires at <HH:MM>." |
| mixed | one line per item with its own status. **Never** "All set" unless every item this turn is confirmed |
| recovery / partial (existing saga kinds) | existing sentence, now naming only this turn's confirmed items |

Forbidden (asserted by test): the words "confirmed", "booked" or "all set" applied to an item
whose status this turn is not `confirmed` (FR-032, SC-011). An item confirmed on an earlier turn
never appears in this turn's reply (FR-031).

**No baseline available** (a pre-010 conversation's first post-upgrade turn, per data-model.md):
`turn_outcomes` cannot be computed, so the composer never uses "confirmed"/"held" **this-turn**
wording at all — falling back to "every record is new" would report an old confirmation as
confirmed by this turn, violating FR-031. It falls back to the lookup presentation instead: every
deduped record (data-model `BookingRecord`) with its current status, introduced as "here's where
things stand:" rather than as an outcome of this turn.

## Lookup reply (`BOOKING_LOOKUP`)

- Matches found: one line per record: venue, date, time, party size, status wording
  (data-model `LookupAnswer`).
- No match: "I couldn't find a booking matching that in this conversation." plus "Here's what you
  have: …" if `all_records` is non-empty, else "You don't have any bookings in this conversation
  yet." Never starts a booking (FR-016).
- Held dining items: live re-check first (`check_dining_status(kind="hold")`). Other services, or
  a re-check error: "held — status could not be confirmed right now" (research R-10).

## Decline reply (`DECLINE`)

"I can't help with <flights/travel/that> — I'm the resort assistant. I can help with dining,
shows and entertainment, spa and wellness, your room and stay, and loyalty questions." The noun
comes from the matched out-of-scope vocabulary entry, else "that". Contains **no** retrieved text
(FR-018): the node has no retriever dependency, asserted structurally.

## Small-talk reply (`SMALLTALK`)

Greeting → "Hello! I can help with dining, shows, spa, your stay, and loyalty questions — what
would you like?" Thanks or acknowledgement → "You're welcome — anything else?" No retrieval, no
model (FR-019).

## Modify reply (`MODIFY_REQUEST`)

"I can't change or cancel bookings myself, so I've passed your request about <venue, date time>
to a team member." When no booking can be identified: "…your request to a team member." Then
`HUMAN_HANDOFF`'s standard line follows. `focus_keys` identify the booking: carry-over focus, else
the lookup matcher over the message, else none.

## Console (`ui/static/index.html`)

Hold card countdown only when `status ∈ {pending, held}`. For `confirmed`, `released`, `expired`,
`orphan_released`: show the status badge and **no** countdown, no "EXPIRED" flip (FR-034).

## Turn summary: additive field (FR-035)

`graph/turn_summary.summarize_turn` gains one **additive** key, `booking_outcome`
(data-model `TurnOutcomeSummary`). Every existing key is unchanged. Both `graph/api.py` and the
console receive it.

## Console approval/clarify badge (FR-035, SC-014)

After a receipt reaches `succeeded`, the badge comes from `result.booking_outcome.state`, never
from the receipt status:

| `booking_outcome.state` | Badge |
|---|---|
| `confirmed` | Confirmed |
| `held` | Held — not yet confirmed |
| `released` | Released |
| `handed_off` | Passed to a team member |
| `awaiting_input` | Waiting for your input (the next interrupt card follows) |
| `nothing_booked` | Done — nothing booked |

A receipt `failed` shows "Failed". The word "Confirmed" appears only for `confirmed`.
