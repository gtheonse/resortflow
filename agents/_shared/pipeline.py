"""The propose-and-hold skeleton every saga participant shares: run a sequence of
the domain's own node functions against a working copy of state, accumulate each
delta, and convert the domain's tool error into a terminal `ToolFailure`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from graph.errors import ErrorClass
from graph.models import Domain, ToolFailure

Apply = Callable[[dict[str, Any]], None]
Body = Callable[[dict[str, Any], dict[str, Any], Apply], Awaitable[dict[str, Any] | None]]


async def run_propose_and_hold(
    state: dict[str, Any],
    *,
    domain: Domain,
    error_type: type[Exception],
    body: Body,
) -> dict[str, Any]:
    """`body(working, accumulated, apply)` applies each step's delta via `apply`
    and may return a complete delta to short-circuit (e.g. a policy block)."""
    working: dict[str, Any] = dict(state)
    accumulated: dict[str, Any] = {}

    def apply(update: dict[str, Any]) -> None:
        accumulated.update(update)
        working.update(update)

    try:
        early = await body(working, accumulated, apply)
        if early is not None:
            return early
    except error_type as exc:
        accumulated.setdefault("failures", []).append(
            ToolFailure(
                domain=domain, tool="propose_and_hold", error_class=ErrorClass.TERMINAL,
                detail=getattr(exc, "detail", str(exc)),
            )
        )
    return accumulated


def selected_proposal(working: dict[str, Any]) -> Any:
    return next(
        (p for p in working.get("proposals", []) if p.proposal_id == working.get("selected_proposal_id")),
        None,
    )
