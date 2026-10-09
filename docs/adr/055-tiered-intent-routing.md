# ADR-055 — Tiered intent routing: deterministic first, a constrained economy-tier classifier last

**Status**: Accepted
**Date**: 2026-09-28
**Feature**: 010-tiered-intent-routing

## Context

Three live defects came out of the verification console, all with the same root cause: the
router decided what a guest wanted by checking whether any of a short word list appeared
*anywhere* inside the message. That let "theater" trigger a dining match on "eat", let a single
typo ("restaurent") or an inflection ("reserved") fall through entirely, and gave an out-of-scope
request ("book flight for me to fly to India") no signal at all — so it fell into the general
knowledge-answer path, which always returns its three closest documents however unrelated.
Constitution Principle I requires an LLM only where intent is genuinely unbounded, and the
Governance section requires any new LLM node to justify that choice explicitly. The question this
ADR answers is not "should routing improve" — the spec (FR-001 to FR-030) already settles the
mechanics — but *where the one new model call in this feature is allowed to sit*, and what happens
to routing quality with that model unavailable.

## Options Considered

**1. Fix tier 0/1 only, no model call at all.** Whole-word tolerant matching plus deterministic
kind rules closes the substring-trap and typo defects (User Story 1) without adding anything
non-deterministic. Rejected as the *complete* answer: some messages are genuinely ambiguous by
construction (a typo equidistant between two services, an out-of-scope request phrased so no
listed vocabulary word appears), and forcing every one of those into the clarifying question
measurably increases friction for no safety benefit — the deterministic tiers already report
`ambiguous`/low-confidence precisely so a further tier can attempt them.

**2. An LLM router for all traffic**, replacing tiers 0/1 outright. Simpler to build and maintain
as one code path. Rejected outright: it violates Principle I directly (single-domain,
low-ambiguity requests are exactly what "an LLM MUST NOT decide" covers), removes the ~5 ms/message
latency budget the deterministic tiers give SC-007, and makes routing non-reproducible offline —
the eval gate (US5) could no longer run without live model credentials.

**3. Tiers 0 and 1 deterministic; a constrained tier-2 classifier only for what they cannot decide**
(the chosen shape). The classifier's output is restricted to the same closed `RequestKind`/`Domain`
enumerations tier 1 already uses, validated by code before anything downstream sees it, and
capped by SC-005 at no more than 20% of traffic. Every failure mode (no credentials, timeout,
malformed output, an out-of-enum value) degrades to the existing clarifying question rather than
to a guess.

## Decision

**Option 3.** `graph.routing.router.route()` runs tier 0 (tolerant vocabulary matching), then tier 1
(deterministic kind rules and one-turn follow-up carry-over); only a decision below the
`HIGH_CONFIDENCE` gate — and never a `smalltalk` or `out_of_scope` decision, both of which tier 1
can already assert with certainty — reaches tier 2. Tier 2 is `graph.routing.classifier`: an
Azure OpenAI call on the **economy runtime tier** (`MODEL_CHEAP`, declared statically per
Principle VI, no escalation), with a JSON-only response format, a 1.5 s timeout, and a strict
Pydantic model (`ClassifierOutput`, `extra="forbid"`) that rejects any kind or service outside the
allowed set. Every failure — unavailable, timeout, invalid, or a validation rejection — becomes
`Tier2Outcome` other than `DECIDED`, which routes to the same `CLARIFY` node the deterministic
tiers already use for "undecided". Tenant and guest identity are never read from the classifier's
output (`ClassifierOutput` has no such field at all, not merely an unused one) — FR-012's rule is
enforced by the type the model is constrained to return, not by a downstream filter that could be
forgotten.

## Consequences

**Made easier.** Most traffic — the deterministic tiers, calibrated to keep tier-2 share under 20%
(SC-005) — stays fully offline-testable and reproducible in CI. The eval gate (US5) can assert
routing accuracy without live credentials for the bulk of its labelled set. Adding a sixth request
kind or a sixth service later means widening one enumeration in one place; the classifier's prompt
is re-rendered from that enumeration on every call (Principle IV: the code, not the prompt,
defines what's legal), so a template edit can never quietly add a kind the code doesn't handle.

**Made harder.** Tier 1's precedence order (out-of-scope beats a service match; modify beats book;
lookup beats book) is now load-bearing for safety, not just accuracy — getting that order wrong
silently reintroduces the "flight with dinner on board" defect this feature exists to close. A
future service or kind addition has to be threaded through three places (the vocabulary data, the
kind-rule table, and the classifier's code-injected enumeration) rather than one.

**Foreclosed.** A future feature cannot fold tier 0/1 into the model call "for simplicity" without
reopening this ADR — Principle I's ambiguity-based-routing constraint applies to any redesign of
this router, not just this one. The classifier can never gain tool-calling ability (ADR-032's
existing prohibition on tool-calling prompts that see guest text) without a separate decision.

**Disclosed limitation.** A `modify`/cancel request routes to the existing `HUMAN_HANDOFF`, which
(per `_route_from_entry`'s existing rule) stops all further automated processing for the rest of
that conversation. This feature recognises and hands off modify/cancel requests; it does not soften
that lock-in, because performing the change itself is explicitly out of scope (spec.md's Out of
Scope section).
