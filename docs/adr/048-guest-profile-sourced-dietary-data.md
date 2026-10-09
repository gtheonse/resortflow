# ADR-048 — Sourcing dietary/allergy data from a stored guest profile

**Status**: Proposed (not undertaken now — recorded for a future `/speckit-specify`
pass, not a decision made today)
**Date**: 2026-09-14
**Feature**: none yet — analysis only, prompted by manual testing of the feature 008
verification console

## Context

While testing the verification console, the question came up: if the system already
had all of a guest's data on file, would the guest still need to type allergy/dietary
information into their message every time, or could the system skip that? This ADR
records the answer, traced directly against the code rather than assumed, so the
analysis isn't lost outside this conversation.

**Verified: dietary data today has exactly one source — free text, parsed fresh every
turn.** `agents/dining/nodes/intent_parser.py`'s `_latest_human_text` feeds both
`RuleBasedIntentExtractor._extract_dietary` (a keyword/regex table) and
`AzureIntentExtractor.extract` (an LLM call) — `state["guest_id"]` is never read in
this file for any purpose. `agents/dining/nodes/venue_discovery.py:56-66` copies that
list verbatim onto every candidate `Proposal.dietary`.

**Verified: no guest profile store for dietary/allergy data exists anywhere in the
repo.** `sims/_shared/guest_seed.py:40-54`'s `GuestProfile` (the only real per-guest
record — used to seed loyalty/gaming/PMS data) has fields for tier, points, comp
dollars, self-exclusion, offer/gaming permissions — no dietary or allergy field at
all. No MCP tool, sim endpoint, or `content/`/`rag/` store holds per-guest structured
dietary preferences; `content/`/`rag/` only hold venue-side menu allergen labeling
(what a dish contains), not guest-side stored preferences.

So the answer to "would we need code updates" is **yes**, for two independent
reasons: there is nowhere to read profile-sourced dietary data from today, and there
is no code path anywhere that would look it up even if there were.

## What would *not* need to change

Verified directly from `agents/dining/nodes/allergen_check.py:75-125`, the existing
enforcement logic is provenance-agnostic by construction:

- The **menu-conflict check** (lines 93-113) iterates `proposal.dietary` directly,
  with no reference to conversation text — a profile-sourced restriction the guest
  never mentions this turn would still be checked against the venue's menu and still
  block on a severe/moderate conflict.
- The **fidelity check** (`policy/dietary.py:18-30`, ADR-029) is one-directional:
  every restriction *mentioned in conversation* must appear in `proposal.dietary`. It
  has no requirement that `proposal.dietary` be limited to what was said — a
  profile-only entry simply never triggers it.

Both checks satisfy AGENTS.md's hard rule ("never carry a dietary restriction only in
free text — structured enum + severity, asserted before commit") regardless of where
the structured data originated. **`allergen_check.py`, `venue_discovery.py`, and
`policy/dietary.py` would need zero changes** under the shape below.

## Options Considered (for the future spec, not decided here)

**1. New PMS/stay-domain MCP tool** — a `get_guest_profile(guest_id)` tool in
`mcp/gateway/stay/`, mirroring `agents/gaming/tools.py:79`'s `get_loyalty_status`
pattern (typed MCP lookup by guest_id, the closest existing precedent for "one
domain owns a per-guest structured record"), merged into `intent_parser.py`'s
existing dietary extraction (union of profile + freshly-mentioned text, deduped by
restriction). Advantage: reuses an established pattern, keeps `allergen_check.py`
untouched, keeps `venue_discovery.py` ignorant of provenance (as it already is).
Disadvantage: pulls a cross-domain (stay) dependency into the dining subgraph — a new
shape, since every other cross-domain reuse in this repo (ADR-030, ADR-037) was
scoped within a single case-by-case justification, and ADR-037 specifically sets a
bar against adding new MCP surface without justifying it over reuse.

**2. A new, domain-independent "guest profile" concept**, not owned by stay or
gaming, that any domain could read. Advantage: dietary preference is arguably
guest-level, not stay-level or loyalty-level — this avoids awkwardly filing it under
PMS just because `Stay.GUEST_PROFILE_ID` happens to already exist nearby.
Disadvantage: a new domain/ownership boundary is a bigger structural decision than
extending an existing one, with no current precedent for a cross-cutting profile
service in this repo.

Neither is adopted here — see Decision.

## Decision

**Not undertaken now.** This ADR exists to record the analysis and the concrete open
questions a real implementation would need to resolve, for a future
`/speckit-specify` pass — not to commit to Option 1, Option 2, or any implementation
detail today.

If pursued later, the following would need explicit answers before implementation,
not left as implicit behavior:

1. **Conflict resolution.** If a stored profile says "peanut allergy" but the guest
   says "I can have peanuts now" in the current conversation, which wins? Today's
   fidelity check only guards one direction (conversation mentions must not be
   dropped) — it has no notion of a profile being *contradicted*. A safe default
   (profile-sourced restrictions can only be added to via chat, never removed via
   chat alone) is the likely answer, but that is a real policy decision.
2. **Severity tie-break.** If profile and this-turn text disagree on severity for the
   same restriction, which is authoritative? `policy/dietary.py` never compares
   severity, only presence — so whichever value the merge picks is what actually gets
   enforced, making the merge's tie-break rule a real safety decision, not an
   implementation detail.
3. **Profile-lookup failure mode.** Does a failed/timed-out profile fetch degrade to
   text-only, matching `AzureIntentExtractor`'s existing fail-open pattern
   (`intent_parser.py:270-281`)? That pattern is acceptable for intent parsing in
   general, but here a failure would silently lose safety data the guest was relying
   on being remembered — a materially different stakes profile than the existing
   fallback's.
4. **Data ownership** (Option 1 vs. Option 2 above) — whether guest dietary data
   belongs to PMS/stay by default proximity, or needs its own concept.

## Consequences

**Made easier, if this is pursued later**: the enforcement path
(`allergen_check.py`/`policy/dietary.py`) needs no changes, confirmed by this
analysis — a future implementer doesn't need to re-derive that safety property from
scratch.

**Made harder**: none — this ADR changes no code and forecloses no option.

**Foreclosed**: nothing. This is a Proposed record, not an Accepted decision.
