"""T045 — idempotency-key schema/declaration test (SC-009).

100% of mutating tools reject a call made with no idempotency key; 0% of read tools
declare an `idempotency_key` parameter at all.

Two independent proofs, matching the two halves of SC-009:

1. **Declaration** (structural, signature-level): every mutating tool's underlying
   function accepts an `idempotency_key` parameter; no read tool's does. This is what
   "declares" means at the schema level — the parameter FastMCP would expose on the
   tool's wire schema.
2. **Enforcement** (behavioral): every mutating tool, invoked with no
   `idempotency_key`, is refused by `register_tool`'s own gate *before* any upstream
   call — proven the same way `test_entitlements.py` proves entitlement refusal
   happens pre-upstream, by monkeypatching every domain's `UpstreamClient.call` to
   raise if reached at all.
"""

from __future__ import annotations

import inspect

import pytest
from gateway._shared.errors import NormalizedFailure
from gateway._shared.identity import CallerIdentity
from gateway._shared.middleware import _current_identity
from gateway.dining import server as dining_server
from gateway.entertainment import server as entertainment_server
from gateway.gaming import server as gaming_server
from gateway.stay import server as stay_server
from gateway.wellness import server as wellness_server

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
    tools = []
    for module in DOMAIN_MODULES.values():
        for name in vars(module):
            obj = getattr(module, name)
            if callable(obj) and hasattr(obj, "full_name") and hasattr(obj, "entitlement_action"):
                tools.append(obj)
    return tools


ALL_TOOLS = _all_tools()
MUTATING_TOOLS = [t for t in ALL_TOOLS if t.is_mutating]
READ_TOOLS = [t for t in ALL_TOOLS if not t.is_mutating]


def test_at_least_31_tools_discovered() -> None:
    assert len(ALL_TOOLS) >= 31


def test_mutating_and_read_tools_partition_every_tool() -> None:
    assert len(MUTATING_TOOLS) + len(READ_TOOLS) == len(ALL_TOOLS)
    assert MUTATING_TOOLS  # sanity: the fixture set is non-trivial
    assert READ_TOOLS


# --- 1. Declaration: signature-level presence/absence of idempotency_key -----------


def _declares_idempotency_key(tool) -> bool:
    """`register_tool`'s `@wraps(func)` copies `func`'s signature onto the wrapper via
    `__wrapped__`, so `inspect.signature` unwraps to the real declared parameters."""
    underlying = inspect.unwrap(tool)
    return "idempotency_key" in inspect.signature(underlying).parameters


@pytest.mark.parametrize("tool", MUTATING_TOOLS, ids=lambda t: t.full_name)
def test_100_percent_of_mutating_tools_declare_idempotency_key(tool) -> None:
    assert _declares_idempotency_key(tool), (
        f"{tool.full_name} is mutating but declares no idempotency_key parameter"
    )


@pytest.mark.parametrize("tool", READ_TOOLS, ids=lambda t: t.full_name)
def test_0_percent_of_read_tools_declare_idempotency_key(tool) -> None:
    assert not _declares_idempotency_key(tool), (
        f"{tool.full_name} is read-only but declares an idempotency_key parameter"
    )


# --- 2. Enforcement: rejected pre-upstream with no key ------------------------------


def _fail_if_called(*args, **kwargs):
    raise AssertionError("upstream call must never happen when idempotency_key is missing")


@pytest.fixture(autouse=True)
def _block_upstream(monkeypatch):
    for client in DOMAIN_CLIENTS.values():
        monkeypatch.setattr(client, "call", _fail_if_called)
    yield


def _grant_full_scopes():
    identity = CallerIdentity(
        sub="idempotency-schema-test",
        tenant_id="MGV",
        scopes=tuple(f"{d}:{a}" for d in DOMAIN_MODULES for a in ("read", "hold", "commit")),
        iat=0,
        exp=99_999_999_999,
    )
    return _current_identity.set(identity)


@pytest.mark.parametrize("tool", MUTATING_TOOLS, ids=lambda t: t.full_name)
async def test_100_percent_of_mutating_tools_reject_a_call_with_no_key(tool) -> None:
    token = _grant_full_scopes()
    try:
        result = await tool(input=None)  # no idempotency_key kwarg at all
    finally:
        _current_identity.reset(token)
    assert isinstance(result, NormalizedFailure), (
        f"{tool.full_name}: expected refusal for missing idempotency_key, got {result!r}"
    )
    assert result.error_class == "TERMINAL"
    assert "idempotency_key" in result.detail


@pytest.mark.parametrize("tool", MUTATING_TOOLS, ids=lambda t: t.full_name)
async def test_100_percent_of_mutating_tools_reject_an_explicit_none_key(tool) -> None:
    token = _grant_full_scopes()
    try:
        result = await tool(input=None, idempotency_key=None)
    finally:
        _current_identity.reset(token)
    assert isinstance(result, NormalizedFailure)
    assert result.error_class == "TERMINAL"
    assert "idempotency_key" in result.detail
