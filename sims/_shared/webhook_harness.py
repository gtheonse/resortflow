"""Lightweight webhook test harness (US5, T033), per
`specs/002-simulation-ecosystem/contracts/sim-control-plane.md`. Phase 1's own stand-in
for feature 002's eventual real webhook consumer (ADR-009) — not a production interface,
carries no auth, and is not part of ticketing-sim's frozen OpenAPI contract.

- ``POST /__sim__/webhook-sink`` — accepts any payload, records it, returns 204.
- ``GET /__sim__/webhook-sink/{order_id}`` — returns the recorded callback(s) for that
  order, or 404 if none arrived yet.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request

# In-memory, per-process — this harness is a test double for a test double; it does not
# need durability across a sim restart, unlike the sim's own SQLite state.
_received: dict[str, list[dict[str, Any]]] = defaultdict(list)


def build_webhook_sink_router() -> APIRouter:
    router = APIRouter()

    @router.post("/__sim__/webhook-sink", status_code=204)
    async def receive_webhook(request: Request) -> None:
        payload = await request.json()
        order_id = payload.get("order_id", "unknown")
        _received[order_id].append(payload)

    @router.get("/__sim__/webhook-sink/{order_id}")
    async def get_webhook_callbacks(order_id: str) -> dict:
        callbacks = _received.get(order_id)
        if not callbacks:
            raise HTTPException(status_code=404, detail=f"no callback recorded yet for order {order_id}")
        return {"order_id": order_id, "callbacks": callbacks}

    return router


def clear_received() -> None:
    """Test-only helper: clear recorded callbacks between test runs."""
    _received.clear()
