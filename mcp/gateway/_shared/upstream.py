"""The shared upstream HTTP client: one `httpx.AsyncClient` per vendor, wired through
tenancy injection, vendor auth, retry, the circuit breaker, and response validation — so
no domain module ever calls `httpx` directly (Principle II, ADR-005).
"""

from __future__ import annotations

import json
from typing import TypeVar

import httpx
from pydantic import BaseModel

from gateway import settings
from gateway._shared import errors, idempotency, middleware, resilience, tenancy, vendor_auth

M = TypeVar("M", bound=BaseModel)

DOMAIN_TO_VENDOR: dict[str, str] = {
    "dining": "dining",
    "entertainment": "ticketing",
    "stay": "pms",
    "wellness": "amenities",
    "gaming": "casino",
}

VENDOR_BASE_URL: dict[str, str] = {
    "dining": settings.DINING_SIM_URL,
    "ticketing": settings.TICKETING_SIM_URL,
    "pms": settings.PMS_SIM_URL,
    "amenities": settings.AMENITIES_SIM_URL,
    "casino": settings.CASINO_SIM_URL,
}


def _extract_vendor_code(raw_body: bytes) -> str | None:
    """Best-effort extraction across the three vendor envelope shapes this project
    freezes (`specs/001-contracts-foundation/data-model.md`): dining's
    ``{"error": {"code": ...}}``, ticketing's ``{"fault": {"type": ...}}``, and
    amenities' flat ``{"err_code": ...}``. pms and casino publish no code at all —
    returns `None` for those, which is exactly what `errors.classify_status` expects."""
    try:
        parsed = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    detail = parsed.get("detail")
    if isinstance(detail, dict):
        parsed = detail
    error = parsed.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    fault = parsed.get("fault")
    if isinstance(fault, dict) and isinstance(fault.get("type"), str):
        return fault["type"]
    err_code = parsed.get("err_code")
    if isinstance(err_code, str):
        return err_code
    return None


def _extract_retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


