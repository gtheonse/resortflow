# Research: Browse Requests

**Feature**: `011-browse-requests` | Decisions behind [plan.md](plan.md). Each item: Decision, Rationale,
Alternatives considered.

---

## R-01 — Where the browse decision lives

**Decision**: `browse` is a new `RequestKind` member. `RoutingDecision` (already checkpointed under the
existing `routing` key) gains optional `browse_filters: BrowseFilters | None` and `window: DateWindow | None`.
No new `ResortFlowState` key. `GRAPH_VERSION` 5.1 → 5.2 (minor).

**Rationale**: `routing` already round-trips through `EncryptedSerializer` and `plain_serde()` and already
carries the next-turn context (`PreviousTurn` is built from it). Two optional fields with defaults resume
cleanly from a pre-011 checkpoint (the fields read as `None`). A minor bump is correct: a major bump would
release every outstanding hold on the first post-upgrade turn (the reasoning ADR-056 already recorded).

**Alternatives**: a new `ResortFlowState` key `browse` (rejected: a second writer-owned key and a wider contract
change for data that is really part of "what was decided this turn"); encoding filters into `focus_keys`
strings (rejected: opaque, untestable, not inspectable).

## R-02 — Precedence, and the two things it must not break

**Decision**: `out_of_scope > modify > lookup-phrases > browse > possessive-lookup > book > smalltalk >
faq > undecided`. Browse requires an **enumeration cue** (list / show / what are / which / what's on /
what's available / what do you offer / what can I …) **and** a **catalogue noun** (venues, restaurants,
places to eat, events, shows, concerts, rooms, suites, treatments, services, options, experiences,
activities, things to do), or one of a small set of strong standalone phrases ("what's on", "what do you
offer", "what can I do here"). Lookup *phrases* ("what have I booked", "my itinerary", "my booking") are
checked first so "list my bookings" stays a lookup.

**Rationale**: The live defects were both precedence bugs: FAQ swallowed "what are the dining venues"
(`what` + `available`), and the possessive-lookup heuristic added in the demo-report fix swallowed "events in
my stay period" (`my` + `stay` + interrogative start). Placing browse above both, but gated by *cue AND
noun*, fixes both without catching "what time is checkout" (no enumeration cue, no catalogue noun) or
"do you have connecting rooms?" (`do you have` is deliberately not an enumeration cue — feature 010's
ground-truth case `faq-*-10` expects FAQ and must keep passing).

**Alternatives**: browse below FAQ with a "prefer browse if a catalogue noun is present" override
(rejected: two rules fighting instead of one ordering); an embedding classifier (rejected: Principle I,
and the unlabelled-corpus objection from 010's R-alternatives still holds).

## R-03 — Event windows: one fetch, filter locally

**Decision**: Event browse calls `search_events()` once (no `date`), follows `next_page_token` for at most
three pages, filters to the window in code, drops `sold_out`/`cancelled`, sorts soonest-first, caps at 10.

**Rationale**: The tool takes a single `date`, not a range (S-1). A per-day loop over a 28-day window is up
to 28 calls; one bounded fetch plus a pure filter is cheaper, testable offline, and has one failure mode.
The sim returns everything in one page with `next_page_token: null`; the three-page bound protects against
a real vendor's paging. Truncation is disclosed ("and N more"), never silent.

**Alternatives**: one call per day (rejected: N calls, N failure points); adding a date-range parameter to
the MCP tool (rejected: a frozen contract change, and the local filter is sufficient).

## R-04 — "My stay" comes from the booking view

**Decision**: `BookingRecord` gains an additive `ends_at` (from `Proposal.ends_at`, which the stay
`hold_placement` already stamps). `stay_period(records)` returns the arrival (`starts_at`) and departure
(`ends_at`) of the most recent stay record whose status is `confirmed` or `held`; `None` otherwise. With
`None` the node asks for dates (FR-019).

**Rationale**: The dates already exist in state once a room is held (the T085 fix from the demo report made
that reliable). `booking_view` is the single source of "what has this guest booked", deduped by
`idempotency_key`, so the stay period cannot disagree with what a lookup would say.

**Alternatives**: storing a dedicated `stay_period` key (rejected: derived data, would go stale); parsing the
dates back out of message text (rejected: fragile and it is exactly the assumption FR-019 forbids).

## R-05 — Gaming

**Decision**: A gaming-only browse routes to the existing gaming standalone graph via the existing
`routing.services == [GAMING]` branch (unchanged); that graph already runs `require_gaming_clear`, surfaces
`mandated_message` verbatim, and formats floor games. The general overview calls `require_gaming_clear`
fresh, on that turn, solely to decide whether to include the gaming line; restricted, self-excluded or
unavailable → the line is omitted (fail closed).

**Rationale**: One implementation of the responsible-gaming path (ADR-030/037); duplicating it in `BROWSE`
would create a second copy that could drift. The overview needs only the boolean, so it does not call
`list_casino_floor`.

**Alternatives**: `BROWSE` calling `list_casino_floor` itself (rejected: duplicate policy path); omitting
gaming from the overview entirely (rejected by the clarify answer: gaming is included).

## R-06 — Filters, occupancy and dates a browse assumes

**Decision**: Filters come from vocabulary data (`kinds.browse.filters`): cuisine (the dining cuisine set),
price band (`$`–`$$$$`, "cheap/budget/upscale" words), dietary (the structured restriction enum's words),
wellness category, event classification. Room browse uses 2 adults unless a party size is stated, and
**says** it assumed 2. Event windows default to the next 7 days; "next N days" starts today for events and
tomorrow for stays; the reply states the dates used.

**Rationale**: Consistent with how the stay booking path already defaults occupancy; stating the assumption
is what makes a default safe (FR-018). Vocabulary-as-data keeps it reviewable like the service vocabulary.

**Alternatives**: asking for occupancy first (rejected: a needless question for a read); hard-coding filter
words in Python (rejected: not reviewable data, against FR-004 of 010).

## R-07 — Reply composition

**Decision**: All browse text is a code template in `graph/replies.py`: list (name + a fixed set of fields
per service), overview (five service lines, gaming line conditional), empty, unavailable, and the dietary
note ("Allergen handling is confirmed when you book, where your restriction is recorded — I can't confirm a
venue is safe from a list"). Cap 10, then "and N more — say a cuisine, price or date to narrow". The
"showing your property" sentence is a constant used when the guest named another property.

**Rationale**: Principle IV (policy is code) and FR-009/FR-017 are easiest to guarantee, and to test
exhaustively, when no model or KB text can enter the reply.

## R-08 — Tier-2 classifier

**Decision**: Add `browse` to the allowed kinds (already code-injected from `RequestKind`, so the schema
stays closed and fail-closed); the prompt gains one definition line and an explicit rule: enumeration
requests about resort services are never `out_of_scope`. Live probes on 2026-09-30 showed the classifier
returning `faq`, `undecided`, or a timeout for near-identical phrasings of "list the resort experiences";
the 1.5 s hard timeout is therefore measured against the `UNAVAILABLE` rate over the browse cases at
calibration (R-11) and only changed with that data. Deterministic browse cues never reach tier 2.

**Rationale**: Feature 010 R-alternatives: the model is for the unbounded remainder; the fix for flaky
tier-2 answers on *common* phrasings is a deterministic cue, not a bigger model. Changing a timeout on a
hunch would trade one symptom for latency.

**Alternatives**: raising the timeout now (rejected: unmeasured); routing every "list …" to tier 2
(rejected: Principle I).

## R-09 — One-turn refinement (FR-005a)

**Decision**: `PreviousTurn` gains `filters` and `window` (read from the previous `RoutingDecision`);
`_try_carry_over` adds: previous kind `BROWSE` + a narrowing cue ("only", "just", "cheaper", "under",
"what about", "how about", "instead") + a filter token or a date phrase → `BROWSE` with the previous
services, filters merged with the new one, window replaced if a new date was given. One turn only.

**Rationale**: Same mechanism and same one-turn rule feature 010 built for booking follow-ups; no new state.

## R-10 — Clarify interaction (FR-005)

**Decision**: When `INTENT_ROUTER` decides `BROWSE`, it also writes `clarification_count: 0`. A browse is a
resolution of the clarification, not a failed answer to it, so it must not consume the bounded
clarification budget (`MAX_CLARIFICATION_COUNT`).

**Rationale**: Otherwise a guest who answers the assistant's own question with "list the options" twice
gets escalated to a human for doing nothing wrong.

## R-11 — Measurement

**Decision**: New category set in `routing_cases.yaml` (browse per service, filters, stay-period phrasing,
refinements) **and near-miss cases that must NOT be browse** (FAQ: "what time is checkout", "what time does
the pool close", "do you have connecting rooms"; booking: "book a table"; lookup: "what have I booked",
"list my bookings"). Authored before and independently of the vocabulary; dev/holdout split with the
per-category-per-tenant floor from 010. New blocking metrics: `browse_accuracy` (kind + services, holdout)
and `browse_catalogue_misroute` (catalogue cases that landed on `faq` or `out_of_scope`, must be 0).
Calibrated against live tier 2 at the end, as T070/T080 were.

**Rationale**: 010's live testing found the unmeasured `dev` split hid 36% failure; the near-misses are
what protect FAQ from the new rule's reach.

## R-12 — What is not solved here

- The two deferred 010 routing classes (bare-"and" clause splitting; past-tense narrative vs request) are
  unchanged. Browse + book joined by a comma-and/"also" reuses the existing clause-pair path (generalised
  from lookup+book); bare-"and" mixes still fall to the clarifying question.
- Recommendations/ranking, cross-property browse and gaming wagering remain out of scope (spec).
- Option A from feature 010's recovery work (loop `RECOVERY` through `COMMIT`) is unrelated and untouched.

## R-13 — Calibration of the browse gate (T049, 2026-09-30)

Measured with live Azure OpenAI credentials configured (`eval.routing_scorer.default_routing_classifier`) over
every `browse_*` case, **both splits**: `dev` 64/64 correct, `holdout` 32/32 correct; `browse_accuracy` = 1.000
on both. Tier-2 was reached by **0 of 96** browse cases, so `browse_llm_tier_share` = 0.000 and the live
classifier made no call — there was no tier-2 `UNAVAILABLE` rate to observe (R-08's risk is retired for browse:
recognition is deterministic and does not depend on the model).

Decisions:
- `browse_accuracy` floor stays **0.95** (measured 1.000; the gap is headroom for vocabulary drift, not slack).
- `browse_llm_tier_share` stays **report-only** (`BROWSE_LLM_TIER_SHARE_BLOCKING = False`). 0.000 is honest but
  vacuous — the browse set has no `requires_tier2` case, the same caveat feature 010 hit; flipping it to
  blocking would gate nothing. Revisit when genuinely ambiguous browse phrasings are added.
- Classifier timeout unchanged (1.5 s): browse never waits on it.

## R-12 addendum — browse plus book without a comma (T059, 2026-10-01)

Decision: **accepted deferral, unchanged.** "show me the restaurants, and book the steakhouse for 2 tomorrow at 7pm" (with the comma)
shows the list and books the venue, verified end to end live and by `agents/tests/test_browse_dining.py`. The same sentence with a bare
"and" has an explicit book cue, so it is a booking: the guest gets the booking they asked for and not the list. Nothing irreversible
or wrong happens, and splitting on a bare "and" is the unsafe change R-12 already declined (it would also split "dinner and a show").
Revisit only if live traffic shows guests asking for both in one comma-less breath.
