# Quickstart: validating Browse Requests

**Feature**: `011-browse-requests` | Validation guide only: what to run and what you should see.
Design: [data-model.md](data-model.md), [contracts/](contracts/).

## Prerequisites

- Offline suites can also run in Linux: `docker compose --profile test run --rm tests` (the only route on machines where Smart
  App Control blocks the spaCy/psycopg wheels).
- Python ≥ 3.11 with the project installed; run pytest with `-p no:schemathesis` on machines where
  Application Control blocks the `jsonschema_rs` DLL (environmental).
- Scenarios 1–7 are offline (fake tools, no network). Scenarios 8–10 are live: `docker compose --profile sims
  --profile app up -d`, `.env` with Azure OpenAI credentials, the ticketing sim seeded with rolling dates
  (`POST http://localhost:9003/__sim__/reset` after the seed change is in the running image).

## 1. Recognition: the reported phrases and the near-misses

```bash
pytest -p no:schemathesis graph/routing/tests/test_browse_rules.py graph/routing/tests/test_browse_filters.py \n  graph/routing/tests/test_tenant_profile.py -q
python -m graph.routing.explain "what are the dining venues available in nevada"
python -m graph.routing.explain "list the resort experiences"
python -m graph.routing.explain "what all events are happening in my stay period"
python -m graph.routing.explain "what events are happening in the next 5 days"
python -m graph.routing.explain "what time is checkout"
python -m graph.routing.explain "list my bookings"
```

Expected: the first four `browse` (dining; no service; entertainment; entertainment), tier 1; `what time is
checkout` stays `faq`; `list my bookings` stays `lookup`.

## 2. Dates and stay length

```bash
pytest -p no:schemathesis graph/routing/tests/test_date_range.py graph/tests/test_stay_period.py -q
python -m graph.routing.explain "book stay for next 5 days"
```

Expected: `book`/`[stay]`; ranges for `next 5 days`, `this weekend`, `from oct 5 to oct 8` resolve as in
[browse-router-api.md](contracts/browse-router-api.md); the stay period comes from a held/confirmed room.

## 3. Browse answers (per service, fake tools)

```bash
pytest -p no:schemathesis agents/tests/test_browse_dining.py agents/tests/test_browse_events.py \
  agents/tests/test_browse_wellness.py agents/tests/test_browse_stay.py -q
```

Expected: each returns only its own tenant's items, capped at 10 with "and N more", filtered as asked, no
knowledge-base text; a dietary filter adds the "state it when you book" line and never a safety claim;
provider failure gives the "couldn't retrieve" line and no invented items.

## 4. Read-only guarantee (SC-009)

```bash
pytest -p no:schemathesis agents/tests/test_browse_readonly_structural.py -q
```

Expected: the `BROWSE` node's code references only read tool methods; a browse turn leaves `holds`,
`proposals`, `confirmations` and the ledger untouched.

## 5. Gaming gate (SC-010)

```bash
pytest -p no:schemathesis agents/tests/test_browse_overview_gate.py agents/tests/test_browse_gaming_gate.py -q
```

Expected: cleared guest sees the casino line/list; self-excluded and status-unavailable guests get no list and
no gaming line in the overview; the check runs fresh every turn.

## 6. Refinement and clarify

```bash
pytest -p no:schemathesis agents/tests/test_browse_refinement.py agents/tests/test_browse_clarify.py -q
```

Expected: "only Japanese" right after a dining browse narrows it; after an intervening turn it does not;
answering the clarifying question with "list the experiences" returns the overview and leaves
`clarification_count` at 0.

## 7. Resume and the gate

```bash
pytest -p no:schemathesis graph/tests/test_state_amendment_011.py eval/tests/test_browse_cases_data.py \
  eval/tests/test_browse_gate_degraded.py prompts/tests/test_routing_prompt.py -q
python -m eval.runner --tier pr
```

Expected: a 5.1 checkpoint resumes with both new fields `None`; `browse_accuracy ≥ 0.95` on holdout,
`browse_catalogue_misroute = 0`, `near_miss_browse_leak = 0`; the degraded test fails the gate.

## 8. Live: the reported defects (SC-001)

```bash
python -m ui.run     # http://localhost:8420, new session
```

| Send | Expect |
|---|---|
| `what are the dining venues available in nevada` | node order `… intent_router → browse`; the 5 real venues, no allergen text |
| `only japanese` | narrows to the Japanese venue(s) (one-turn refinement) |
| `list the resort experiences` | the overview, not a decline |
| `book stay for next 5 days` | asks only for the arrival date, mentions 5 nights |
| `what events are happening in the next 5 days` | events within 5 days (rolling seed), "I used …" dates stated |

## 9. Live: my stay period

`book a room for oct 5 to oct 8 for 2` → approve → `what all events are happening in my stay period`:
only events dated Oct 5–8, intro states "your stay: Oct 5–8". A new session asking the same question gets
"I don't see a stay on your booking yet — which dates should I look at?".

## 10. Live: classifier and telemetry

`resortflow.routing.kind = browse` appears on the `intent_router` span in Langfuse with no message text. With
credentials, the `requires_tier2` browse cases score; without, they report not-evaluated (never a pass).
