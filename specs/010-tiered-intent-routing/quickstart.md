# Quickstart: validating Tiered Intent Routing

**Feature**: `010-tiered-intent-routing` | Validation guide only: what to run and what you should
see. Design details: [data-model.md](data-model.md), [contracts/](contracts/).

## Prerequisites

- Python ≥ 3.11 with the project installed (`pip install -e .[dev]`).
- Offline scenarios (1–5) need nothing else. The router's tiers 0–1 and the reply templates have
  no I/O.
- Live scenarios (6–8) need `make up && make seed`, `.env` with Azure OpenAI credentials, and
  `python -m rag.indexer` run once.

## 1. Router: typos, variants, substring traps (US1, SC-001, SC-003)

```bash
pytest graph/routing/tests -q
# `graph.routing.explain` is a small debug CLI added by this feature
python -m graph.routing.explain "book a restaurent table for 4 at 8pm"
python -m graph.routing.explain "can I get tickets for the theater tonight"
python -m graph.routing.explain "tabel for 2 tonight"
python -m graph.routing.explain "cancle my dinner"
python -m graph.routing.explain "can I zoom in on the room photos"
```

`graph.routing.explain` prints the `RoutingDecision` (kind, services, tier, confidence, the
vocabulary entry that matched). Expected: `book/[dining]` tier 0–1; `book/[entertainment]` (not
dining); `book/[dining]` via a letter swap; `modify/[dining]`; `zoom` not matched as `room`.

## 2. Routing gate (US5, SC-001/002/003/005/009/013)

```bash
python -m eval.runner --tier pr        # routing metrics are part of the PR tier (added by this feature)
pytest eval/tests/test_routing_cases_data.py eval/tests/test_routing_gate_degraded.py -q
```

Expected: `routing_accuracy ≥ 0.95` (measured on the `holdout` split) per tenant and overall; `oos_passthrough_rate = 0`;
`substring_trap_misroutes = 0`; `modify_book_starts = 0`; `llm_tier_share` reported. Without
credentials, `routing_tier2_cases` appears under not-evaluated (INCOMPLETE), not as a pass. The
degraded test must **fail** the gate.

## 3. Tier-2 failure matrix and SC-006

```bash
pytest graph/routing/tests/test_classifier_failures.py -q
```

Every row of the router-api failure matrix routes to `CLARIFY`. The turn completes and nothing
raises.

## 4. Booking reply and single recording (US6, SC-011/SC-012)

```bash
pytest agents/tests/test_turn_reporting.py graph/tests/test_turn_summary_outcome.py ui/tests/test_console_outcome.py -q
```

The first test in this file is the regression that **fails before the fix**: confirm a dining
booking on turn 1, place a hold without confirming on turn 2, then send 5 more turns. Expected
after the fix: turn 2's reply says "held, not yet confirmed" with an expiry and never "confirmed";
exactly one confirmation record for turn 1's booking.
The approval-path cases must also pass: approve → confirmed once; decline → released; expired
hold → handed off. `booking_outcome.state` matches each, and none shows "Confirmed" unless it
confirmed (FR-035, SC-014).

## 5. Old checkpoint resume (FR-027, FR-033)

```bash
pytest graph/tests/test_state_amendment_010.py -q
```

A fixture checkpoint written without `routing`/`turn_baseline` and holding a duplicated
confirmation resumes. The lookup lists the booking once, and the state round-trips through
`EncryptedSerializer` and `plain_serde()`.

## 6. Live: the two reported defects (SC-010)

```bash
python -m ui.run     # http://localhost:8420, tenant MGV
```

| Send | Expect |
|---|---|
| `book flight for me to fly to India` | decline naming flights and listing the five services; node order `… intent_router → decline`; no knowledge-base text |
| `table for 4 on sep 30 at 8pm` (approve if asked) | confirmation naming venue, date, time, party of 4 |
| `what is the restaurent i got reserved for sep 30, 8 PM?` | `booking_lookup`: names that restaurant, Sep 30, 8:00 PM, party of 4, "confirmed" |
| `is it confirmed?` | follow-up carry-over: same booking, no clarifying question |

In the right-hand panel the confirmed hold shows status `confirmed` and **no countdown** (FR-034).
In a flow that pauses for approval, **Decline all** must show a "Released" badge, never
"Confirmed" (the pre-010 console showed "Confirmed" for any resume that didn't crash).
The `intent_router` span in Langfuse carries `resortflow.routing.kind/tier/confidence` and no message
text.

## 7. Live: held-not-confirmed wording (FR-032) and hand-off (US4)

Trigger a dietary-conflict approval (for example "table for 2 tomorrow 7pm, severe peanut allergy"
at a venue with a conflicting menu) and **do not** approve. Ask `what have I booked?`. Expected:
"held, not yet confirmed — hold expires at HH:MM", and the dining hold re-checked live. Then send
`cancel my dinner`. Expected: the `modify_request` reply identifying the booking, then the hand-off
line. No new booking is started.

## 8. Knowledge relevance floor (US3, SC-008)

```bash
python -m rag.eval.runner               # now also runs the off-topic section (added by this feature)
python -m rag.eval.calibrate_floor      # new: prints both score distributions and the chosen floor
```

Expected: 100% of answerable cases answered and 100% of off-topic cases refused. Record the
chosen floor and distributions in research.md R-16. Live check: `what time does the pool close?`
returns the relevant passage only (at most 2), and `what's the weather in Delhi` gets the decline.

## 9. Latency (SC-007)

```bash
pytest graph/routing/tests/test_latency.py -q
```

Median added time for tiers 0–1 over the labelled set is < 5 ms.
