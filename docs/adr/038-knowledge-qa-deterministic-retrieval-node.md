# ADR-038 — `KNOWLEDGE_QA`: a deterministic, screened retrieval node for general property-knowledge questions

**Status**: Accepted
**Date**: 2026-09-13
**Feature**: 006-policy-guardrails-hitl (convergence follow-up, T054)

## Context

`/speckit-converge`, run after this feature's initial implementation, found that
FR-007/FR-008/FR-009's own named scenario — "a property knowledge document... contains
text designed to look like an instruction... the system answers the guest's question
using the document's legitimate content" — had no code path to exercise it end-to-end.
The only real retrieval call site anywhere in `agents/`/`graph/` was
`agents/dining/menu.py`, used exclusively for allergen keyword-matching and never fed to
a guest-facing generation context. `policy.injection.screen_retrieved_content` (ADR-032)
was implemented and tested in isolation, but had nowhere real to prove itself against the
scenario that motivated it. `rag.retriever.HybridRetriever` (feature 002) and the
per-tenant content corpora (`content/corpus/{mgv,mhb}/*.yaml`) already existed, unused
for this purpose — the retrieval infrastructure was there; the guest-facing node that
consumes it, screened, was not.

## Options Considered

**1. Build a full LLM-composed answer node** (retrieve, screen, then have an LLM
generate a natural-language answer from the clean chunks). Most natural-feeling guest
experience. Rejected for this pass: it would be the first LLM-invoking node this project
adds outside the ones already declared and tiered (planner, recovery, groundedness
judge), requiring its own Constitution Principle VI tier declaration, prompt design, and
groundedness/hallucination test coverage — the enterprise plan's own seven-layer table
lists "groundedness" as a distinct enforcement layer this would need to satisfy. That is
a feature-sized undertaking in its own right, not a small addition, and disproportionate
to what this convergence pass should absorb.

**2. Do nothing — leave FR-007's scenario as a documented, deferred gap.** Consistent
with the original implementation's disclosed limitation. Rejected once asked to close
convergence findings: the gap is closable at a scope this pass CAN absorb (option 3),
and leaving `screen_retrieved_content` provably untested against its own motivating
scenario is a weaker position than necessary.

**3. A deterministic retrieval node: retrieve via the existing `HybridRetriever`, screen
every chunk through `screen_retrieved_content`, and compose the answer by concatenating
the clean chunks' content verbatim — no LLM call anywhere in the node.** Matches
Constitution Principle I directly: "LLMs only where intent is unbounded... deterministic
paths stay first-class." A general property-knowledge question ("what time does the pool
close," "is there a resort fee") has a bounded, factual answer already sitting in the
content corpus — composing it from retrieved text is exactly the kind of "arithmetic, not
judgment" case this principle reserves for non-agentic code, the same reasoning already
applied to the conflict checker (feature 005) and every check this feature itself adds
(ADR-028).

## Decision

**Option 3.** `make_knowledge_qa_node` (`graph/supervisor.py`), a new node,
`KNOWLEDGE_QA` (`graph/nodes.py` — an additive node-registry entry, not a rename of
anything reserved). Reached when `IntentRouter` detects zero booking-domain keywords
(previously routed straight to `CLARIFY`): retrieves up to 3 chunks via an injectable
`retriever_factory` (defaults to `HybridRetriever`, injectable the same way every
domain's own `tools`/`menu_query` parameter already is, so tests don't need a live
Qdrant), screens them, and answers by joining the clean chunks' content. A `suspect`
chunk is silently excluded and recorded as a `PolicyFlag(rule="injection_detected",
disposition="blocked")` — the same audit pattern every other policy block in this feature
uses. When nothing usable comes back (empty retrieval, all-suspect, or the retriever
itself failing — e.g. Qdrant unreachable, degrading gracefully like
`agents/dining/menu.py` already does), the node falls through to the existing `CLARIFY`
node rather than inventing a second "I don't know" mechanism — a genuine FAQ miss is
exactly the ambiguous case `CLARIFY` (ADR-024) already exists for.

`_route_from_knowledge_qa` needs no new `ResortFlowState` key to decide CLARIFY vs. done:
whether this turn actually answered is already visible in whether the *last* message is
the `AIMessage` the node would have just appended (`add_messages` guarantees a
just-appended message sorts last). On success, the node routes straight to `END`, not
`RESPOND` — the same reasoning T052's fix applied to `DETERMINISTIC`: `RESPOND`'s
"nothing was booked for this request" summary would incorrectly stomp a successful FAQ
answer, since an FAQ turn has zero confirmations by construction. `_apply_policy_postflight`/
`_apply_offer_agent` (the same functions T052 extracted) are called directly from within
the node before returning, so the self-exclusion gate before any offer is considered
still applies "on every turn, regardless of topic" (Constitution Principle IV) — even a
pure FAQ turn.

## Consequences

**Made easier.** `screen_retrieved_content`'s corpus sweep (T033) and structural
tool-call-isolation test (T032) now have a genuine, real consumer to point to, not just
an isolated unit-tested function. Every future domain that wants a retrieval-backed
answer has a working, tested pattern to extend rather than inventing its own.

**Made harder.** The answer quality is bounded by "concatenate matching chunks verbatim"
— no paraphrasing, no synthesis across multiple chunks into one coherent sentence. This
is a deliberate, disclosed trade-off (Option 3 vs. Option 1), not an oversight.

**Foreclosed.** No future feature may add an LLM call to `KNOWLEDGE_QA` without revisiting
this ADR and adding the Constitution Principle VI tier declaration, prompt design, and
groundedness test coverage Option 1 was rejected for skipping here — extending this node
into a generative one is a real, separate feature-sized decision, not a small follow-up.
