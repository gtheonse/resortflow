"""T036/T038 [US2] [CRIT] — adversarial tenant-isolation suite.

Proves, by attempting the cross-tenant access rather than asserting config,
that `tenant_id` cannot cross by any means AGENTS.md's hard rule forbids:
"Never derive tenant_id from model output. It comes from the token claim,
injected server-side." Every test here drives a real MCP wire session (real
JSON-RPC over a real streamable-HTTP transport, in-process ASGI — no live
vendor sim) against every domain, for both tenants (MGV, MHB):

1. A normal call scopes to the caller's own tenant (upstream sees the right
   `tenant_id`).
2. An explicit `tenant_id` argument smuggled into the tool call is refused
   OUTRIGHT (the call fails), not silently dropped and the real tenant used
   anyway — silently ignoring it would still be "safe" for tenancy but is not
   what the task requires: a caller must see their attempted override fail.
3. A resource id belonging to the other tenant is refused or produces
   not-found — never served from the other tenant's data.
4. No value belonging to the other tenant ever appears in a response or a
   failure's `detail` string (SC-003).

T038's registry-wide schema property (no tool anywhere accepts a tenant
field) is appended at the bottom of this file to keep it with the rest of
the tenant-isolation proof.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, patch

import pytest
from gateway._shared.errors import UpstreamFailure

from ..fixtures.wire import DOMAINS, domain_session, mint

TENANTS = ("MGV", "MHB")
OTHER_TENANT = {"MGV": "MHB", "MHB": "MGV"}


@pytest.mark.asyncio
@pytest.mark.parametrize("domain_name", sorted(DOMAINS))
@pytest.mark.parametrize("tenant_id", TENANTS)
async def test_normal_call_scoped_to_own_tenant(domain_name: str, tenant_id: str):
    info = DOMAINS[domain_name]
    token = mint(tenant_id, info.all_scopes)
    fake_response = info.read_response_factory()
    captured: dict = {}

    async def _fake_call(*args, **kwargs):
        captured["tenant_id"] = kwargs["tenant_id"]
        return fake_response

    with patch.object(info.module, info.client_attr) as mocked:
        mocked.call = AsyncMock(side_effect=_fake_call)
        async with domain_session(info, token=token) as session:
            result = await session.call_tool(
                f"resortflow.{domain_name}.{info.read_tool}",
                arguments={"input": info.read_tool_args(f"{tenant_id}-res-1")},
            )

    assert not result.is_error, f"{domain_name}/{tenant_id}: normal call should succeed"
    assert captured["tenant_id"] == tenant_id, (
        f"{domain_name}: upstream call received tenant_id={captured['tenant_id']!r}, "
        f"expected {tenant_id!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("domain_name", sorted(DOMAINS))
@pytest.mark.parametrize("tenant_id", TENANTS)
async def test_explicit_tenant_id_argument_refused_outright(domain_name: str, tenant_id: str):
    """A caller stuffing a `tenant_id`-shaped field into the tool's own
    arguments must have the CALL REFUSED (an error result), not have the
    field silently dropped and the request served anyway. Silently ignoring
    it would still be tenant-safe, but is not "refused outright" — the input
    schema must reject the unrecognized field."""
    info = DOMAINS[domain_name]
    token = mint(tenant_id, info.all_scopes)
    other_tenant = OTHER_TENANT[tenant_id]
    captured: dict = {}

    async def _fake_call(*args, **kwargs):
        captured["tenant_id"] = kwargs["tenant_id"]
        return info.read_response_factory()

    with patch.object(info.module, info.client_attr) as mocked:
        mocked.call = AsyncMock(side_effect=_fake_call)
        async with domain_session(info, token=token) as session:
            result = await session.call_tool(
                f"resortflow.{domain_name}.{info.read_tool}",
                arguments={
                    "input": {
                        **info.read_tool_args("resource-1"),
                        "tenant_id": other_tenant,
                    }
                },
            )

    assert result.is_error, (
        f"{domain_name}: a call carrying an explicit tenant_id argument must be refused "
        f"outright, not silently accepted"
    )
    # And even if the upstream call somehow happened, it must never have seen the
    # attempted override.
    if "tenant_id" in captured:
        assert captured["tenant_id"] != other_tenant


@pytest.mark.asyncio
@pytest.mark.parametrize("domain_name", sorted(DOMAINS))
@pytest.mark.parametrize("tenant_id", TENANTS)
async def test_other_tenant_resource_id_refused_or_not_found(domain_name: str, tenant_id: str):
    info = DOMAINS[domain_name]
    token = mint(tenant_id, info.all_scopes)
    other_tenant = OTHER_TENANT[tenant_id]
    captured: dict = {}

    async def _fake_call(*args, response_model=None, **kwargs):
        captured["tenant_id"] = kwargs["tenant_id"]
        # The requested resource belongs to the OTHER tenant — a real
        # tenant-partitioned vendor would refuse this for the caller's tenant.
        raise UpstreamFailure(error_class="TERMINAL", domain=domain_name, detail="resource not found")

    with patch.object(info.module, info.client_attr) as mocked:
        mocked.call = AsyncMock(side_effect=_fake_call)
        async with domain_session(info, token=token) as session:
            result = await session.call_tool(
                f"resortflow.{domain_name}.{info.read_tool}",
                arguments={"input": info.read_tool_args(f"{other_tenant}-res-1")},
            )

    assert result.is_error, f"{domain_name}: other-tenant resource id must be refused/not-found"
    assert captured["tenant_id"] == tenant_id, (
        "upstream must still have been called with the CALLER's own tenant_id, "
        "never the other tenant's"
    )
    payload = result.structured_content or {}
    detail = str(payload.get("detail", ""))
    assert other_tenant not in detail, (
        f"{domain_name}: failure detail must not name the other tenant: {detail!r}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("domain_name", sorted(DOMAINS))
async def test_no_other_tenant_value_in_success_response(domain_name: str):
    """Belt-and-braces SC-003 check: a normal successful call's response
    contains no literal reference to the tenant the caller does NOT belong
    to."""
    info = DOMAINS[domain_name]
    tenant_id = "MGV"
    other_tenant = "MHB"
    token = mint(tenant_id, info.all_scopes)

    async def _fake_call(*args, **kwargs):
        assert kwargs["tenant_id"] == tenant_id
        return info.read_response_factory()

    with patch.object(info.module, info.client_attr) as mocked:
        mocked.call = AsyncMock(side_effect=_fake_call)
        async with domain_session(info, token=token) as session:
            result = await session.call_tool(
                f"resortflow.{domain_name}.{info.read_tool}",
                arguments={"input": info.read_tool_args("resource-1")},
            )

    assert not result.is_error
    payload_text = str(result.structured_content)
    assert other_tenant not in payload_text, (
        f"{domain_name}: response payload must never mention {other_tenant!r}"
    )


# --- T038: registry-wide "no tool schema accepts a tenant field" property ------------


def _all_tool_input_models() -> dict[str, type]:
    """Every registered tool's input model class, across all five domains,
    keyed by the tool's full `resortflow.<domain>.<verb>_<noun>` name."""
    models: dict[str, type] = {}
    for info in DOMAINS.values():
        for obj in vars(info.module).values():
            if callable(obj) and hasattr(obj, "full_name") and getattr(obj, "domain", None) == info.name:
                # `register_tool`'s wrapper is generic (*args, **kwargs); recover the
                # original function's annotated `input` parameter from `__wrapped__`
                # set by `functools.wraps`.
                original = obj.__wrapped__ if hasattr(obj, "__wrapped__") else obj
                # `eval_str=True`: every domain server module has `from __future__
                # import annotations` (PEP 563), so `input`'s annotation is the bare
                # string "SomeInput" unless resolved against the function's own
                # module globals here.
                orig_sig = inspect.signature(original, eval_str=True)
                input_param = orig_sig.parameters.get("input")
                if input_param is not None and hasattr(input_param.annotation, "model_fields"):
                    models[obj.full_name] = input_param.annotation
    return models


