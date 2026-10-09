# ADR-059 — Browse requests: a deterministic, read-only kind answered from the providers

**Status**: Accepted (2026-09-30, after live verification of the quickstart against the compose stack)
**Date**: 2026-09-30
**Feature**: 011-browse-requests

## Context

Feature 010 gave the assistant six ways to understand a message (book, lookup, modify, faq,
out_of_scope, smalltalk) and no way to understand "what is available?". Live testing found the same gap three
ways: "what are the dining venues available in nevada" was read as a property question and answered with an
unrelated allergen-policy passage from the knowledge base; "list the resort experiences" (typed in reply to the
assistant's own "which experience would you like?") was declined as out of scope; "what events are happening in
my stay period" was read as a booking-status lookup because `my` + `stay` looks like one. The catalogue of
venues, events, treatments and rooms lives with the providers each service already books through; nothing in
the graph asked them outside a booking.

The decision here is **how a browse should be recognised and answered** — not whether the assistant should
help a guest choose before booking, which the spec settles.

## Options Considered

**1. Answer browse from the knowledge base (extend `KNOWLEDGE_QA`).** Add venue and event descriptions to the
corpus and let retrieval answer. Rejected: the corpus is static prose and would drift from the providers' real
inventory (events change daily); a relevance floor can refuse off-topic questions but cannot make a
related-but-wrong passage right (the allergen answer cleared it); and it blurs the "documents only" boundary that
keeps the injection-screening rule simple (ADR-038).

**2. Let a language model decide and compose.** A retrieval-augmented or tool-using model could answer
"what's on this weekend" flexibly. Rejected: it violates Principle I for the ~all traffic that is a plain
enumeration, adds cost and latency to the most common pre-booking question, and makes "never states a venue is
safe for an allergy" a prompt request rather than a code guarantee (Principle IV). Feature 010's live tier-2
probes also showed near-identical phrasings flipping between `faq`, `undecided` and a timeout.

**3. A deterministic `browse` kind with a read-only node that calls the existing tools and formats with code
templates (chosen).** Recognition is an enumeration cue plus a plural catalogue noun (or a strong standalone
phrase), placed above the possessive-lookup rule and FAQ in precedence, with vetoes for explicit booking verbs
and attribute questions about one known item. The node reads through the same MCP tool protocols each service
books through, bounds the list, and states its assumptions.

## Decision

Adopt option 3.

- **Recognition** (`contracts/browse-router-api.md`): browse needs a cue AND a plural catalogue noun, a plural
  noun plus a stay-period marker, or a strong phrase. Singular and attribute forms ("what time is checkout",
  "what's the room rate") stay property questions; "do you have connecting rooms" stays FAQ. Precedence is
  `out_of_scope > modify > lookup-phrases > browse > possessive-lookup > book > smalltalk > faq`.
- **Answering**: one `BROWSE` node (`graph/browse_node.py` composer, thin wrapper in `graph/supervisor.py`) reads
  `list_venues` / `search_events` / `list_services` / `search_rooms` through `tools_factory(tenant_id, guest_id)`
  — tenant from the verified token, never message text. Text comes from `graph/replies.py` templates; no model
  and no knowledge-base text can enter a browse reply. Lists cap at 10 with an explicit "and N more".
- **Read-only** (Principle III): a browse writes no hold, proposal, confirmation or ledger row. A structural test
  over the node's source and a behavioural test with recording fakes enforce it.
- **Gaming** (Principle IV): a gaming-only browse goes to the existing gated gaming graph
  (`require_gaming_clear` + mandated message live there once); the general overview calls the same gate fresh
  on the turn solely to decide whether to show its casino line, failing closed.
- **Failure**: a provider error degrades that section to "couldn't retrieve right now"; it never invents items
  and never falls back to a knowledge-base passage as if it were the list.
- **Tier 2** may now return `browse` and is told enumeration requests are never out of scope, but the
  deterministic cues mean it is reached only for the genuinely ambiguous remainder.

## Consequences

- Guests get a real list, from the same inventory they will book, before committing. Catalogue questions stop
  producing unrelated policy text or declines.
- Every browse phrasing that the cue rules cover works with the language-model tier down (SC-005).
- Recognition is heuristic vocabulary and needs upkeep, so the labelled set gained 144 cases, including
  near-misses that must NOT route to browse, and both the `dev` and `holdout` splits are scored from the first
  run (feature 010 found an unscored `dev` split hid a 36% failure rate).
- Precedence is delicate: browse now sits above two rules that used to catch these messages. The near-miss
  cases and the veto lists are the guard, not the ordering alone.
- `search_events` takes a single date, not a range, so event browse is one fetch filtered locally; a real
  vendor's paging is not surfaced by the tool, so a truncated calendar is disclosed rather than enumerated.
- Not solved here: bare-"and" mixed messages, past-tense narrative vs request, recommendations, cross-property
  browse (all recorded in research R-12).

## Amendments found by live verification

- **Booking hand-off (SC-006).** After a browse, "book Sakura Sushi Bar for 2 tomorrow at 7pm" named a venue, not a
  service, and reached the generic clarifying question. A booking request that carries its own book cue and names no
  service now inherits the **previous browse's bookable services** (never gaming; one turn only, like every carry-over;
  the message's own service always wins). The dining flow also resolves a venue **named in the message** against the
  tenant's own venue list (whole-word match; one unfiltered read when a cuisine word inside the name, such as "Sushi",
  filtered it out), so a named venue is proposed instead of the first one listed.
- **Stay ranges.** The stay intent parser now reads an explicit "oct 5 to oct 8" as arrival plus three nights; it used
  to read only the arrival and booked one night.
- **Not solved:** "book stay for next 5 days" still defaults the arrival to tomorrow (existing `room_discovery`
  behaviour, behind the approval gate) instead of asking for it.
