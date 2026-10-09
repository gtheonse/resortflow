# Contract: Browse reply templates

Every string below is a code constant or a format template in `graph/replies.py`. No model text, no
knowledge-base text can enter a browse reply (FR-006/FR-017). English, brief. Lists are capped at **10**.

## General shape

```
<intro line stating what/when/where>
<up to 10 item lines>
<"and N more — say a <filter> to narrow." when more exist>
<next-step line>
```

## Per service

**Dining** — intro `Here are the restaurants at {property_name}:`; item `{name} — {cuisine}, {price_band}`;
next step `Want me to book one? Tell me the venue, party size, date and time.`
With a dietary filter add, once, before the list: `You state your dietary restriction when you book, where it
is recorded and passed to the kitchen — I can't say a venue is safe for it from a list.` (FR-009; a code
constant; deliberately avoids the words "confirmed"/"booked" so the forbidden-content check below stays exact).

**Events** — intro `Events {window_phrase}:` where `window_phrase` is `from {start} to {end}` and, when the
window was defaulted or derived, ends with ` (I used the next 7 days)` / ` (your stay: {arrival}–{departure})`;
item `{name} — {kind}, {weekday} {date} at {time}, from ${min_price}`; next step `Want tickets? Tell me the
event and how many.` Sold-out/cancelled events are omitted.

**Wellness** — intro `Spa and wellness services:`; item `{name} — {category}, {duration} min, ${price}`; next
step `Want to book one? Tell me the service, date and time.`

**Rooms** — intro `Rooms available {arrival} to {departure} ({nights} nights, {adults} adults):`; when adults
were assumed add ` (I assumed 2 adults)`; item `{room_description} — from ${nightly_rate}/night`; next step
`Want me to hold one? Tell me which.`

**Overview** (no service; never a decline) —
```
Here's what I can arrange:
• Dining — restaurants at {property_name}. Try "what restaurants do you have".
• Shows and events — try "what's on this weekend".
• Spa and wellness — try "what spa treatments do you have".
• A room — try "what rooms are available Oct 5 to Oct 8".
• Casino games and loyalty — try "what casino games do you have" or "what's my loyalty tier".   [gaming line only when cleared, FR-015a]
What would you like to look at?
```
For a restricted/unavailable guest the fifth bullet is `• Loyalty — try "what's my loyalty tier".` (no casino text).

## Empty, error and clarifying replies

- Empty: `I couldn't find any {things} {qualifier}. {widen_hint}` where `widen_hint` names what to relax (a
  cuisine, price band, category, or the dates); never invents an item.
- Provider unavailable: `I couldn't retrieve the {things} list right now — please try again in a moment.`
  (per service section).
- Needs dates (rooms, or "my stay" with no stay): `Which dates? Give me an arrival and departure, or how many
  nights.` / `I don't see a stay on your booking yet — which dates should I look at?`
- Other-property mention: prefix `I can show {property_name}'s options only. ` (FR-007).
- Clarify question (feature 010's, updated): `What would you like to arrange — dining, a show or event, spa
  and wellness, or a room (stay)? And for which date(s)?`

## Forbidden content (tested)

A browse reply never contains: "confirmed", "booked", "held", "all set" (nothing was booked); never a venue
described as safe/suitable for an allergy; never text from `state["retrieved"]`; never another property's
name; never a gaming line for a guest who is not cleared.
