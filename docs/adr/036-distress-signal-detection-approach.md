# ADR-036 — Distress-signal detection reuses the injection-screening technique; no model judgment

**Status**: Accepted
**Date**: 2026-09-12
**Feature**: 006-policy-guardrails-hitl

## Context

The constitution is explicit and absolute: "Any utterance suggesting gambling-related
distress MUST route to human handoff with responsible-gaming resources surfaced. No
model judgment participates in that route." FR-004/FR-018 add that an *ambiguous*
utterance (plausibly distress, plausibly casual interest) must also route to handoff
rather than requiring certainty first. `agents/gaming/nodes/intent_parser.py` already has
a deterministic keyword list for a related but distinct purpose (`_RG_KEYWORDS`,
routing to the `rg_status` *lookup* intent — an informational question, not a distress
signal) — this ADR is about a different, higher-stakes classification that must escalate
to a human, not merely answer a question. ADR-032 already chose a corpus-match plus
embedding-similarity approach for a structurally similar problem (screening text for a
category with no single correct pattern-match, where false negatives matter more than
false positives).

## Options Considered

**1. An LLM classifier** ("does this utterance indicate gambling distress?"). Rejected
outright: this is precisely what the constitution's "no model judgment participates in
that route" forbids — not a close call, a named prohibition.

**2. Keyword list only** (extending `_RG_KEYWORDS`'s existing pattern). Cheap, fully
consistent with the existing gaming intent-parser style. Rejected as the sole mechanism:
distress language is exactly the kind of content where phrasing varies more than a fixed
keyword list can cover ("I can't stop," "it's ruining things," "I need to talk to
someone about this") — FR-018's ambiguous-case-escalates requirement implies the
detector needs to catch near-misses, not just exact phrase matches.

**3. Keyword/phrase list plus embedding-similarity scoring against a maintained set of
example distress utterances** — the same two-layer deterministic technique ADR-032
already adopted for injection screening, applied to a different corpus and threshold.
No new dependency (`sentence-transformers` again), no model judgment anywhere in the
decision.

## Decision

**Option 3.** `policy/distress.py:check_distress_signal` uses the identical mechanism
shape as `policy/injection.py:screen_retrieved_content` — exact/fuzzy phrase match plus
embedding similarity against a maintained corpus of example distress utterances — with
its own threshold and corpus, tuned so that the *ambiguous band* (similarity above a
lower bound but below the confident-match bound) still resolves to `flagged=True` (or
`ambiguous=True`, routed identically), per FR-018's "treat ambiguous as requiring
handoff" rule. This is a routine application of ADR-032's already-justified technique to
a second corpus, not a new architectural decision about *how* to screen text
deterministically — that question was already answered once.

## Consequences

**Made easier.** No new detection technique to design, build, or justify separately;
`check_distress_signal` and `screen_retrieved_content` share test infrastructure and
tuning methodology (threshold selection, corpus-maintenance process).

**Made harder.** The two thresholds (injection-similarity, distress-similarity) are
tuned independently and could drift out of sync in how "sensitive" each screen is if not
reviewed together periodically — a minor, documented maintenance note rather than a
structural risk.

**Foreclosed.** No future feature may introduce an LLM-based distress classifier as a
"smarter" replacement without a constitution amendment — the "no model judgment"
constraint is explicit and absolute in the constitution itself, not a default this ADR
could unilaterally revisit.
