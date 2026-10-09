# ADR-020 — Resolving OFFER_AGENT and HUMAN_HANDOFF in the graph topology

**Status**: Accepted
**Date**: 2026-09-11
**Feature**: 005-multi-agent-orchestration

## Context

`graph/nodes.py`'s frozen node-name registry reserves `OFFER_AGENT` and `HUMAN_HANDOFF`
as string constants, but its own docstring flags both as "reserved but unresolved": the
`docs/ResortFlow-Enterprise-Multi-Agent-Plan.md` §4 agent-responsibility table lists both as
first-class agents, yet the §4 `stateDiagram-v2` draws neither as a distinct state. This
feature is the first to actually compile a graph against that registry, so the ambiguity
can no longer be deferred — some decision about whether each name becomes a real,
compiled node has to be made now, one way or the other.

The two names are not symmetric in what this feature actually needs. Offer Agent's
substance is "contextual upgrade/package suggestions from Snowflake feature store" — no
such feature store integration exists yet, and Constitution Principle IV requires the
self-exclusion check to run "before the Offer Agent runs at all," which depends on the
Policy Engine this feature explicitly defers to Phase 6. Human Handoff, by contrast, is
directly required by this feature's own exit criteria: User Story 5's bounded replan
cycle and Constitution Principle V's "every cyclic path... escalation to a human at the
bound" both need a real place in the graph to land.

## Options Considered

**1. Compile both as real, distinct nodes this feature, matching the roster table
exactly.** Most literal reading of the enterprise plan doc. Forces building Offer Agent's
upsell logic (or a stub) and its self-exclusion precondition before the Policy Engine
that gates it exists — either a half-built feature with no real policy check in front of
it (a Principle IV violation waiting to happen the moment it's wired live), or a stub so
empty it adds a compiled graph node for no behavior.

**2. Fold both into adjacent nodes — Offer Agent into `RESPOND`, Human Handoff into
`RECOVERY`.** Avoids building anything new, but Human Handoff genuinely needs to be a
distinct, addressable state: a conversation that has escalated is not "still in
recovery," it has stopped automated processing entirely (spec.md User Story 5,
Acceptance Scenario 2: "the system does not silently resume automated retrying"), and
folding it into `RECOVERY` makes that state indistinguishable from ongoing compensation.

**3. Compile `HUMAN_HANDOFF` as a real interrupt node now; leave `OFFER_AGENT`'s name
reserved in the registry but do not mount it as a node in this feature's compiled graph
at all.** Builds exactly what this feature's exit criteria require and nothing its
Principle IV precondition can't yet support.

## Decision

**Option 3.** `HUMAN_HANDOFF` becomes a real `interrupt()`-based node in this feature's
supervisor graph, reachable from: `RECOVERY` when compensation itself fails (Constitution
Principle III's dead-letter-and-escalate requirement), and from the bounded replan and
clarification cycles (`MAX_REPLAN_COUNT`, and the new `MAX_CLARIFICATION_COUNT` from
ADR-023) when their bound is reached. `OFFER_AGENT`'s constant stays in `graph/nodes.py`
untouched — removing it would be the actual breaking change — but it is not added as a
node in this feature's `StateGraph`. It remains reserved for whichever future feature
builds its substance, expected to be Phase 6 alongside the Policy Engine, since that is
the feature that also builds the self-exclusion precondition Constitution Principle IV
requires to sit in front of it.

## Consequences

**Made easier.** This feature's actual exit criterion — "replan loop terminates under
adversarial input" — has a real, testable landing node instead of an implied one.
Human Handoff's behavior (stop all automated processing, present a clear escalation
state) can be tested in isolation without any dependency on unbuilt Policy Engine or
Offer Agent substance.

**Made harder.** The compiled graph in this feature does not visually match the full §4
roster table one-to-one — a reader comparing the two needs this ADR to understand why,
which is exactly why it is written down rather than left as a silent omission.

**Foreclosed.** No future feature may wire `OFFER_AGENT` live without also having a real
Policy Engine self-exclusion gate in front of it — that ordering is now a documented
constraint, not an implicit assumption. `HUMAN_HANDOFF` may not be re-folded into
`RECOVERY` or any other node later without a new ADR, since other features (Phase 6's
human handoff for policy blocks, distress-signal routing) are expected to reuse this same
node.
