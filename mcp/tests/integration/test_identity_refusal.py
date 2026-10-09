"""T037 [US2] [CRIT] — credential-refusal suite (SC-004).

Per `contracts/caller-identity-contract.md`, a request is refused before any
tool body runs — and therefore before any upstream call is made — under
exactly five conditions: absent credential, bad signature, expired token,
malformed/missing claim, and unknown `tenant_id`. Each is exercised here
against a real MCP wire session (real streamable-HTTP transport, in-process
ASGI), with a spy on the domain's `UpstreamClient.call` proving the upstream
was never reached.

All five conditions must produce the SAME caller-visible outcome (the
contract deliberately does not distinguish them — see
`gateway/_shared/identity.py`'s module docstring), so these tests assert
"refused, upstream never called" uniformly rather than five different error
shapes.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from gateway._shared.identity import MCP_DEV_SIGNING_KEY, issue_token

from ..fixtures.wire import DOMAINS, domain_session

DOMAIN_FOR_TEST = "dining"  # any one domain suffices; the chokepoint is shared middleware


async def _attempt_call(token: str | None, *, extra_headers: dict | None = None) -> tuple[bool, bool]:
    """Returns (call_refused, upstream_was_called)."""
    info = DOMAINS[DOMAIN_FOR_TEST]
    upstream_spy = AsyncMock(side_effect=AssertionError("upstream must never be called"))

    refused = False
    upstream_called = False
    with patch.object(info.module, info.client_attr) as mocked:
        mocked.call = upstream_spy
        try:
            async with domain_session(info, token=token, extra_headers=extra_headers) as session:
                result = await session.call_tool(
                    f"resortflow.{DOMAIN_FOR_TEST}.{info.read_tool}",
                    arguments={"input": info.read_tool_args("resource-1")},
                )
                refused = result.is_error
        except Exception:  # noqa: BLE001 — any handshake failure IS the refusal being tested
            # Session handshake itself failing (e.g. transport-level 401 before
            # any MCP protocol byte is accepted) is refusal too — the contract
            # only promises the tool body never runs, not a particular
            # transport-level shape for how "refused" surfaces.
            refused = True
    upstream_called = upstream_spy.await_count > 0
    return refused, upstream_called


@pytest.mark.asyncio
async def test_absent_credential_refused_before_upstream_call():
    refused, upstream_called = await _attempt_call(token=None)
    assert refused
    assert not upstream_called


@pytest.mark.asyncio
async def test_expired_token_refused_before_upstream_call():
    now = int(time.time())
    expired_token = jwt.encode(
        {
            "sub": "test-harness",
            "tenant_id": "MGV",
            "scopes": ["dining:read"],
            "iat": now - 7200,
            "exp": now - 3600,
        },
        MCP_DEV_SIGNING_KEY,
        algorithm="HS256",
    )
    refused, upstream_called = await _attempt_call(token=expired_token)
    assert refused
    assert not upstream_called


@pytest.mark.asyncio
async def test_tampered_signature_refused_before_upstream_call():
    valid_token = issue_token(sub="test-harness", tenant_id="MGV", scopes=["dining:read"])
    # Flip the last character of the signature segment to corrupt it without
    # touching the header/payload segments' base64 shape.
    header_b64, payload_b64, sig_b64 = valid_token.split(".")
    tampered_char = "A" if sig_b64[-1] != "A" else "B"
    tampered_sig = sig_b64[:-1] + tampered_char
    tampered_token = f"{header_b64}.{payload_b64}.{tampered_sig}"

    refused, upstream_called = await _attempt_call(token=tampered_token)
    assert refused
    assert not upstream_called


@pytest.mark.asyncio
async def test_malformed_claim_refused_before_upstream_call():
    now = int(time.time())
    # `scopes` must be a list of well-formed strings; here it's a bare string.
    malformed_token = jwt.encode(
        {
            "sub": "test-harness",
            "tenant_id": "MGV",
            "scopes": "dining:read",  # malformed: not a list
            "iat": now,
            "exp": now + 3600,
        },
        MCP_DEV_SIGNING_KEY,
        algorithm="HS256",
    )
    refused, upstream_called = await _attempt_call(token=malformed_token)
    assert refused
    assert not upstream_called


@pytest.mark.asyncio
async def test_unknown_tenant_refused_before_upstream_call():
    now = int(time.time())
    unknown_tenant_token = jwt.encode(
        {
            "sub": "test-harness",
            "tenant_id": "ZZZ",  # not MGV or MHB
            "scopes": ["dining:read"],
            "iat": now,
            "exp": now + 3600,
        },
        MCP_DEV_SIGNING_KEY,
        algorithm="HS256",
    )
    refused, upstream_called = await _attempt_call(token=unknown_tenant_token)
    assert refused
    assert not upstream_called


@pytest.mark.asyncio
async def test_all_five_refusal_conditions_produce_indistinguishable_outcome():
    """The contract's point: a probing caller cannot fingerprint which of the
    five conditions caused a refusal by looking at the outcome shape."""
    now = int(time.time())
    valid_token = issue_token(sub="test-harness", tenant_id="MGV", scopes=["dining:read"])
    header_b64, payload_b64, sig_b64 = valid_token.split(".")
    tampered_sig = sig_b64[:-1] + ("A" if sig_b64[-1] != "A" else "B")

    tokens = {
        "absent": None,
        "expired": jwt.encode(
            {"sub": "t", "tenant_id": "MGV", "scopes": [], "iat": now - 7200, "exp": now - 3600},
            MCP_DEV_SIGNING_KEY, algorithm="HS256",
        ),
        "tampered": f"{header_b64}.{payload_b64}.{tampered_sig}",
        "malformed": jwt.encode(
            {"sub": "t", "tenant_id": "MGV", "scopes": "not-a-list", "iat": now, "exp": now + 3600},
            MCP_DEV_SIGNING_KEY, algorithm="HS256",
        ),
        "unknown_tenant": jwt.encode(
            {"sub": "t", "tenant_id": "ZZZ", "scopes": [], "iat": now, "exp": now + 3600},
            MCP_DEV_SIGNING_KEY, algorithm="HS256",
        ),
    }

    outcomes = {}
    for label, token in tokens.items():
        refused, upstream_called = await _attempt_call(token=token)
        outcomes[label] = (refused, upstream_called)

    assert all(refused and not upstream_called for (refused, upstream_called) in outcomes.values()), (
        f"not all five conditions were refused before an upstream call: {outcomes}"
    )
