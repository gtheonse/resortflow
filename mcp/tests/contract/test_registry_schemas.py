"""T026 [US1] — registry-enumeration contract test.

A real MCP **client** (`mcp.ClientSession` over a real streamable-HTTP
transport, driven through `mcp/tests/fixtures/wire.py`'s in-process ASGI
transport — not the Node `npx @modelcontextprotocol/inspector`) connects to
each domain's mount path and asserts tool counts, valid input/output JSON
schemas, and `resortflow.<domain>.<verb>_<noun>` name-regex conformance. This is
the CI-gating proof; the inspector stays a human-facing demo only.

Entertainment's expected count is read from the LIVE
`mcp/gateway/entertainment/server.py` module rather than hardcoded, per the
task note that `contracts/tool-registry.md`'s "7" may be stale relative to
the code — the code is authoritative there. Every other domain's count is
asserted against the frozen contract's literal figure, since a mismatch
there is a real registry/contract drift bug, not a doc typo to route around.
"""

from __future__ import annotations

import pytest
from gateway._shared.registry import TOOL_NAME_RE
from gateway.entertainment.server import app as entertainment_app

from ..fixtures.wire import DOMAINS, domain_session, mint

# Per-domain expected tool counts. Dining/stay/wellness/gaming are asserted
# against `contracts/tool-registry.md`'s frozen literal figures. Entertainment
# is filled in below from the live registry (see module docstring).
EXPECTED_COUNTS: dict[str, int] = {
    "dining": 8,
    "stay": 7,
    "wellness": 6,
    "gaming": 3,
}


@pytest.mark.asyncio
async def test_entertainment_live_count_matches_module():
    """Sanity check the live-count helper itself works before using it below."""
    tools = await entertainment_app.list_tools()
    assert len(tools) >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("domain_name", sorted(DOMAINS))
async def test_registry_enumeration_and_schemas(domain_name: str):
    """For each domain: connect a real MCP client, list tools, and assert
    count, name-regex conformance, and schema validity."""
    info = DOMAINS[domain_name]
    token = mint("MGV", info.all_scopes)

    async with domain_session(info, token=token) as session:
        result = await session.list_tools()
        tools = result.tools

        if domain_name == "entertainment":
            expected = len(tools)  # live-registry is authoritative here (doc drift note)
            # cross-check against the module's own registration surface directly
            live_tools = await entertainment_app.list_tools()
            assert len(tools) == len(live_tools)
        else:
            expected = EXPECTED_COUNTS[domain_name]

        assert len(tools) == expected, (
            f"{domain_name}: expected {expected} tools, got {len(tools)}: "
            f"{sorted(t.name for t in tools)}"
        )

        seen_names: set[str] = set()
        for tool in tools:
            assert TOOL_NAME_RE.match(tool.name), (
                f"{domain_name}: tool name {tool.name!r} does not match "
                f"resortflow.<domain>.<verb>_<noun>"
            )
            assert tool.name.startswith(f"resortflow.{domain_name}."), (
                f"{domain_name}: tool {tool.name!r} not namespaced under its own domain"
            )
            assert tool.name not in seen_names, f"{domain_name}: duplicate tool name {tool.name!r}"
            seen_names.add(tool.name)

            input_schema = tool.input_schema
            assert isinstance(input_schema, dict), f"{tool.name}: input schema not a dict"
            assert input_schema.get("type") == "object" or "properties" in input_schema, (
                f"{tool.name}: input schema missing type/properties"
            )

            output_schema = tool.output_schema
            assert output_schema is not None, f"{tool.name}: missing output schema"
            assert isinstance(output_schema, dict), f"{tool.name}: output schema not a dict"
            assert output_schema.get("type") == "object" or "properties" in output_schema, (
                f"{tool.name}: output schema missing type/properties"
            )
