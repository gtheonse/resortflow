"""T027 [US1] — surface uniformity integration test.

One read tool per domain, invoked over a real MCP wire session (real
JSON-RPC over a real streamable-HTTP transport, in-process ASGI — no live
vendor sim), returns a typed, schema-conformant result. No vendor envelope
key (`error`, `fault`, `RESPONSE`) and no vendor credential value ever
appears anywhere in the response — the whole point of the governed surface
(Principle II).

Each domain's `UpstreamClient.call` is mocked (there is no live
`sims/*` process in this test environment) to return a minimal, fully
valid instance of the tool's own declared response model — the one seam
this suite does not exercise for real is the vendor HTTP hop itself;
everything from the wire session downward (auth, tenancy injection, tool
dispatch, response serialization) is real.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from gateway import settings

from ..fixtures.wire import DOMAINS, domain_session, mint

VENDOR_ENVELOPE_KEYS = {"error", "fault", "RESPONSE", "err_code"}

VENDOR_CREDENTIAL_VALUES = {
    settings.DINING_API_KEY,
    settings.TICKETING_CLIENT_ID,
    settings.TICKETING_CLIENT_SECRET,
    settings.TICKETING_HMAC_SECRET,
    settings.PMS_CLIENT_ID,
    settings.PMS_CLIENT_SECRET,
    settings.AMENITIES_USERNAME,
    settings.AMENITIES_PASSWORD,
    settings.CASINO_BEARER_TOKEN,
    settings.MCP_DEV_SIGNING_KEY,
}


def _find_envelope_keys(payload) -> set[str]:
    found: set[str] = set()

    def _walk(node) -> None:
        if isinstance(node, dict):
            found.update(VENDOR_ENVELOPE_KEYS & node.keys())
            for value in node.values():
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)
    return found


@pytest.mark.asyncio
@pytest.mark.parametrize("domain_name", sorted(DOMAINS))
async def test_one_read_per_domain_returns_typed_uniform_result(domain_name: str):
    info = DOMAINS[domain_name]
    token = mint("MGV", info.all_scopes)

    fake_response = info.read_response_factory()

    async def _fake_call(*args, **kwargs):
        return fake_response

    with patch.object(info.module, info.client_attr) as mocked_client:
        mocked_client.call = AsyncMock(side_effect=_fake_call)

        async with domain_session(info, token=token) as session:
            input_args = info.read_tool_args("resource-1")
            result = await session.call_tool(
                f"resortflow.{domain_name}.{info.read_tool}", arguments={"input": input_args}
            )

    assert not result.is_error, f"{domain_name}: read call reported an error: {result}"
    payload = result.structured_content
    assert payload is not None, f"{domain_name}: no structured content returned"

    # No vendor envelope key anywhere in the returned payload.
    envelope_hits = _find_envelope_keys(payload)
    assert not envelope_hits, f"{domain_name}: vendor envelope key(s) leaked: {envelope_hits}"

    # No vendor credential value anywhere in the serialized payload.
    serialized = json.dumps(payload, default=str)
    for credential in VENDOR_CREDENTIAL_VALUES:
        assert credential not in serialized, (
            f"{domain_name}: vendor credential value leaked into response: {credential!r}"
        )
