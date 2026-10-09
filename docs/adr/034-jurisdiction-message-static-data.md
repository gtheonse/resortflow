# ADR-034 — Jurisdiction-mandated message text is static per-tenant data, not evaluated logic

**Status**: Accepted
**Date**: 2026-09-12
**Feature**: 006-policy-guardrails-hitl

## Context

ADR-028 settled that `policy/` is plain deterministic code, explicitly rejecting a
data-driven rules engine because a rule expressed as interpreted data can be "undone by a
config edit" for the safety-critical checks. But FR-012 requires jurisdiction-mandated
messaging that differs per tenant (`NV` vs `MA`) and per trigger (`gaming_content`,
`offer`) — and that message text is legal copy (e.g., the Nevada Council on Problem
Gambling contact string already visible in `agents/gaming/tests/fakes.py`'s fixture),
not a decision this codebase makes. This ADR exists to draw the line ADR-028 left open:
is message text an exception to "no data-driven rules," or does it need to be hardcoded
per call site instead?

## Options Considered

**1. Hardcode each message inline at every call site that needs one.** Consistent with
"no data as rules" taken maximally literally. Rejected: this is legal copy that a
compliance/legal team edits independent of any code change to *when* it's shown — forcing
a code change (and a full review cycle for `policy/`'s safety-critical modules) merely to
fix a phone number or update mandated wording conflates two different kinds of change
that don't carry the same risk.

**2. A small static per-tenant, per-trigger lookup table** (`policy/jurisdiction.py`'s
`JURISDICTION_MESSAGES` dict, or an equivalent YAML data file mirroring
`content/corpus/{mgv,mhb}/policies.yaml`'s existing per-tenant data-file convention),
looked up deterministically by `mandated_message_for(jurisdiction, trigger)` and
concatenated — never interpreted, never conditionally suppressed by anything other than
"does an entry exist for this (jurisdiction, trigger) pair."

## Decision

**Option 2.** The message text lives as static data; the *decision to append it* (which
triggers require it, that it's deterministic and never omitted for tone) is code, per
FR-012 and ADR-028. This is not a reopening of ADR-028's "no rules as data" position: a
lookup table with no branching logic, no precedence rules, and no evaluation semantics of
its own is not a rules engine — it is exactly as data-driven as `_KNOWN_CODE_DISPOSITIONS`
in `graph/errors.py`, an existing dict lookup this project already treats as ordinary
code, not as the kind of "policy as data" this project's constitution warns against.

## Consequences

**Made easier.** Updating mandated wording (a genuinely frequent, legally-driven event
in a regulated business) is a data change reviewable by whoever owns the legal-copy
sign-off, without touching `policy/`'s enforcement logic or requiring its test suite to
re-verify unrelated behavior.

**Made harder.** Nothing measurable — this is strictly smaller surface area than option 1
would have produced.

**Foreclosed.** No future feature may attach conditional logic to `JURISDICTION_MESSAGES`
itself (e.g., "suppress this message if X") without revisiting this ADR — any such logic
belongs in the *caller* of `mandated_message_for`, keeping the lookup table itself free of
evaluation semantics.
