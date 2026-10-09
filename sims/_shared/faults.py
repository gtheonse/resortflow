"""Fault-injection middleware: `X-Sim-Fault` / `X-Sim-Latency` per
`specs/002-simulation-ecosystem/contracts/sim-control-plane.md`. Deliberately outside
every sim's frozen OpenAPI contract (ADR-008) — implemented as ordinary middleware that
every HTTP framework accepts unconditionally regardless of whether OpenAPI declares it.

Two of the six fault values (`hold_expired`, `inventory_race`) and `webhook_delay` are
business-logic faults this middleware cannot honor by itself — it validates the header
and stashes the value on `request.state.sim_fault` for the handling endpoint (which
already accepts a ``force_expired`` flag on `sims/_shared/holds.py`'s confirm/release,
or forces its own 409/delayed-webhook path) to read. `timeout`, `error_500`, and
`rate_limit` are generic enough to be handled entirely here, short-circuiting before the
route (and before auth) — the same place a real load balancer or API gateway's chaos
injection would sit.

`schema_drift` (T071, SC-016) is a seventh, differently-shaped fault: rather than
short-circuiting *before* the route like `timeout`/`error_500`/`rate_limit`, it lets the
real handler run to completion and then corrupts its *successful* (`<400`) JSON response
on the way out — deleting one top-level field the sim's own OpenAPI contract declares
required and injecting one it does not declare at all, so the body is simultaneously
missing-a-required-field and extra-field-invalid against that endpoint's schema
regardless of which endpoint was hit. This is what lets a test drive a genuinely
schema-invalid 2xx body out of a live sim (rather than only unit-testing
`gateway._shared.errors.classify_body`'s `ValidationError` branch against a
hand-constructed payload) to prove the MCP gateway's error-classification layer turns it
into a specific, distinct, non-`RETRYABLE` failure instead of crashing or passing the
malformed body through to the caller.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

ALL_FAULT_VALUES = {
    "timeout",
    "error_500",
    "rate_limit",
    "hold_expired",
    "inventory_race",
    "webhook_delay",
    "schema_drift",
}
ALL_LATENCY_VALUES = {"p50", "p95", "p99"}

# Milliseconds per latency profile step — an implementation/config detail left open by
# `sim-control-plane.md` ("an implementation/config detail set in tasks.md, not fixed by
# this contract"). Uniform across sims for simplicity; nothing in the spec requires
# per-sim variance.
LATENCY_PROFILE_SECONDS = {"p50": 0.05, "p95": 0.2, "p99": 0.5}

# How long the `timeout` fault sleeps before finally responding. Genuinely hanging
# forever would make this feature's own test suite (and any caller without a
# short client-side timeout) hang too; sleeping past a realistic client timeout
# threshold and then still returning is what makes this fault reproducible and
# testable without needing to actually block a worker forever.
TIMEOUT_FAULT_SECONDS = 3.0

EnvelopeFn = Callable[[str, str], dict]


def active_fault(request: Request) -> str | None:
    """Read the business-logic fault (``hold_expired`` / ``inventory_race`` /
    ``webhook_delay``) the middleware stashed on this request, if any. Endpoints call
    this rather than reading ``request.state.sim_fault`` directly, since an unset
    ``state`` attribute raises ``AttributeError`` rather than returning ``None``."""
    return getattr(request.state, "sim_fault", None)


def _generic_envelope(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def add_fault_middleware(
    app: object,
    *,
    sim_name: str,
    supported_faults: set[str],
    envelope_fn: EnvelopeFn = _generic_envelope,
) -> None:
    """Register the fault-injection middleware on a sim's FastAPI app (US2, T023).
    ``supported_faults`` is the subset of ``ALL_FAULT_VALUES`` this sim's contract
    documents (e.g. amenities/pms support `hold_expired`/`inventory_race` but not
    `webhook_delay`, which is ticketing-only)."""

    class FaultInjectionMiddleware(BaseHTTPMiddleware):
        async def dispatch(
            self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
        ) -> Response:
            fault_value = request.headers.get("X-Sim-Fault")
            latency_value = request.headers.get("X-Sim-Latency")

            if fault_value is not None and (
                fault_value not in ALL_FAULT_VALUES or fault_value not in supported_faults
            ):
                return JSONResponse(
                    status_code=400,
                    content=envelope_fn(
                        "INVALID_FAULT",
                        f"X-Sim-Fault={fault_value!r} is not recognized or does not apply to {sim_name}-sim",
                    ),
                )

            if latency_value is not None and latency_value not in ALL_LATENCY_VALUES:
                return JSONResponse(
                    status_code=400,
                    content=envelope_fn(
                        "INVALID_LATENCY", f"X-Sim-Latency={latency_value!r} is not a recognized profile"
                    ),
                )

            if latency_value is not None:
                await asyncio.sleep(LATENCY_PROFILE_SECONDS[latency_value])

            if fault_value == "timeout":
                await asyncio.sleep(TIMEOUT_FAULT_SECONDS)
                return JSONResponse(
                    status_code=504, content=envelope_fn("UPSTREAM_TIMEOUT", "simulated timeout")
                )
            if fault_value == "error_500":
                return JSONResponse(
                    status_code=500, content=envelope_fn("UPSTREAM_ERROR", "simulated server error")
                )
            if fault_value == "rate_limit":
                return JSONResponse(
                    status_code=429,
                    content=envelope_fn("RATE_LIMITED", "simulated rate limit"),
                    headers={"Retry-After": "1"},
                )

            if fault_value == "schema_drift":
                response = await call_next(request)
                return await _drift_response_schema(response)

            # hold_expired / inventory_race / webhook_delay: business-logic faults,
            # handled by the endpoint via request.state.sim_fault.
            request.state.sim_fault = fault_value
            return await call_next(request)

    app.add_middleware(FaultInjectionMiddleware)  # type: ignore[attr-defined]


async def _drift_response_schema(response: Response) -> Response:
    """`schema_drift` post-processing (T071): only touches successful, JSON, non-empty
    object bodies — an already-failing response is left alone, since that path is
    already covered by `error_500`/vendor-native error envelopes and drifting *those*
    would conflate two different fault modes in one response."""
    if response.status_code >= 400:
        return response
    content_type = response.headers.get("content-type", "")
    if "application/json" not in content_type:
        return response

    body = b"".join([section async for section in response.body_iterator])  # type: ignore[attr-defined]
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return Response(
            content=body,
            status_code=response.status_code,
            media_type=content_type,
        )
    if not isinstance(parsed, dict) or not parsed:
        return Response(content=body, status_code=response.status_code, media_type=content_type)

    drifted = dict(parsed)
    # Delete one top-level field the real schema declares required...
    dropped_key = next(iter(drifted))
    del drifted[dropped_key]
    # ...and add one it declares nowhere, with a type no field of that name would ever
    # legitimately have -- doubly schema-invalid regardless of which field survives a
    # future contract change to whichever endpoint was actually hit.
    drifted["__sim_schema_drift__"] = {"dropped_field": dropped_key, "unexpected": True}

    return JSONResponse(content=drifted, status_code=response.status_code)
