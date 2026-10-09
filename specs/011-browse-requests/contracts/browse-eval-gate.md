# Contract: Browse evaluation gate

Extends [routing-eval-gate.md](../../010-tiered-intent-routing/contracts/routing-eval-gate.md). Same
deterministic scorer (`eval/routing_scorer.py`), same tenant stratification, same dev/holdout rule: **blocking
values come from the `holdout` split only; `dev` is reported for diagnosis.**

## Cases (`eval/data/routing_cases.yaml`, authored BEFORE and independently of the vocabulary)

New categories, ≥ 4 cases per category per tenant, ≥ 2 of them in `holdout` per category per tenant:

| Category | Expected kind | Examples (each tenant carries its own copy) |
|---|---|---|
| `browse_dining` | `browse` [dining] | "what are the dining venues available in nevada" · "which restaurants do you have" · "list restaurants" · "restaurent options" (typo) |
| `browse_events` | `browse` [entertainment] | "what events are happening in the next 5 days" · "any shows this weekend" · "what shows are on" |
| `browse_stay_period` | `browse` [entertainment] (window from stay) | "what all events are happening in my stay period" · "what's on during my stay" |
| `browse_overview` | `browse` [] | "list the resort experiences" · "what do you offer" · "what can I do here" |
| `browse_wellness` | `browse` [wellness] | "what spa treatments do you have" · "list wellness services" |
| `browse_rooms` | `browse` [stay] | "what rooms are available oct 5 to oct 8" · "list the suites" |
| `browse_gaming` | `browse` [gaming] | "what casino games do you have" · "what's on the casino floor" |
| `browse_refinement` | `browse` (carried) | `previous: {kind: browse, services: [dining]}` + "only japanese" · "cheaper ones" |
| `near_miss_faq` | `faq` | "what time is checkout" · "what time does the pool close" · "what's the room rate" · "do you have connecting rooms" · "what time do the shows start" |
| `near_miss_book` | `book` | "book a table for 2" · "get me tickets to a show" · "I'd like a room for 3 nights" |
| `near_miss_lookup` | `lookup` | "what have I booked" · "list my bookings" · "is my dinner confirmed" |
| `stay_length` | `book` [stay] | "book stay for next 5 days" · "a room for 3 nights" |

Every phrase reported live (spec Context) appears verbatim with its label. `previous` uses the existing case
field. `requires_tier2` cases (genuinely ambiguous browse-like phrasing) are scored only with credentials,
never fabricated, as in 010.

## Metrics (per tenant and overall; all on `holdout`)

| Metric | Definition | Floor/ceiling | Direction | Blocking |
|---|---|---|---|---|
| `browse_accuracy` | fraction of `browse_*` cases with the right kind and services | ≥ 0.95 (placeholder; calibrated) | higher | yes |
| `browse_catalogue_misroute` | count of `browse_*` cases decided as `faq` or `out_of_scope` | 0 | lower | yes (SC-002) |
| `near_miss_browse_leak` | count of `near_miss_*` cases wrongly decided as `browse` | 0 | lower | yes (SC-003) |
| `stay_length_accuracy` | fraction of `stay_length` cases routed to stay | ≥ 0.95 | higher | yes |
| `browse_llm_tier_share` | share of browse cases that reached tier 2 | ≤ 0.10 | lower | report-only until calibrated |

Existing 010 metrics (`routing_accuracy`, `oos_passthrough_rate`, …) still run over the whole set, so a browse
change that harms other kinds is caught by the existing gate.

## Degraded-config check

`eval/tests/test_browse_gate_degraded.py`: with the browse vocabulary removed, holdout `browse_accuracy` falls
below its floor and the verdict is FAIL (constitution's degraded-configuration rule).

## Calibration (recorded in research.md)

Run against the live classifier as T070/T080 did. Placeholders (0.95, 0.10) change only with the measurement
recorded; the dev-split is measured too and reported, so a repeat of feature 010's hidden-failure gap
cannot happen.
