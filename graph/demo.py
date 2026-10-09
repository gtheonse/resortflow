"""T037 — CLI entry point implementing quickstart.md's `python -m graph.demo`
scenarios. Requires a live stack (`make up && make seed`) — this is a real
end-to-end runner against the actual gateway/sims/Postgres, not a fakes-backed
harness (those live in `agents/tests/`).

Scenario 1 (three-domain success) is the default; `--inject-failure`,
`--force-unavailable`, and a free-form `--text` cover Scenarios 2-5 per
quickstart.md — this module does not hardcode scenario numbers, it exposes
the primitives quickstart.md's scenarios compose from.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import selectors
import sys
from collections.abc import Callable
from typing import Any

from langgraph.types import Command

from graph.ledger import PostgresHoldLedger
from graph.models import Domain
from graph.supervisor import DEFAULT_TOOLS_FACTORY, compile_supervisor_graph, new_session_state

DEFAULT_GUEST_BY_TENANT = {
    "MGV": "guest-mgv-001",
    "MHB": "guest-mhb-001",
}
"""Deterministic seeded guest ids (`sims/_shared/guest_seed.py`)."""


_FORCE_UNAVAILABLE_FAULT = "inventory_race"
"""`sims/_shared/faults.py`'s closest fit for "nothing is ever available":
every hold attempt loses the race — `--force-unavailable` doesn't need its
own fault value, this one already produces the right guest-visible outcome
(no proposal ever survives to a hold)."""


def _inject_failure_tools_factory(
    inject_failure: list[str], force_unavailable: list[str]
) -> dict[Domain, Callable[..., Any]]:
    """Wraps `DEFAULT_TOOLS_FACTORY` so `--inject-failure domain:tool` and
    `--force-unavailable domain` reach the real vendor sims' own fault-
    injection controls (`sims/_shared/faults.py`'s `X-Sim-Fault` header
    convention) — forwarded end-to-end via `graph.mcp_client.McpClient`'s
    `extra_headers` -> `AuthenticationMiddleware` (reads it off the incoming
    gateway request) -> `UpstreamClient.call()` (relays it onto the vendor
    sim call). `tool` in `domain:tool` is accepted for quickstart.md's exact
    `--inject-failure dining:confirm_reservation` spelling but the fault
    itself is process-wide per domain, not scoped to one tool name — the
    sim's own fault middleware has no per-endpoint targeting finer than
    "this sim, this request.\""""
    fault_by_domain: dict[str, str] = {}
    for spec in inject_failure:
        domain, _, _tool = spec.partition(":")
        fault_by_domain[domain] = "hold_expired"
    for domain in force_unavailable:
        fault_by_domain[domain] = _FORCE_UNAVAILABLE_FAULT

    def factory_for(domain: Domain) -> Callable[..., Any]:
        fault = fault_by_domain.get(domain.value)
        if fault is None:
            return DEFAULT_TOOLS_FACTORY[domain]
        return lambda tenant_id, guest_id: _construct_with_fault(domain, tenant_id, guest_id, fault)

    return {domain: factory_for(domain) for domain in DEFAULT_TOOLS_FACTORY}


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


async def run_demo(
    *,
    tenant_id: str,
    guest_id: str | None,
    text: str,
    inject_failure: list[str],
    force_unavailable: list[str],
    auto_approve: bool,
) -> None:
    guest_id = guest_id or DEFAULT_GUEST_BY_TENANT.get(tenant_id, "guest-mgv-001")
    tools_factory = _inject_failure_tools_factory(inject_failure, force_unavailable)
    ledger = PostgresHoldLedger() if os.environ.get("POSTGRES_URL") else None

    if os.environ.get("POSTGRES_URL"):
        from agents.dining.graph import postgres_checkpointer

        async with postgres_checkpointer() as cp:
            await _run(cp, tools_factory, ledger, tenant_id, guest_id, text, auto_approve)
    else:
        await _run(None, tools_factory, ledger, tenant_id, guest_id, text, auto_approve)


async def _run(
    checkpointer: Any, tools_factory: Any, ledger: Any, tenant_id: str, guest_id: str,
    text: str, auto_approve: bool,
) -> None:
    app = compile_supervisor_graph(checkpointer=checkpointer, tools_factory=tools_factory, ledger=ledger)
    state = new_session_state(tenant_id=tenant_id, guest_id=guest_id, text=text)
    cfg = {"configurable": {"thread_id": state["session_id"]}}

    result = await app.ainvoke(state, config=cfg)
    _print_turn(result)

    while result.get("__interrupt__") and auto_approve:
        result = await app.ainvoke(Command(resume={"action": "approved"}), config=cfg)
        _print_turn(result)

    if result.get("__interrupt__") and not auto_approve:
        print("\n[awaiting human approval — re-run with --auto-approve, or wire a real UI]")


def _print_turn(result: dict[str, Any]) -> None:
    print(f"proposals: {[p.domain.value for p in result.get('proposals', [])]}")
    print(f"holds: {[(h.domain.value, h.status.value) for h in result.get('holds', [])]}")
    print(f"confirmations: {[(c.domain.value, c.confirmation_id) for c in result.get('confirmations', [])]}")
    if result.get("failures"):
        print(f"failures: {[(f.domain.value, f.tool, f.error_class) for f in result['failures']]}")
    if result.get("escalate"):
        print(f"ESCALATED: {result.get('escalate_reason')}")
    for message in result.get("messages", [])[-1:]:
        print(f"guest-facing: {message.content}")
    print("---")


def _cli() -> None:
    parser = argparse.ArgumentParser(prog="python -m graph.demo")
    parser.add_argument("--tenant", default="MGV", choices=["MGV", "MHB"])
    parser.add_argument("--guest", default=None)
    parser.add_argument("--text", required=True, help="Guest's free-text request")
    parser.add_argument(
        "--inject-failure", action="append", default=[],
        help="domain:tool, e.g. dining:confirm_reservation (Scenario 2)",
    )
    parser.add_argument(
        "--force-unavailable", action="append", default=[],
        help="domain name, e.g. entertainment (Scenario 3/4)",
    )
    parser.add_argument(
        "--auto-approve", action="store_true",
        help="automatically approve any HumanApproval interrupt (demo convenience)",
    )
    args = parser.parse_args()

    coro = run_demo(
        tenant_id=args.tenant, guest_id=args.guest, text=args.text,
        inject_failure=args.inject_failure, force_unavailable=args.force_unavailable,
        auto_approve=args.auto_approve,
    )
    if sys.platform == "win32":
        # psycopg's async mode (used by postgres_checkpointer, per this quickstart's own
        # POSTGRES_URL prerequisite) cannot run under Windows' default ProactorEventLoop
        # — found live running specs/005's quickstart.md scenarios (Phase 7.5 gap
        # closure) — identical root cause `ui/run.py`'s own docstring already documents
        # for the verification console.
        asyncio.run(coro, loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(coro)


if __name__ == "__main__":  # pragma: no cover
    _cli()
