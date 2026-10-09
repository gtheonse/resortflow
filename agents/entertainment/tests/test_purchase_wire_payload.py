"""Regression: `McpEntertainmentTools.purchase_tickets` must send what the gateway accepts.

The agent-side interface takes `guest`; the gateway's `PurchaseTicketsInput`
(`mcp/gateway/entertainment/server.py`, `extra="forbid"`) takes `hold_id`,
`payment_token` and `delivery` and nothing else, matching the frozen vendor contract.
The adapter used to forward `guest` verbatim, so the gateway rejected every purchase
("rejected arguments: ['input.guest', 'input.payment_token']") while the fake-based
tests, which implement the agent-side signature, stayed green.
"""

from __future__ import annotations

from typing import Any

import pytest

from agents.entertainment.tools import (
    DEFAULT_DELIVERY,
    PLACEHOLDER_PAYMENT_TOKEN,
    McpEntertainmentTools,
)


class _RecordingClient:
    """Stands in for `graph.mcp_client.McpClient`: records the call, answers like the gateway."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append((tool_name, kwargs))
        return {"order_id": "order-1", "status": "PENDING", "expected_resolution_seconds": 5}


def _adapter() -> tuple[McpEntertainmentTools, _RecordingClient]:
    adapter = McpEntertainmentTools(tenant_id="MGV", guest_id="guest-mgv-001")
    recorder = _RecordingClient()
    adapter._client = recorder  # type: ignore[assignment]
    return adapter, recorder


@pytest.mark.asyncio
async def test_purchase_sends_exactly_the_fields_the_gateway_accepts() -> None:
    adapter, recorder = _adapter()

    result = await adapter.purchase_tickets(
        hold_id="hold-1", guest={"guest_id": "guest-mgv-001"}, idempotency_key="idem-1"
    )

    assert result.order_id == "order-1"
    [(tool, sent)] = recorder.calls
    assert tool == "purchase_tickets"
    # `idempotency_key` travels outside the tool's `input` (McpClient pops it), the rest is the input.
    assert sent == {
        "hold_id": "hold-1",
        "payment_token": PLACEHOLDER_PAYMENT_TOKEN,
        "delivery": DEFAULT_DELIVERY,
        "idempotency_key": "idem-1",
    }
    assert "guest" not in sent


@pytest.mark.asyncio
async def test_purchase_payload_validates_against_the_real_gateway_model() -> None:
    from gateway.entertainment.server import PurchaseTicketsInput

    adapter, recorder = _adapter()
    await adapter.purchase_tickets(hold_id="hold-1", guest={"guest_id": "g"}, idempotency_key="idem-1")

    [(_, sent)] = recorder.calls
    tool_input = {k: v for k, v in sent.items() if k != "idempotency_key"}
    # `extra="forbid"` plus required fields: raises if the adapter drifts from the gateway again.
    validated = PurchaseTicketsInput.model_validate(tool_input)
    assert validated.hold_id == "hold-1"
    assert validated.payment_token == PLACEHOLDER_PAYMENT_TOKEN