def test_registry_wide_no_tool_input_model_accepts_a_tenant_field():
    """Across all 31 tools: no input model declares a forbidden tenant field
    (already enforced structurally at registration by
    `_shared.tenancy.assert_no_tenant_field` — this is the enumerable proof
    of it) AND, since a merely-undeclared field can still be silently
    accepted and ignored by default Pydantic behaviour, every input model
    must also reject an unrecognized `tenant_id`/`tenant` field outright via
    `model_config = {"extra": "forbid"}` — the property T036's smuggled-field
    tests exercise live."""
    models = _all_tool_input_models()
    assert len(models) >= 1

    forbidden_field_failures = []
    extra_not_forbidden_failures = []
    for tool_name, model in models.items():
        if {"tenant_id", "tenant", "x_tenant_id"} & model.model_fields.keys():
            forbidden_field_failures.append(tool_name)
        if model.model_config.get("extra") != "forbid":
            extra_not_forbidden_failures.append(tool_name)

    assert not forbidden_field_failures, (
        f"tools declaring a forbidden tenant field: {forbidden_field_failures}"
    )
    assert not extra_not_forbidden_failures, (
        f"tools whose input model does not reject unrecognized fields "
        f"(extra != 'forbid'), so a smuggled tenant_id argument would be "
        f"silently ignored rather than refused: {extra_not_forbidden_failures}"
    )

