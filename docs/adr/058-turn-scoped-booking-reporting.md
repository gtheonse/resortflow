# ADR-058 — Turn-scoped booking reporting: a baseline snapshot, a writer fix, and reader dedupe

**Status**: Accepted
**Date**: 2026-09-28
**Feature**: 010-tiered-intent-routing

## Context

A live session showed the assistant say a booking was "booked" while the verification console
still showed the underlying hold with its TTL actively counting down. Reading the code (research
R-12) found three compounding causes, none of them hypothetical: `_compose_respond_message` decides
"confirmed" from whether the *conversation* has ever held any confirmation, not whether *this turn*
produced one; `_deterministic_core`'s copy-back forwards the outer state's full `confirmations` list
into the inner sub-thread as input and then copies the inner sub-thread's full list back out, so a
single-service turn re-appends every earlier confirmation on both sides; and the console's hold-card
countdown runs for any hold with an `expires_at`, whatever its status, including `confirmed`. The
question this ADR has to answer is not whether to fix the symptom — FR-031 to FR-035 already commit
to that — but *how the assistant is supposed to know what "this turn" even means*, since `ResortFlowState`
today has no turn boundary at all.

## Options Considered

**1. A turn-stamped saga-event log** — every hold placement, confirmation and release writes a
`SagaEvent` with `detail["turn"]` set to the current turn's identity (`SagaEvent.detail` is already
`dict[str, str]`, so no frozen-model change is needed to carry it). This is the audit-trail design
this ADR would choose first on the merits: it makes "what happened, in what order, on which turn"
a first-class, replayable fact rather than a derived snapshot, and it is the shape a proper
agent-harness answer to "why did the console say confirmed" should eventually have. It is rejected
**for this feature**, not on principle: today, only compensation failure writes a saga event on the
single-service path at all (`agents/dining/edges/conditions.py`) — every other single-domain
hold/confirm success writes none. Making every domain's hold/confirm success path write one touches
roughly eight `[CRIT]` saga-writing nodes across four domains, each requiring its own saga-permutation
test coverage per the constitution's "TEST BEFORE ADVANCE" standard. Worse, `RESPOND`, `RECOVERY` and
the reply composer today read only the **last 10** saga events (`graph/supervisor.py`, three call
sites) — a denser event stream from every hold/confirm, not just compensation failures, risks
pushing a `compensated`/`released` event out of that window on a long-running conversation, silently
changing recovery behaviour this feature has no mandate to touch.

**2. Diff against LangGraph's own checkpoint history** — compare the current state to the checkpoint
recorded at this run's start (`CheckpointMetadata.source == "input"`). Rejected: it couples the
reply composer to persistence internals the rest of this codebase deliberately keeps behind the
checkpointer abstraction; `DETERMINISTIC`'s inner sub-thread keeps its own separate checkpoint
history, so "the current run's start" is ambiguous across the outer/inner boundary; it breaks under
checkpoint pruning (not configured today, but not foreclosed either); and every *resume* — including
an approval resume that is logically the continuation of one turn — would count as a new run under
this definition, which is exactly backwards for FR-031's "this turn" semantics across an
approval pause.

**3. A `turn_baseline` snapshot** — record, once per turn, which booking-record keys and hold
statuses existed when `INTENT_ROUTER` ran; report as "this turn's outcome" whatever changed since
(option (a) alone), extended with a `turn_index` field that costs nothing today and gives option 1
a stable per-conversation turn identity to stamp events with later (option (d), the chosen shape).

## Decision

**Option 3 (research R-12's option d).** `graph.bookings.turn_outcomes(state)` compares the current
`booking_view(state)` against `state["turn_baseline"]` and returns only records that are new or
whose status changed; `_compose_respond_message` is rewritten to report from that function instead
of the cumulative `confirmations` channel. The writer bug is fixed at its source —
`_deterministic_core`'s copy-back no longer forwards the append-only channels as input to an
already-checkpointed sub-thread, and copies back only genuinely new confirmations and only the new
tail of `failures`/`saga_log`/`policy_flags` — rather than papering over the duplication with a
reducer change; `graph/state.py`'s frozen `append_only` reducer is untouched. Readers additionally
dedupe by booking identity (`booking_view`'s join-and-dedupe-by-`idempotency_key`), so a pre-010
checkpoint that already contains the old bug's duplicates reports correctly the moment it resumes,
with no migration. The console's badge is driven by a new, additive `booking_outcome` field on
`summarize_turn` (FR-035) rather than by the receipt's transport-level "did the resume raise"
status, which is what let **Decline all** and an expired-before-commit hold both show "Confirmed"
today. A missing baseline (a pre-010 conversation's first post-upgrade turn) is not treated as
"every record is new" — that would report an old confirmation as confirmed by this turn, which
FR-031 forbids outright — it falls back to the lookup-style "here's where things stand" presentation
instead, never turn-outcome wording, until the next turn's baseline makes normal reporting possible
again.

## Consequences

**Made easier.** The fix ships without touching any `[CRIT]` saga-writing node or its test coverage,
and without risking `RESPOND`/`RECOVERY`'s existing "last 10 events" recovery-window behaviour. A
pre-010 conversation self-heals on resume rather than needing a data migration.

**Made harder.** "This turn's outcome" is a derived comparison (current view vs. a snapshot), not a
queryable history — a future question like "which turn confirmed this booking, and how long after
it was held" cannot be answered from `turn_baseline` alone; it needs option 1's event log, which
this feature deliberately does not build.

**Foreclosed.** No future change may repurpose `turn_baseline`'s absence as a signal for anything
other than "report without this-turn wording" — in particular, it must never be read as "nothing
has ever been booked," which is a different, stronger claim `booking_view` alone (not
`turn_baseline`) is the correct source for.
