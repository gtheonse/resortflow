"""The single auth chokepoint ahead of all five mounted domain servers (research.md D2).

Verifies the bearer token exactly once, before any tool body runs, and publishes the
resulting `CallerIdentity` on a `contextvars.ContextVar` that `entitlements.py` and
`tenancy.py`-consuming code both read. This is what makes "tenant-crossing call
impossible by construction" a single provable chokepoint (SC-003/SC-004) rather than five
independent per-domain checks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from gateway._shared.identity import CallerIdentity, IdentityError, verify_token

_current_identity: ContextVar[CallerIdentity | None] = ContextVar(
    "current_identity", default=None
)

_current_sim_fault: ContextVar[str | None] = ContextVar("current_sim_fault", default=None)


def get_current_identity() -> CallerIdentity:
    """Read the identity the middleware published for this request. Raises if called
    outside a request the middleware has processed — there is no silent default."""
    identity = _current_identity.get()
    if identity is None:
        raise RuntimeError("no CallerIdentity published for this request")
    return identity


def get_current_sim_fault() -> str | None:
    """Read the `X-Sim-Fault` value this request's caller asked to inject, if
    any (T042/`graph/demo.py`'s `--inject-failure`). `None` — the common
    case — means no fault requested; `_shared/upstream.py`'s `UpstreamClient`
    forwards this verbatim as the same header onto the vendor sim call
    (`sims/_shared/faults.py`'s own middleware reads it from *its* incoming
    request, which is this gateway's outgoing one)."""
    return _current_sim_fault.get()


REFUSED_BODY = {
    "error_class": "TERMINAL",
    "domain": "gateway",
    "detail": "request refused: missing, invalid, or expired credential",
}


class AuthenticationMiddleware(BaseHTTPMiddleware):
    """Verifies `Authorization: Bearer <token>` once, ahead of every mounted sub-app.
    A request failing any of the contract's five conditions is refused here — the tool
    body never runs, and no upstream call is ever made (SC-004)."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(status_code=401, content=REFUSED_BODY)
        token = auth_header[len("Bearer ") :]
        try:
            identity = verify_token(token)
        except IdentityError:
            return JSONResponse(status_code=401, content=REFUSED_BODY)

        reset_token = _current_identity.set(identity)
        fault_reset_token = _current_sim_fault.set(request.headers.get("X-Sim-Fault"))
        try:
            return await call_next(request)
        finally:
            _current_identity.reset(reset_token)
            _current_sim_fault.reset(fault_reset_token)
