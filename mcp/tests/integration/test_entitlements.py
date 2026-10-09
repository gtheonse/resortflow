"""T050: Entitlement matrix test (SC-005).

Every declared scope string x every tool: a caller lacking a tool's entitlement is
refused before any upstream call. `read`+`hold` is refused on 100% of commit tools in
every domain.

This test is `[CRIT]` (adjacent to policy enforcement): it must exercise the actual
registered tool wrappers (`gateway._shared.registry.register_tool`'s `wrapper`), not a
reimplementation of the check, so it fails the moment `check_entitlement` stops being
called first in the real call path.
"""

from __future__ import annotations

import pytest
from gateway._shared import middleware
from gateway._shared.errors import NormalizedFailure
from gateway._shared.identity import CallerIdentity
from gateway.dining import server as dining_server
from gateway.entertainment import server as entertainment_server
from gateway.gaming import server as gaming_server
from gateway.stay import server as stay_server
from gateway.wellness import server as wellness_server

DOMAINS = ("dining", "entertainment", "stay", "wellness", "gaming")
ACTIONS = ("read", "hold", "commit")
ALL_SCOPES = tuple(f"{domain}:{action}" for domain in DOMAINS for action in ACTIONS)

DOMAIN_MODULES = {
    "dining": dining_server,
    "entertainment": entertainment_server,
    "stay": stay_server,
    "wellness": wellness_server,
    "gaming": gaming_server,
}

DOMAIN_CLIENTS = {
    "dining": dining_server.dining_client,
    "entertainment": entertainment_server.entertainment_client,
    "stay": stay_server.stay_client,
    "wellness": wellness_server.wellness_client,
    "gaming": gaming_server.gaming_client,
}


def _all_tools() -> list:
    """Every tool wrapper produced by `register_tool` across all five domains,
    discovered structurally (via the attributes the decorator stamps on), never by a
    hardcoded name list — so this test tracks the live registry."""
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
        sub="entitlement-matrix-test",
        tenant_id="MGV",
        scopes=tuple(scopes),
        iat=0,
        exp=99999999999,
    )
    return middleware._current_identity.set(identity)


def _fail_if_called(*args, **kwargs):
    raise AssertionError("upstream call must never happen when entitlement check fails")


@pytest.fixture(autouse=True)
def _block_upstream(monkeypatch):
    """Every domain client's `.call` is guarded: any reach to it during a
    refused-entitlement call is a hard test failure, proving the refusal happens
    before any upstream call (not merely that the caller sees a failure)."""
    for client in DOMAIN_CLIENTS.values():
        monkeypatch.setattr(client, "call", _fail_if_called)
    yield


def test_at_least_31_tools_discovered():
    assert len(ALL_TOOLS) >= 31, (
        f"expected at least 31 declared tools across all domains, found "
        f"{len(ALL_TOOLS)}: {[t.full_name for t in ALL_TOOLS]}"
    )


def test_every_tool_declares_a_known_entitlement_action():
    for tool in ALL_TOOLS:
        assert tool.entitlement_action in ACTIONS, (
            f"{tool.full_name} declares unknown entitlement_action {tool.entitlement_action!r}"
        )


@pytest.mark.parametrize("tool", ALL_TOOLS, ids=lambda t: t.full_name)
async def test_caller_lacking_entitlement_is_refused_before_upstream_call(tool):
    """Grant every scope string except the one this tool declares. The caller is
    refused, and the upstream client is never touched (enforced by `_block_upstream`)."""
    required_scope = f"{tool.domain}:{tool.entitlement_action}"
    scopes = [s for s in ALL_SCOPES if s != required_scope]
    token = _set_identity(scopes)
    try:
        result = await tool(input=None)
    finally:
        middleware._current_identity.reset(token)

    assert isinstance(result, NormalizedFailure), (
        f"{tool.full_name}: expected a NormalizedFailure refusal, got {result!r}"
    )
    assert result.error_class == "TERMINAL"
    assert result.domain == tool.domain


@pytest.mark.parametrize("domain", DOMAINS)
async def test_read_and_hold_never_grants_commit(domain):
    """SC-005's second clause: read+hold is refused on 100% of commit tools, in every
    domain that has any (gaming has none — the loop is then vacuously satisfied, which
    is exactly T053/T054's separate claim about gaming)."""
    scopes = [f"{domain}:read", f"{domain}:hold"]
    commit_tools = [t for t in ALL_TOOLS if t.domain == domain and t.entitlement_action == "commit"]

    for tool in commit_tools:
        token = _set_identity(scopes)
        try:
            result = await tool(input=None)
        finally:
            middleware._current_identity.reset(token)
        assert isinstance(result, NormalizedFailure), (
            f"{tool.full_name}: read+hold must not grant commit"
        )
        assert result.error_class == "TERMINAL"


def test_commit_tools_exist_in_at_least_one_domain():
    """Sanity guard: if no commit tool is discovered anywhere, the parametrized test
    above would pass vacuously for every domain and hide a discovery bug."""
    commit_tools = [t for t in ALL_TOOLS if t.entitlement_action == "commit"]
    assert commit_tools, "expected at least one commit tool across all domains"
