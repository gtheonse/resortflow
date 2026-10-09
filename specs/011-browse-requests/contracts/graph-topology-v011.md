# Contract: Graph topology v011

Extends [graph-topology-v010.md](../../010-tiered-intent-routing/contracts/graph-topology-v010.md). Node names
are a persistence contract: **one node is added, none renamed or removed.** `GRAPH_VERSION` `"5.1"` → `"5.2"`.

## New node

| Node | Constant | Terminal? | Mutates? |
|---|---|---|---|
| `browse` | `graph.nodes.BROWSE` (added to `ALL_NODES`) | yes (→ `END`, or → `DETERMINISTIC`/`SUPERVISOR` for a browse+book pair) | **never** — no hold, no ledger write, no vendor mutation |

## `_route_from_intent_router` — additions

Evaluated in the existing order; only these branches are new/changed:

```text
routing is None or kind is None                    → CLARIFY            (unchanged)
kind == OUT_OF_SCOPE                               → DECLINE            (unchanged)
kind == SMALLTALK                                  → SMALLTALK          (unchanged)
kind == MODIFY                                     → MODIFY_REQUEST     (unchanged)
kind == LOOKUP                                     → BOOKING_LOOKUP     (unchanged)
kind == BROWSE and services == [GAMING]            → DETERMINISTIC      (NEW; the existing gated gaming graph)
kind == BROWSE                                     → BROWSE             (NEW)
… (book / faq branches unchanged)
```

The pre-existing `services == [GAMING]` shortcut sits after the `LOOKUP`/`FAQ`-without-services branches and
before the `BOOK`/`FAQ`-with-services ones today; the new gaming-only `browse` branch is placed **immediately
before** the generic `BROWSE` branch so a gaming browse always takes it and can never bypass
`require_gaming_clear`.

## `BROWSE` node behaviour

1. Read `routing` (kind, services, `browse_filters`, `window`); read `tenant_id`/`guest_id` from state only.
2. Resolve `window`: if `source == "needs_dates"`, look for `stay_period(booking_view(state))`; found →
   `source="stay_period"` with those dates; not found → reply asks for dates (no read performed).
3. Dispatch per service (all reads through `tools_factory[domain](tenant_id, guest_id)`):
   - no service → deterministic **overview** (calls `require_gaming_clear` fresh only to decide the gaming
     line; restricted/unavailable → omit it);
   - `dining` → `list_venues(cuisine, price_band, supports_dietary)`;
   - `entertainment` → `search_events()` (bounded pages) → window filter → drop sold-out/cancelled → sort → cap;
   - `wellness` → `list_services(category)`;
   - `stay` → `search_rooms(arrival, departure, adults)` (dates required; else ask);
   - several services → one section per service, in a fixed order.
4. A provider error for a service → that service's section is the "couldn't retrieve right now" line
   (FR-017); other sections still render. **Never** falls back to `retrieved`/KB text.
5. Compose the reply from `graph/replies.py` templates; if the message carried `secondary_kind == BOOK`,
   continue to `DETERMINISTIC`/`SUPERVISOR` with `secondary_services` exactly as `BOOKING_LOOKUP` does.
6. Call `_apply_policy_postflight` and `_apply_offer_agent` (the gaming self-exclusion gate that runs on
   every turn) before ending, like every other END-bound node.
7. Write `clarification_count: 0` from `INTENT_ROUTER` (not this node) when the decision is `BROWSE`.

## Structural guarantees (tested)

- The `BROWSE` node's module code references only read methods of the tool protocols: an allow-list test
  fails if `hold_*`, `confirm_*`, `purchase_*`, `cancel_*`, `release_*` or `join_waitlist` appear in it
  (Principle III / SC-009).
- No `graph/routing/*.py` module imports a tool or `openai` (existing test, still passing).
- `BROWSE` reads no `state["retrieved"]` (browse never uses KB text).

## Resume

A pre-011 checkpoint has no `browse_filters`/`window` on its `routing` value; both read `None`. A
conversation checkpointed at 5.1 resumes at 5.2 (minor). Tested in `test_state_amendment_011.py`.
