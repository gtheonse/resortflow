"""Entitlement enforcement: read/hold/commit vocabulary tied to reversibility (ADR-013).

`[CRIT]` per docs/models.md — a silent failure here is a licensing incident. Every tool
declares exactly one of `read`/`hold`/`commit`. The check happens at registration time
(startup), not at request time — violations fail loudly upfront rather than silently
granting access to a tool that should have been gated.

The entitlement is a structural grant, not a convention: a caller with `hold` but not
`commit` cannot invoke a `commit` tool, full stop. This is where the reversible/
irreversible boundary (Principle III, ADR-006) is actually enforced.
"""

from __future__ import annotations

from typing import Literal

from gateway._shared.middleware import get_current_identity

EntitlementAction = Literal["read", "hold", "commit"]


class EntitlementError(Exception):
    """Raised when a caller lacks the declared entitlement for a tool."""


def check_entitlement(domain: str, action: EntitlementAction) -> None:
    """Called at tool invocation time. Raises if the caller's scopes don't include
    this tool's declared entitlement — before any upstream call or tool body runs."""
    identity = get_current_identity()
    scope = f"{domain}:{action}"
    if scope not in identity.scopes:
        raise EntitlementError(
            f"caller lacks entitlement {scope!r} "
            f"(caller has: {', '.join(sorted(identity.scopes))})"
        )
