"""Console-only additions layered on top of `graph/runtime.py`'s production
wiring (feature 008.5 gap closure): per-thread, next-call-only `X-Sim-Fault`
injection (`_NextCallFaultRegistry`, `_thread_aware_tools_factory`) used by
the verification console's fault-injection toolbar.

The Postgres-vs-`MemorySaver` checkpointer/ledger construction this module
used to own directly now lives in `graph/runtime.py` (a production concern
shared with `graph/api.py`) -- this module no longer duplicates that logic,
it wraps it.

This is deliberately **not** a bare `from graph.runtime import GraphRuntime,
build_runtime` re-export, even though that would satisfy every current
import site (`ui/app.py`, `ui/tests/test_runtime_and_receipts.py`): the
console's `GraphRuntime.app_for_thread` must keep compiling against
`_thread_aware_tools_factory(thread_id)`, not `graph.runtime.GraphRuntime`'s
plain `DEFAULT_TOOLS_FACTORY` -- re-exporting the base class verbatim would
silently disable the fault toolbar on every real (live-stack) run without
any test catching it (`ui/tests/` only exercises the registry's bookkeeping,
never a real end-to-end turn -- see that test file's own docstring). The
class name `GraphRuntime` and the function name `build_runtime` are kept
identical here so every existing import (`from ui.runtime import
GraphRuntime, build_runtime, ...`) still resolves -- it's just backed by a
console-specific subclass rather than the production one directly.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from graph import runtime as _base_runtime
from graph.models import Domain
from graph.supervisor import DEFAULT_TOOLS_FACTORY, compile_supervisor_graph

REAL_FAULT_VALUES = ("timeout", "error_500", "rate_limit", "hold_expired", "inventory_race", "webhook_delay", "schema_drift")
"""`sims/_shared/faults.py`'s `ALL_FAULT_VALUES`, copied rather than imported
so the console has no import-time dependency on `sims/` being on the path in
every deployment shape; kept as a tuple (ordered) for stable toolbar rendering."""


class _NextCallFaultRegistry:
    """`{thread_id: {domain_value: fault_value}}`. `pop`-based: a fault set
    for a thread+domain applies to exactly the next tool-client construction
    for that domain on that thread, then clears itself — real "next call
    only" semantics, which nothing server-side provides today (confirmed:
    `graph/mcp_client.py` binds `extra_headers` at client-construction time
    for that client's whole lifetime, so a fresh client is what "one call"
    has to mean here — the same trick `graph/demo.py` already uses at
    process scope, just re-scoped to one thread and one turn)."""

    def __init__(self) -> None:
        self._pending: dict[str, dict[str, str]] = {}

    def set(self, thread_id: str, domain: str, fault: str) -> None:
        self._pending.setdefault(thread_id, {})[domain] = fault

    def clear(self, thread_id: str, domain: str) -> None:
        self._pending.get(thread_id, {}).pop(domain, None)

    def peek(self, thread_id: str) -> dict[str, str]:
        return dict(self._pending.get(thread_id, {}))

    def pop_for(self, thread_id: str, domain: Domain) -> str | None:
        return self._pending.get(thread_id, {}).pop(domain.value, None)


fault_registry = _NextCallFaultRegistry()


def _construct_with_fault(domain: Domain, tenant_id: str, guest_id: str, fault: str) -> Any:
    extra_headers = {"X-Sim-Fault": fault}
    if domain is Domain.DINING:
        from agents.dining.tools import McpDiningTools

        return McpDiningTools(tenant_id=tenant_id, guest_id=guest_id, extra_headers=extra_headers)
    if domain is Domain.ENTERTAINMENT:
        from agents.entertainment.tools import McpEntertainmentTools

        return McpEntertainmentTools(tenant_id=tenant_id, guest_id=guest_id, extra_headers=extra_headers)
    if domain is Domain.WELLNESS:
        from agents.wellness.tools import McpWellnessTools

        return McpWellnessTools(tenant_id=tenant_id, guest_id=guest_id, extra_headers=extra_headers)
    if domain is Domain.STAY:
        from agents.stay.tools import McpStayTools

        return McpStayTools(tenant_id=tenant_id, guest_id=guest_id, extra_headers=extra_headers)
    from agents.gaming.tools import McpGamingTools

    return McpGamingTools(tenant_id=tenant_id, guest_id=guest_id, extra_headers=extra_headers)


def _thread_aware_tools_factory(thread_id: str) -> dict[Domain, Callable[..., Any]]:
    def factory_for(domain: Domain) -> Callable[..., Any]:
        def build(tenant_id: str, guest_id: str) -> Any:
            fault = fault_registry.pop_for(thread_id, domain)
            if fault is None:
                return DEFAULT_TOOLS_FACTORY[domain](tenant_id, guest_id)
            return _construct_with_fault(domain, tenant_id, guest_id, fault)

        return build

    return {domain: factory_for(domain) for domain in DEFAULT_TOOLS_FACTORY}


class GraphRuntime(_base_runtime.GraphRuntime):
    """Console-specific override of `graph.runtime.GraphRuntime`: compiles
    against `_thread_aware_tools_factory(thread_id)` instead of the plain
    `DEFAULT_TOOLS_FACTORY`, so the fault-injection toolbar's next-call-only
    `X-Sim-Fault` headers actually reach the tool clients a turn on this
    thread constructs. Reuses the base class's `checkpointer`/`ledger`
    fields and constructor unchanged."""

    def app_for_thread(self, thread_id: str) -> Any:
        return compile_supervisor_graph(
            checkpointer=self.checkpointer,
            tools_factory=_thread_aware_tools_factory(thread_id),
            ledger=self.ledger,
            trace=True,
        )


@asynccontextmanager
async def build_runtime() -> AsyncIterator[GraphRuntime]:
    async with _base_runtime.build_runtime() as base:
        yield GraphRuntime(checkpointer=base.checkpointer, ledger=base.ledger)
