# Contract: `ResortFlowState` / model amendment 011

Extends [state-schema-amendment-010.md](../../010-tiered-intent-routing/contracts/state-schema-amendment-010.md).
Recorded as ADR-060.

## What changes

| Item | Change | Kind |
|---|---|---|
| `ResortFlowState` keys | **none** (stays 25; `tests/test_state_contract.py` frozen count unchanged) | — |
| `graph.models.RequestKind` | + `BROWSE = "browse"` | additive enum member |
| `graph.models.RoutingDecision` | + `browse_filters: BrowseFilters \| None = None`, `window: DateWindow \| None = None`; `secondary_kind` validator allows `BOOK` under `LOOKUP` **or** `BROWSE` | additive fields, one relaxed invariant |
| `graph.models.BrowseFilters`, `DateWindow` | new frozen `ResortFlowModel`s | new types |
| `graph.bookings.BookingRecord` | + `ends_at: datetime \| None = None` | additive field |
| `graph.nodes` | + `BROWSE = "browse"` in `ALL_NODES` (`tests/test_contract_registries.py` frozen list updated) | additive node name |
| `GRAPH_VERSION` | `"5.1"` → `"5.2"` | minor bump |
| `agents/dining/serde.py::_ALLOWED_MSGPACK_TYPES` | + `BrowseFilters`, `DateWindow` | required — a missing entry breaks resume [CRIT] |

## Why minor, not major

A checkpoint written at 5.1 contains a `RoutingDecision` without the two new fields; they default to `None`
on load, so it resumes and behaves exactly as before. Nothing an in-flight conversation depends on is removed
or reinterpreted. A major bump would release outstanding holds on the first post-upgrade turn (ADR-056's
reasoning, unchanged).

## Invariants added to `RoutingDecision`'s validator

- `browse_filters` and `window` must be `None` unless `kind == BROWSE`.
- `kind == BROWSE` with `Domain.GAMING` in `services` is valid only alone (`services == [GAMING]`) — a
  gaming-only browse; a mixed browse must not list gaming as an item service (the overview may mention it
  behind the gate, but that is a reply concern, not a decision field).
- `window.end >= window.start`; `window.source` in the four allowed literals.

## Tests (write first, must fail first)

`graph/tests/test_state_amendment_011.py`: a pre-011 (5.1) checkpoint fixture resumes and reads both fields
`None`; a populated decision round-trips through `EncryptedSerializer` and `plain_serde()` with no pickle
fallback; every invariant above holds and is rejected when violated; the frozen registries
(`tests/test_contract_registries.py`, `tests/test_state_contract.py`) are updated to the new node/version.
