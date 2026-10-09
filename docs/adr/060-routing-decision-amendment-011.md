# ADR-060 — `RoutingDecision` amendment 011: a `browse` kind and optional filters/window, no new state key

**Status**: Accepted (2026-09-30)
**Date**: 2026-09-30
**Feature**: 011-browse-requests

## Context

Browse (ADR-059) needs the decision to carry two things that feature 010's `RoutingDecision` does not: the
**filters** a guest gave ("Japanese", "cheaper", a dietary need) and the **date window** the request resolved to
(and how — stated, defaulted, from the guest's stay, or "needs dates"). Both are read again on the next turn:
"only Japanese" has no service or cue of its own and can only be understood as a narrowing of the previous
browse, and the reply must be able to say which dates it used. `routing` is already a checkpointed `ResortFlowState`
key that round-trips through `EncryptedSerializer`, is overwritten once per turn by `INTENT_ROUTER`, and is what
`PreviousTurn` is built from. The question is where the new data lives without disturbing the frozen contract or
in-flight conversations (Principle V).

## Options Considered

**1. A new `ResortFlowState` key (`browse`).** A second writer-owned key, a wider contract change (the frozen key
count moves from 25), a new reducer decision, and a new checkpoint field for data that is really part of "what
was decided this turn". Rejected.

**2. Encode filters into `focus_keys` or another existing string field.** No schema change, but opaque,
untestable, and not inspectable — the opposite of Principle V. Rejected.

**3. Re-derive filters and window from the message text on the next turn.** Impossible for "only Japanese" (no
cue), and non-reproducible whenever tier 2 decided the kind. Rejected.

**4. Extend `RoutingDecision` with two optional fields (chosen).**

## Decision

Adopt option 4.

- `RequestKind` gains `BROWSE = "browse"`.
- `RoutingDecision` gains `browse_filters: BrowseFilters | None = None` and `window: DateWindow | None = None`.
  New frozen models `BrowseFilters` (cuisine, price band, the structured `DietaryRestriction` enum — never free
  text —, category, party size) and `DateWindow` (`start`, `end`, `source` in `stated | default | stay_period |
  needs_dates`) are added to the serializer allowlist.
- Invariants: the two fields are valid only when `kind == BROWSE`; a browse may include gaming only alone (the
  gated gaming-only path); `window.end >= window.start`; the existing `secondary_kind` rule is relaxed so `BOOK`
  may be secondary under `LOOKUP` **or** `BROWSE` (a browse-plus-book message), and nothing else changes.
- `ResortFlowState` key set is unchanged (still 25). `GRAPH_VERSION` `5.1` → `5.2`, a **minor** bump: a 5.1
  checkpoint's `RoutingDecision` has neither field, both read `None`, and it behaves exactly as before. A major
  bump would release every outstanding hold on the first post-upgrade turn (ADR-056's reasoning, unchanged).
- The next turn's `PreviousTurn` reads the previous decision's services, filters and window (never message
  text) — used both to narrow a browse and to hand a following booking request its service (ADR-059 amendment); because `routing` is overwritten every turn, refinement is one-turn-only by construction (FR-005a).
- `BookingRecord` gains an additive `ends_at` (a stay's departure) so "my stay" can resolve to the guest's own
  held or confirmed dates; it stays `None` for services with no end.

## Consequences

- No new state key, reducer or writer; resume of pre-011 conversations is by default, and tested with a 5.1
  fixture.
- A missing allowlist entry would break resume of a browse decision, so it is a `[CRIT]` task with a
  serializer round-trip test for both serializers and no pickle fallback.
- `RoutingDecision` grows; its validator now carries two more rules that a future kind must keep consistent.
  The relaxed `secondary_kind` invariant is the one place an existing rule loosened, covered by a test that
  still rejects every other combination.
- The window's `source` makes the reply honest about assumptions ("I used the next 7 days", "your stay:
  Oct 5–8") and is what lets a `needs_dates` browse be completed by a one-turn follow-up.
