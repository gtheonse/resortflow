# Interface Contract: Supervisor Graph Topology v010

**Feature**: `010-tiered-intent-routing` | Extends `specs/005-multi-agent-orchestration/contracts/graph-topology-v005.md` additively.

## Node names: additions only (FR-027)

Added to `graph/nodes.py` and `ALL_NODES`. **No existing name changes.**

| Constant | Name | Purpose |
|---|---|---|
| `BOOKING_LOOKUP` | `booking_lookup` | answers `lookup` from this conversation's bookings (FR-014 to FR-016) |
| `MODIFY_REQUEST` | `modify_request` | acknowledges `modify`, identifies the booking, sets `escalate_reason="modify_request"` (FR-017) |
| `DECLINE` | `decline` | `out_of_scope` reply listing supported services, no knowledge-base text (FR-018) |
| `SMALLTALK` | `smalltalk` | brief greeting/acknowledgement reply, no retrieval, no model (FR-019) |

## Routing from `INTENT_ROUTER` (`_route_from_intent_router`, reads `state["routing"]` only)

```text
routing is None or routing.kind is None      -> CLARIFY
kind == out_of_scope                          -> DECLINE
kind == smalltalk                             -> SMALLTALK
kind == modify                                -> MODIFY_REQUEST
kind == lookup                                -> BOOKING_LOOKUP
kind == faq and services == []               -> KNOWLEDGE_QA
kind in {book, faq, lookup*} and services == [gaming]   -> DETERMINISTIC   (read-only gaming graph)
kind == book and len(services) == 1          -> DETERMINISTIC
kind == book and len(services) >= 2          -> SUPERVISOR
kind == faq and services != [] (non-gaming)  -> KNOWLEDGE_QA
```

`*` A `lookup` whose only service is gaming ("what's my loyalty tier") is a loyalty read, not a
booking lookup. `KNOWLEDGE_QA` is never a destination for a `book`, `modify` or `out_of_scope`
decision (SC-002, SC-009).

## New edges

```text
BOOKING_LOOKUP --(routing.secondary_kind == book, 1 svc)--> DETERMINISTIC
BOOKING_LOOKUP --(routing.secondary_kind == book, >=2 svc)--> SUPERVISOR
BOOKING_LOOKUP --(otherwise)--> END
MODIFY_REQUEST --> HUMAN_HANDOFF
DECLINE        --> END
SMALLTALK      --> END
```

For the mixed lookup + book case, `DETERMINISTIC` and `PLANNER` use
`routing.secondary_services`, not `routing.services`.

## Per-turn policy obligations (unchanged principle, new nodes)

`BOOKING_LOOKUP`, `DECLINE` and `SMALLTALK` end at `END` without passing `RESPOND`, so each
**must** call `_apply_policy_postflight` and `_apply_offer_agent` itself. This is the same pattern
as `KNOWLEDGE_QA` / `DETERMINISTIC` (feature 006 T052: the self-exclusion gate runs on every turn).
`MODIFY_REQUEST` reaches `HUMAN_HANDOFF`, which already stops automation. A structural test
asserts all three call both functions.

## Unchanged

`ENTRY → POLICY_PREFLIGHT → INTENT_ROUTER` ordering (distress screening still runs before any
routing), `CLARIFY → INTENT_ROUTER` loop and its bound, and the whole `SUPERVISOR` saga path.
