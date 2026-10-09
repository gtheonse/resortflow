# Interface Contract: Entertainment MCP Tools

**Feature**: `005-multi-agent-orchestration` | Extends: `docs/contracts/mcp-tool-contract.md` (feature 001, frozen)

This document does not amend the frozen `mcp-tool-contract.md` standard — it applies that
standard's existing naming, idempotency, error-shape, and scope rules to the one domain
that has never had a server built against them. Everything here is a routine application
of an already-recorded decision (no ADR).

## Tool inventory

Mirrors `mcp/gateway/stay/server.py`'s pattern exactly. Response shapes are the Pydantic
models already built in `mcp/gateway/entertainment/models.py` (unchanged by this
feature).

| Tool name | Verb class | Mutating | Response model | Scope |
|---|---|---|---|---|
| `resortflow.entertainment.search_events` | read | no | `SearchEventsResponse` | `entertainment:read` |
| `resortflow.entertainment.get_event_detail` | read | no | `EventDetail` | `entertainment:read` |
| `resortflow.entertainment.check_inventory` | read | no | `InventoryResponse` | `entertainment:read` |
| `resortflow.entertainment.hold_seats` | mutating | yes | `SeatHold` | `entertainment:mutate` |
| `resortflow.entertainment.release_seat_hold` | mutating | yes | *(empty response)* | `entertainment:mutate` |
| `resortflow.entertainment.purchase_tickets` | mutating | yes | `PurchaseResponse` | `entertainment:mutate` |
| `resortflow.entertainment.get_order_status` | read | no | `OrderStatus` | `entertainment:read` |
| `resortflow.entertainment.request_refund` | mutating | yes | `RefundResponse` | `entertainment:mutate` |

All names satisfy the frozen naming regex `^resortflow\.[a-z]+\.[a-z]+_[a-z_]+$`.

## Idempotency

`hold_seats`, `release_seat_hold`, `purchase_tickets`, and `request_refund` are the
mutating tools; each requires `idempotency_key`, generated per-domain via the same
`idempotency_key(session_id, proposal_id, attempt)` shape already proven in
`agents/dining/tools.py`, instantiated in `agents/entertainment/tools.py` (new, this
feature — not a shared cross-domain function, per research.md's "Idempotency key
generation generalizes the existing formula, unchanged").

## `EntertainmentTools` Protocol (mirrors `DiningTools`)

```python
class EntertainmentTools(Protocol):
    async def search_events(self, *, classification=None, date=None, tenant_venue=None) -> SearchEventsResponse: ...
    async def get_event_detail(self, *, event_id: str) -> EventDetail: ...
    async def check_inventory(self, *, event_id: str) -> InventoryResponse: ...
    async def hold_seats(self, *, event_id, section_id, quantity, idempotency_key, ttl_seconds=None) -> SeatHold: ...
    async def release_seat_hold(self, *, hold_id, idempotency_key) -> EmptyResponse: ...
    async def purchase_tickets(self, *, hold_id, guest, idempotency_key) -> PurchaseResponse: ...
    async def get_order_status(self, *, hold_id=None, order_id=None) -> OrderStatus: ...
    async def request_refund(self, *, order_id, idempotency_key) -> RefundResponse: ...
```

Real implementation (`McpEntertainmentTools`) is a thin adapter over the shared
`graph/mcp_client.py` wire-transport client (ADR-021) — not an in-process call, unlike
dining's Phase 4 implementation, which this feature retrofits onto the same client.

## Transport

Per ADR-021: real MCP wire transport via the shared `graph/mcp_client.py` client, talking
to `mcp/gateway/entertainment` mounted at `/entertainment` on the existing gateway
process (`mcp/gateway/main.py`, ADR-011). This is the first entertainment server to exist
at all — it is mounted alongside the four already-running domains, not replacing an
in-process shortcut (that retrofit applies to dining only).

## What this contract does not cover

Vendor-side event/inventory data shape (owned by `docs/contracts/openapi/` and the
entertainment simulator, Track A — unchanged by this feature). Retry/backoff/circuit
breaker policy (still deferred, same as the original `mcp-tool-contract.md` scoped it
out — now a natural home in `graph/mcp_client.py` per ADR-021, but not built by this
feature beyond what dining's existing behavior already required).