class UpstreamClient:
    """One instance per domain (five total), each owning its own `httpx.AsyncClient`
    and `CircuitBreaker`."""

    def __init__(self, *, domain: str) -> None:
        self.domain = domain
        self.vendor = DOMAIN_TO_VENDOR[domain]
        self.breaker = resilience.CircuitBreaker(upstream=self.vendor)
        self._client = httpx.AsyncClient(
            base_url=VENDOR_BASE_URL[self.vendor],
            auth=vendor_auth.build_vendor_auth(self.vendor),
            timeout=10.0,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _recreate_client_for_tests(self) -> None:
        """Test-only escape hatch (`mcp/tests/integration/test_policy_blocked.py`,
        `test_error_taxonomy.py`): replaces this instance's `httpx.AsyncClient` with a
        fresh one bound to whichever event loop is current when this is called.

        `UpstreamClient` instances are process-lifetime singletons (one per domain,
        held by each `gateway.<domain>.server` module) shared across every test file in
        the session, but `httpx.AsyncClient`'s underlying connection pool binds to the
        event loop that first uses it. A test that drives a *real* HTTP round-trip
        in-process (bypassing the MCP wire transport's own fresh-client-per-call
        pattern, per those files' docstrings) under pytest-asyncio's default
        function-scoped loop leaves a pool bound to a loop that gets torn down at the
        end of that test — the next test (or worse, the next test *file*) reusing the
        same singleton then dies with "RuntimeError: Event loop is closed" trying to
        close/reuse that stale pooled connection. Recreating the client fresh before
        (and closing it after) each such test keeps every real connection scoped to
        the one event loop that was alive when it was opened.
        """
        self._client = httpx.AsyncClient(
            base_url=VENDOR_BASE_URL[self.vendor],
            auth=vendor_auth.build_vendor_auth(self.vendor),
            timeout=10.0,
        )

    async def call(
        self,
        method: str,
        path: str,
        *,
        tenant_id: str,
        response_model: type[M],
        params: dict | None = None,
        json_body: dict | None = None,
        idempotency_key: str | None = None,
        is_mutating: bool = False,
    ) -> M:
        """Make one upstream call. Raises `errors.UpstreamFailure` on any failure,
        fully classified — the caller (`registry.py`) never sees a raw `httpx`
        exception or an unclassified outcome."""
        headers = tenancy.tenant_headers(tenant_id)
        if idempotency_key is not None:
            headers.update(idempotency.vendor_header(idempotency_key))
        sim_fault = middleware.get_current_sim_fault()
        if sim_fault is not None:
            # T042: forwards the caller's requested fault (via `graph/demo.py`'s
            # `--inject-failure`/`--force-unavailable`, propagated by
            # `AuthenticationMiddleware` from this gateway's own incoming
            # request) onto the vendor sim call, where `sims/_shared/faults.py`'s
            # identical `X-Sim-Fault` middleware reads it.
            headers["X-Sim-Fault"] = sim_fault

        # httpx serializes a None-valued dict entry as an empty-string query
        # param (`key=`), not an omitted key -- found live (feature 008):
        # mcp/gateway/entertainment/server.py's search_events passes
        # `classification=input.classification` (None when the guest didn't
        # ask for one), which arrived at sims/ticketing/main.py as the
        # literal empty string. That sim's own filter
        # (`if classification is not None and r["classification"] !=
        # classification`) then excluded every real event, since "" never
        # equals a real classification like "concert" -- zero results with
        # no error anywhere, on the very first real optional-param call any
        # domain has made through this shared client. Filtering None values
        # out here fixes it for every domain, not just entertainment.
        clean_params = {k: v for k, v in (params or {}).items() if v is not None} if params else None

        async def _attempt() -> M:
            self.breaker.before_call()
            try:
                response = await self._client.request(
                    method, path, params=clean_params, json=json_body, headers=headers
                )
            except vendor_auth.VendorAuthError as exc:
                self.breaker.record_failure()
                error_class = errors.classify_status(
                    exc.http_status or 503, domain=self.domain
                )
                if error_class == "RETRYABLE":
                    raise resilience.RetryableUpstreamError(str(exc)) from exc
                raise errors.UpstreamFailure(
                    error_class=error_class, domain=self.domain, detail=str(exc),
                    http_status=exc.http_status,
                ) from exc
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                self.breaker.record_failure()
                raise resilience.RetryableUpstreamError(f"transport error: {exc}") from exc

            if response.status_code < 400:
                body = errors.classify_body(response.content, model_cls=response_model)
                if body.error_class is None:
                    self.breaker.record_success()
                    assert body.model is not None
                    return body.model  # type: ignore[return-value]
                # Schema-invalid/unparseable 2xx body: counts as a breaker failure
                # identically to a 5xx (FR-038).
                self.breaker.record_failure()
                if body.error_class == "RETRYABLE":
                    raise resilience.RetryableUpstreamError(body.detail)
                raise errors.UpstreamFailure(
                    error_class="TERMINAL", domain=self.domain, detail=body.detail,
                    http_status=response.status_code,
                    effect_status="unresolved" if is_mutating else None,
                )

            vendor_code = _extract_vendor_code(response.content)
            error_class = errors.classify_status(
                response.status_code, domain=self.domain, vendor_code=vendor_code
            )
            if error_class == "RETRYABLE":
                self.breaker.record_failure()
                raise resilience.RetryableUpstreamError(
                    f"{self.vendor} returned {response.status_code}",
                    retry_after_seconds=_extract_retry_after(response),
                )
            # A determinate business rejection (TERMINAL/REQUIRES_HUMAN/POLICY_BLOCKED)
            # is not a vendor-health signal — the vendor answered correctly, it just
            # said no. Does not trip the breaker.
            self.breaker.record_success()
            raise errors.UpstreamFailure(
                error_class=error_class,
                domain=self.domain,
                detail=f"{self.vendor} rejected the request ({response.status_code})",
                vendor_code=vendor_code,
                http_status=response.status_code,
            )

        try:
            return await resilience.retry_with_jitter(_attempt)
        except resilience.BreakerOpen as exc:
            if is_mutating:
                raise errors.UpstreamFailure(
                    error_class="REQUIRES_HUMAN", domain=self.domain, detail=str(exc),
                    effect_status="unresolved",
                ) from exc
            raise errors.UpstreamFailure(
                error_class="RETRYABLE", domain=self.domain, detail=str(exc)
            ) from exc
        except resilience.RetryableUpstreamError as exc:
            # All retry attempts exhausted still RETRYABLE. For a mutating call this is
            # exactly FR-032's indeterminate-effect case: we cannot rule out an earlier
            # attempt having landed vendor-side.
            if is_mutating:
                raise errors.UpstreamFailure(
                    error_class="REQUIRES_HUMAN", domain=self.domain, detail=str(exc),
                    effect_status="unresolved",
                ) from exc
            raise errors.UpstreamFailure(
                error_class="RETRYABLE", domain=self.domain, detail=str(exc),
                retry_after_seconds=exc.retry_after_seconds,
            ) from exc
