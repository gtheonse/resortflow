"""T053/T054: Gaming domain read-only guarantee (SC-011).

The read-only claim for the gaming/casino domain is provable by enumeration, not by
reading a prompt (AGENTS.md hard rule: "Never let an agent place a wager or execute a
gaming transaction. The gaming domain is read-only.").

T053 enumerates the live gaming registry: only `check_*`/`get_*`/`list_*` tool names,
zero `hold`/`commit` entitlements, and zero `create_*`/`confirm_*`/`cancel_*` names.

T054 proves a plausible gaming mutation fails as an *unknown tool* — absent from the
registry entirely — not as a present-but-refused call. That distinction matters: a
present-but-denied tool is still an attack surface an entitlement misconfiguration could
open; an absent tool cannot be invoked no matter what scopes a caller holds.
"""

from __future__ import annotations

import pytest
from gateway.gaming import server as gaming_server

ALLOWED_NAME_PREFIXES = ("check_", "get_", "list_")
FORBIDDEN_NAME_PREFIXES = ("create_", "confirm_", "cancel_", "place_", "hold_", "release_")


def _gaming_tools() -> list:
    tools = []
    for name in vars(gaming_server):
        obj = getattr(gaming_server, name)
        if callable(obj) and hasattr(obj, "full_name") and hasattr(obj, "entitlement_action"):
            tools.append(obj)
    return tools


GAMING_TOOLS = _gaming_tools()


def test_gaming_registry_is_nonempty():
    assert len(GAMING_TOOLS) == 3, (
        f"expected the 3 documented gaming tools, found {len(GAMING_TOOLS)}: "
        f"{[t.full_name for t in GAMING_TOOLS]}"
    )


def test_gaming_tools_are_all_check_get_or_list_named():
    for tool in GAMING_TOOLS:
        bare_name = tool.full_name.rsplit(".", 1)[-1]
        assert bare_name.startswith(ALLOWED_NAME_PREFIXES), (
            f"{tool.full_name}: gaming tool names must be check_*/get_*/list_* only"
        )


def test_gaming_tools_declare_zero_hold_or_commit_entitlements():
    for tool in GAMING_TOOLS:
        assert tool.entitlement_action == "read", (
            f"{tool.full_name}: gaming tools must declare 'read' only, "
            f"found {tool.entitlement_action!r}"
        )


def test_gaming_tools_are_not_flagged_mutating():
    for tool in GAMING_TOOLS:
        assert tool.is_mutating is False, (
            f"{tool.full_name}: gaming tools must not be mutating"
        )


def test_no_mutation_shaped_name_exists_anywhere_in_gaming_module():
    """Zero `create_*`/`confirm_*`/`cancel_*` (or equivalent wager/hold/release)
    top-level names anywhere in the gaming server module — not just among registered
    tools, since a stray helper with such a name would itself be a smell."""
    for name in vars(gaming_server):
        assert not name.startswith(FORBIDDEN_NAME_PREFIXES), (
            f"gateway.gaming.server defines {name!r}, which looks like a mutation"
        )


@pytest.mark.parametrize(
    "mutation_name", ["place_wager", "confirm_bet", "create_wager", "cancel_bet", "hold_wager"]
)
def test_plausible_gaming_mutation_is_unknown_not_denied(mutation_name):
    """A plausible gaming mutation, invoked by name, fails as an unknown tool — it is
    simply absent from `gateway.gaming.server`'s module namespace, never present as a
    registered-but-refused tool."""
    assert not hasattr(gaming_server, mutation_name), (
        f"gateway.gaming.server unexpectedly defines {mutation_name!r} — "
        f"a gaming mutation must not exist at all, present-but-refused is not enough"
    )
    registered_names = {t.full_name.rsplit(".", 1)[-1] for t in GAMING_TOOLS}
    assert mutation_name not in registered_names
