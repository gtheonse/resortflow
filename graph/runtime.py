"""Production graph wiring for the orchestrator service (`graph/api.py`,
feature 008.5 "Phase 7.5 Gap Closure", ADR-050): a real
`compile_supervisor_graph` app bound to a Postgres checkpointer opened once
at process start (so a `thread_id` survives a process restart) plus a
matching `HoldLedger`, or an in-process `MemorySaver`/`NullHoldLedger` pair
when `POSTGRES_URL` is unset.

This is the checkpointer/ledger construction half of what used to be
`ui/runtime.py`'s `GraphRuntime`/`build_runtime()` (feature 008's
verification console) -- moved here because it is a production concern
`graph/api.py` needs too, not a console-only one. **Deliberately does not
carry `ui/runtime.py`'s dev-only per-thread `X-Sim-Fault` injection
machinery** (`_thread_aware_tools_factory`, `_NextCallFaultRegistry`,
`fault_registry`) -- that stays a `ui/`-only concern used by the console's
fault-injection toolbar and is not something a production entrypoint should
expose. `ui/runtime.py` now wraps this module's `GraphRuntime`/`build_runtime`
rather than duplicating the checkpointer-fallback logic, and layers its own
fault-aware subclass on top -- see that module's docstring.
"""
from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from graph.ledger import HoldLedger, NullHoldLedger, PostgresHoldLedger
from graph.supervisor import DEFAULT_TOOLS_FACTORY, compile_supervisor_graph


class GraphRuntime:
    """Owns the one long-lived checkpointer connection and compiles a fresh
    graph app per call (compiling is cheap -- `StateGraph.compile`, no I/O --
    the same reasoning `ui/runtime.py`'s original `GraphRuntime` documented).
    Always compiles against the plain `DEFAULT_TOOLS_FACTORY` -- no per-thread
    fault awareness; `thread_id` is accepted only for interface parity with
    `ui/runtime.py`'s thread-aware subclass, which does use it."""

    def __init__(self, checkpointer: Any, ledger: HoldLedger) -> None:
        self.checkpointer = checkpointer
        self.ledger = ledger

    def app_for_thread(self, thread_id: str) -> Any:
        return compile_supervisor_graph(
            checkpointer=self.checkpointer,
            tools_factory=DEFAULT_TOOLS_FACTORY,
            ledger=self.ledger,
            trace=True,
        )


@asynccontextmanager
async def build_runtime() -> AsyncIterator[GraphRuntime]:
    postgres_url = os.environ.get("POSTGRES_URL")
    ledger: HoldLedger = PostgresHoldLedger() if postgres_url else NullHoldLedger()

    if postgres_url:
        from agents.dining.graph import postgres_checkpointer

        async with postgres_checkpointer() as checkpointer:
            yield GraphRuntime(checkpointer=checkpointer, ledger=ledger)
    else:
        # `checkpointer=None` makes every `CompiledStateGraph.aget_state()`
        # call raise "No checkpointer set" -- fine for a caller that never
        # reads state back, but both `graph/api.py`'s reconnect endpoint and
        # `ui/`'s console depend on it. `MemorySaver` keeps a process fully
        # usable without Postgres; only surviving an actual process restart
        # needs POSTGRES_URL.
        from langgraph.checkpoint.memory import MemorySaver

        yield GraphRuntime(checkpointer=MemorySaver(), ledger=ledger)
