"""T051/T052: Entitlement declaration and zero-scope tests (FR-014).

T051 proves enforcement reads the tool's *declared* entitlement, not anything inferable
from its name, via a deliberately misnamed fixture tool registered right here: a
function named like a `read` tool (`check_*`) but declared `commit`.

T052 proves a token that verifies but carries zero scopes is refused uniformly by every
tool in the live registry — not just the fixture.

`register_tool` (`gateway._shared.registry`) holds no mutable global registry to
pollute — it is a pure decorator that returns a wrapped callable — so the fixture tool
below is registered directly in this test module without needing an isolated registry
instance or any monkeypatching of the real one.
"""

from __future__ import annotations

import pytest
from gateway._shared import middleware
from gateway._shared.errors import NormalizedFailure
from gateway._shared.identity import CallerIdentity
from gateway._shared.registry import register_tool
from gateway.dining import server as dining_server
from gateway.entertainment import server as entertainment_server
from gateway.gaming import server as gaming_server
from gateway.stay import server as stay_server
from gateway.wellness import server as wellness_server
from pydantic import BaseModel

DOMAINS = ("dining", "entertainment", "stay", "wellness", "gaming")

DOMAIN_MODULES = {
    "dining": dining_server,
    "entertainment": entertainment_server,
    "stay": stay_server,
    "wellness": wellness_server,
    "gaming": gaming_server,
}


def _all_tools() -> list:
    tools = []
    for module in DOMAIN_MODULES.values():
        for name in vars(module):
            obj = getattr(module, name)
            if callable(obj) and hasattr(obj, "full_name") and hasattr(obj, "entitlement_action"):
                tools.append(obj)
    return tools


ALL_TOOLS = _all_tools()


def _set_identity(scopes: list[str]) -> object:
    identity = CallerIdentity(
        sub="declaration-test", tenant_id="MGV", scopes=tuple(scopes), iat=0, exp=99999999999
    )
    return middleware._current_identity.set(identity)


# --- T051: declaration coverage ------------------------------------------------------


def test_all_31_tools_declare_exactly_one_entitlement_action():
    assert len(ALL_TOOLS) >= 31, (
        f"expected at least 31 declared tools, found {len(ALL_TOOLS)}: "
        f"{[t.full_name for t in ALL_TOOLS]}"
    )
    for tool in ALL_TOOLS:
        # `entitlement_action` is a single `Literal["read", "hold", "commit"]` value
        # stamped by `register_tool` — "exactly one" is structural (a scalar attribute,
        # not a set) rather than something this test needs to police separately.
        assert tool.entitlement_action in ("read", "hold", "commit"), (
            f"{tool.full_name} declares {tool.entitlement_action!r}"
        )


# --- T051: the deliberately misnamed fixture tool ------------------------------------

# Named exactly like the read tools in every real domain server (`check_rg_status`,
# `check_availability`, `check_service_availability`, `check_dining_status`) — a
# name-based enforcement scheme would treat this as a `read` tool. It is declared
# `commit` here. If enforcement ever regresses to inferring from the name (matching
# `check_*` -> read), this fixture calling under a `dining:read`-only identity would
# succeed instead of being refused, and `test_declaration_wins_over_misleading_name`
# below would fail.


class _FakeStatusInput(BaseModel):
    resource_id: str


@register_tool(domain="dining", entitlement_action="commit", is_mutating=True)
async def check_fake_status(
    input: _FakeStatusInput, idempotency_key: str | None = None
) -> str:
    """Looks like a read-only status check by name; is declared `commit`."""
    return f"committed:{input.resource_id}"


def test_fixture_tool_is_declared_commit_despite_read_shaped_name():
    assert check_fake_status.full_name == "resortflow.dining.check_fake_status"
    assert check_fake_status.entitlement_action == "commit"


async def test_declaration_wins_over_misleading_name_read_scope_refused():
    """A `dining:read` scope — which a name-based ("check_* is a read") scheme would
    accept — is refused, because the declaration is `commit`."""
    token = _set_identity(["dining:read"])
    try:
        result = await check_fake_status(input=_FakeStatusInput(resource_id="r1"))
    finally:
        middleware._current_identity.reset(token)
    assert isinstance(result, NormalizedFailure)
    assert result.error_class == "TERMINAL"


async def test_declaration_wins_over_misleading_name_commit_scope_admitted():
    """The matching declared scope (`dining:commit`) is admitted through the
    entitlement gate and reaches the tool body — proving the check reads
    `entitlement_action`, not any pattern in `func.__name__`."""
    token = _set_identity(["dining:commit"])
    try:
        result = await check_fake_status(
            input=_FakeStatusInput(resource_id="r1"), idempotency_key="resortflow:test:r1:1"
        )
    finally:
        middleware._current_identity.reset(token)
    assert result == "committed:r1"


# --- T052: zero-entitlement refusal ---------------------------------------------------


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.full_name)
async def test_zero_scope_token_refused_by_every_tool(tool):
    """A token that verifies (valid signature/claims) but carries no scopes at all is
    refused uniformly — every tool, not just a subset."""
    token = _set_identity([])
    try:
        result = await tool(input=None)
    finally:
        middleware._current_identity.reset(token)
    assert isinstance(result, NormalizedFailure), (
        f"{tool.full_name}: zero-scope caller must be refused, got {result!r}"
    )
    assert result.error_class == "TERMINAL"


async def test_zero_scope_token_refused_by_fixture_tool_too():
    token = _set_identity([])
    try:
        result = await check_fake_status(input=_FakeStatusInput(resource_id="r1"))
    finally:
        middleware._current_identity.reset(token)
    assert isinstance(result, NormalizedFailure)
    assert result.error_class == "TERMINAL"
