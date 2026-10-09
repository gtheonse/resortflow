"""T005 — thin wrapper over `observability.telemetry` (ADR-041).

Was a standalone Langfuse-specific tracer (T056); now delegates all span
attribute-setting to the shared module, which is the only place any
attribute name may be defined (constitution, OBSERVABILITY IS NOT OPTIONAL).
Public API (`traced_node`) is unchanged — `agents/dining/graph.py` needs no
edit.

ADR-051: `traced_node` already has `state` in scope (it's `_summarize_state`'s
own source), so it threads `state.get("session_id")` through to
`start_agent_span` the same way `observability.telemetry.wrap_node` now does
for every other domain.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

from observability.telemetry import set_payload, start_agent_span


def _summarize_state(state: dict) -> dict:
    """A bounded, JSON-friendly projection of `DiningState` for span I/O —
    never the raw state (messages carry full conversation text; proposals/
    holds carry PII/HEALTH fields)."""
    return {
        "tenant_id": state.get("tenant_id"),
        "session_id": state.get("session_id"),
        "proposal_count": len(state.get("proposals") or []),
        "hold_count": len(state.get("holds") or []),
        "awaiting_approval": state.get("awaiting_approval"),
    }


def traced_node(node_name: str, fn: Callable) -> Callable:
    """Wrap a dining-subgraph node function (sync or async) so every
    invocation produces a span named `node_name` via
    `observability.telemetry.start_agent_span`."""
    is_async = inspect.iscoroutinefunction(fn)

    if is_async:

        async def async_wrapper(state):
            with start_agent_span(
                node_name, node=node_name, domain="dining", session_id=state.get("session_id")
            ) as span:
                set_payload(span, "input", str(_summarize_state(state)))
                result = await fn(state)
                set_payload(span, "output", str(_summarize_state({**state, **result})))
                return result

        return async_wrapper

    def sync_wrapper(state):
        with start_agent_span(
            node_name, node=node_name, domain="dining", session_id=state.get("session_id")
        ) as span:
            set_payload(span, "input", str(_summarize_state(state)))
            result = fn(state)
            set_payload(span, "output", str(_summarize_state({**state, **result})))
            return result

    return sync_wrapper
