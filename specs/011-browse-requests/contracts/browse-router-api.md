# Contract: Browse recognition (tier 0/1) — `graph/routing`

Extends feature 010's [router-api.md](../../010-tiered-intent-routing/contracts/router-api.md). Pure functions,
no I/O, no model, no tool import (the existing structural test scans `graph/routing/*.py` and keeps passing).

## `classify_kind` — precedence (per clause)

```
out_of_scope > modify > lookup(phrases/words) > BROWSE > possessive-lookup > book(cue) > smalltalk > faq > bare-service book > undecided
```

Only the BROWSE slot and its neighbours are new. Existing rules for the other kinds are unchanged.

## Recognition rule

A clause is `browse` when it matches **(1) or (2)** and **none of the vetoes**:

1. **cue AND plural catalogue noun** — from `vocabulary.yaml` `kinds.browse`:
   - *enumeration cues* (words/phrases): `list`, `show me`, `what`, `which`, `what's on`, `whats on`,
     `what is on`, `what's available`, `whats available`, `what is available`, `any`, `browse`;
   - *catalogue nouns*, **plural or set-denoting forms only** (with the same typo tolerance as service
     words): `venues`, `restaurants`, `places to eat`, `events`, `shows`, `concerts`, `games`, `rooms`,
     `suites`, `treatments`, `services`, `options`, `experiences`, `activities`, `things to do`.
   Plural-only is deliberate: "what rooms are available" enumerates a set (browse) while "what's the room
   rate" or "what time does the show start" asks about one known item (FAQ, FR-004).
2. **plural catalogue noun + stay-period marker, no cue needed** — "concerts while I'm here", "shows during my
   stay": the marker (`my stay`, `during my stay`, `while I'm here`, …) already says the guest is asking what is
   available in their window (found while authoring the ground truth; the case stays, the rule grows).
3. **strong phrase alone** — `what do you offer`, `what can i do here`, `what is there to do`,
   `what's happening`, `whats happening`, `what's on`, `whats on`.

**Noun → service mapping** (`kinds.browse.catalogue_nouns` in `vocabulary.yaml`; decides `services` when the
message has no service word of its own, and resolves collisions with service vocabulary):

| Noun(s) | Service |
|---|---|
| `venues`, `restaurants`, `places to eat` | dining |
| `events`, `shows`, `concerts` | entertainment |
| `rooms`, `suites` | stay |
| `treatments` | wellness |
| `games` | **gaming** when a casino word (`casino`, `floor`, `table`, `tables`, `slots`, `poker`, `blackjack`) is in the clause; otherwise entertainment (`game` is an entertainment word — sports) |
| `options`, `experiences`, `activities`, `services`, `things to do` | none → the overview |

A service word already matched in the clause (e.g. `dining` in "dining venues") agrees with or overrides
the noun default; two services from different sources for one noun ("casino" + "games") resolve to the
mapping above, never to both. `list venues`/`which venues` therefore yields `[dining]`, not the overview.

**Vetoes** (any one present → not browse; the message falls to the existing rules):
- an **explicit book cue**: `book`, `reserve`/`reserving`/`reserved`, `get me`, `can i get`, `can you get
  me` (a message that also books is handled by the clause-pair path below, not as a single browse);
- an **attribute question about one item**: `what time`, `when do`, `when does`, `how much`, `how long`,
  `hours`, `price`, `cost`, `policy`, `dress code`.

`do you have`, `is there`, `are there`, `can i` are **not** cues (they remain FAQ, feature 010's ground
truth: "do you have connecting rooms?"). A clause that matches a **lookup phrase** (`what have i booked`,
`my itinerary`, `my booking`, `my reservation`, …) is decided as `lookup` first, so "list my bookings"
stays a lookup.

## Output

`RoutingDecision(kind=BROWSE, services, browse_filters, window, tier=KIND_RULES, confidence)`:

- `services`: the matched service(s); for a general request with no service noun ("what do you offer",
  "list the resort experiences") `services == []` — the node returns the overview.
- `browse_filters`: from `graph.routing.browse.extract_filters(text)` — vocabulary-driven, see below.
- `window`: from `graph.routing.browse.resolve_window(text, today, stay=None)`; the **node** (not the
  router) substitutes `source="stay_period"` from `booking_view` because the router has no state.
- confidence `0.9` exact / `0.75` typo-tolerant match, as other kinds.

## `graph.routing.browse` (new module)

```python
def extract_filters(text: str, vocab: Vocabulary = DEFAULT) -> BrowseFilters
def resolve_window(text: str, *, today: date, wants_stay_period: bool) -> DateWindow | None
def wants_stay_period(text: str) -> bool     # "my stay", "during my stay", "while I'm here", "my stay period"
def detect_other_property(text: str, tenant_id: str) -> str | None   # see data-model.md `TenantProfile`
PROPERTY_NAMES: dict[str, str]               # tenant_id -> display name, from TenantProfile
```

`detect_other_property` is called by the `BROWSE` node (which has the verified `tenant_id`), not by the router
(which has only text), so the property statement can never influence routing or the catalogue read.

`resolve_window` uses `temporal.parse_date_range`; with `wants_stay_period` true and no explicit dates it
returns `DateWindow(source="needs_dates")` (the node upgrades it to `stay_period` if a stay exists).

## `graph.routing.temporal` additions

```python
def parse_date_range(text: str, *, today: date, start_offset_days: int = 0) -> tuple[date, date] | None
def parse_stay_length(text: str) -> int | None        # nights; "next 5 days"/"for 5 nights"/"5-night"/"a week"
```

Rules: `next N days` → `[today+offset, today+offset+N-1]` (events offset 0, stays offset 1; stay length N
nights); `for N nights` → N nights; `this weekend` → the coming Saturday–Sunday (today if already Sat/Sun);
`from X to Y` / `X to Y` / `between X and Y` → the two parsed dates, year-less resolved to the next
occurrence; `tonight`/`tomorrow`/single date → a one-day window. Returns `None` when nothing matches; never
raises; never returns `end < start`.

## Carry-over (`_try_carry_over`, FR-005a)

New branch, checked before the existing ones: `previous.kind == BROWSE` AND the message has a narrowing cue
(`only`, `just`, `cheaper`, `under`, `what about`, `how about`, `instead`) AND a filter token or a date
phrase → `BROWSE`, `carried_over=True`, previous services, filters merged (new value wins per field),
window replaced only if a new date phrase was given. Also: `previous.window.source == "needs_dates"` and the
message is (or contains) a date range/length → `BROWSE` with that window. One turn only: any intervening
non-browse turn clears eligibility (the `PreviousTurn` is rebuilt every turn from the last decision).

## Clause pair

The FR-020 pair table gains `{BROWSE, BOOK}` (primary `BROWSE`, `secondary_kind=BOOK`,
`secondary_services` from the book clause), mirroring `{LOOKUP, BOOK}`. Bare-"and" mixes are unchanged
(clarify).

## Failure/limits

No new failure mode: recognition is total. An unrecognised phrasing that *looks* like browse but fails the
cue+noun rule falls through to the existing rules and, if undecided, to tier 2 (which may now return
`browse`) and finally `CLARIFY`.
