"""The five deliberately divergent vendor auth schemes (research.md C2, F5).

Each is an `httpx.Auth` subclass so `upstream.py` wires exactly one line per vendor and
never branches on scheme. OAuth2 token fetch failures classify like any other upstream
failure (T020) — `VendorAuthError` carries an HTTP status so `upstream.py`'s ordinary
failure-handling path (breaker + classifier) sees it uniformly; it is never allowed to
surface as an unhandled exception or an unclassified outcome.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import httpx

from gateway import settings


class VendorAuthError(Exception):
    """A failure while establishing or refreshing vendor credentials — most commonly an
    OAuth2 token-endpoint failure. Carries an HTTP status so the caller can classify it
    exactly like any other upstream failure (F5/T020) rather than let it escape
    unclassified."""

    def __init__(self, message: str, *, http_status: int | None = None) -> None:
        super().__init__(message)
        self.http_status = http_status


class DiningKeyAuth(httpx.Auth):
    """Static `X-Dining-Key` header — the simplest of the five schemes."""

    def __init__(self, api_key: str = settings.DINING_API_KEY) -> None:
        self._api_key = api_key

    def auth_flow(self, request: httpx.Request):
        request.headers["X-Dining-Key"] = self._api_key
        yield request


class BasicAuth(httpx.Auth):
    """HTTP Basic (amenities) — the oldest/weakest scheme in the estate."""

    def __init__(
        self,
        username: str = settings.AMENITIES_USERNAME,
        password: str = settings.AMENITIES_PASSWORD,
    ) -> None:
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        self._header = f"Basic {token}"

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = self._header
        yield request


class StaticBearerAuth(httpx.Auth):
    """Static Bearer token (casino) — no token endpoint, no refresh."""

    def __init__(self, token: str = settings.CASINO_BEARER_TOKEN) -> None:
        self._token = token

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


class OAuth2ClientCredentialsAuth(httpx.Auth):
    """OAuth2 client-credentials with an in-process token cache and refresh-on-401
    (pms, ticketing). Each vendor has its **own** token endpoint (F5) — this class is
    parameterized per-vendor rather than shared global state."""

    requires_response_body = True

    def __init__(self, *, token_url: str, client_id: str, client_secret: str) -> None:
        self._token_url = token_url
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: str | None = None
        self._expires_at: float = 0.0

    async def _fetch_token(self) -> str:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    self._token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                )
        except httpx.HTTPError as exc:
            raise VendorAuthError(f"token endpoint unreachable: {exc}", http_status=None) from exc
        if response.status_code != 200:
            raise VendorAuthError(
                f"token endpoint returned {response.status_code}",
                http_status=response.status_code,
            )
        body = response.json()
        token = body.get("access_token")
        if not token:
            raise VendorAuthError("token endpoint response carried no access_token", http_status=502)
        self._token = token
        self._expires_at = time.monotonic() + float(body.get("expires_in", 3600)) - 30.0
        return token

    async def _current_token(self) -> str:
        if self._token is None or time.monotonic() >= self._expires_at:
            return await self._fetch_token()
        return self._token

    async def async_auth_flow(self, request: httpx.Request):
        token = await self._current_token()
        request.headers["Authorization"] = f"Bearer {token}"
        response = yield request
        # `response` is only a real httpx.Response when this generator is
        # driven directly by httpx's own auth machinery (pms, used
        # uncomposed). `ComposedAuth`'s `_drain` (below) drives a sub-flow
        # via a bare `async for`, which is equivalent to `.asend(None)` on
        # every step -- it never performs a real request/response
        # round-trip, so `response` is always None there. ticketing is the
        # only vendor that composes this class (`build_vendor_auth`), and
        # was the only vendor that could ever hit `None.status_code` here --
        # confirmed live: every real entertainment/ticketing call crashed
        # with AttributeError on this exact line, masked until now by a
        # separate, since-fixed scope bug that failed even earlier, at
        # gateway ingress auth. Refresh-on-401 simply cannot fire under
        # `ComposedAuth` without `_drain` becoming a full HTTP client itself
        # (a bigger redesign, not undertaken here) -- guarding against None
        # disables that one retry path for ticketing specifically, while
        # leaving pms's real refresh-on-401 (a genuine httpx response)
        # completely unaffected.
        if response is not None and response.status_code == 401:
            # Refresh-on-401: the cached token may have been invalidated upstream.
            token = await self._fetch_token()
            request.headers["Authorization"] = f"Bearer {token}"
            yield request

    def auth_flow(self, request: httpx.Request):  # pragma: no cover — sync path unused
        raise NotImplementedError("OAuth2ClientCredentialsAuth is async-only")


class HmacSignatureAuth(httpx.Auth):
    """Ticketing's extra `X-TM-Signature` burden, layered on top of whatever Bearer
    auth already applies (composed, not replacing, `OAuth2ClientCredentialsAuth`)."""

    def __init__(self, secret: str = settings.TICKETING_HMAC_SECRET) -> None:
        self._secret = secret.encode("utf-8")

    def _signature(self, raw_body: bytes) -> str:
        try:
            parsed = json.loads(raw_body) if raw_body else {}
        except ValueError:
            canonical = raw_body
        else:
            canonical = json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hmac.new(self._secret, canonical, hashlib.sha256).hexdigest()

    def auth_flow(self, request: httpx.Request):
        request.headers["X-TM-Signature"] = self._signature(request.content or b"")
        yield request


class ComposedAuth(httpx.Auth):
    """Applies two `httpx.Auth` instances in sequence (ticketing needs both OAuth2
    Bearer and HMAC signing)."""

    def __init__(self, *auths: httpx.Auth) -> None:
        self._auths = auths

    async def async_auth_flow(self, request: httpx.Request):
        for auth in self._auths:
            flow = auth.async_auth_flow(request) if hasattr(auth, "async_auth_flow") else _wrap_sync(auth, request)
            request = await _drain(flow, request)
        yield request


def _wrap_sync(auth: httpx.Auth, request: httpx.Request):
    async def _gen():
        for req in auth.auth_flow(request):
            yield req

    return _gen()


async def _drain(flow, fallback: httpx.Request) -> httpx.Request:
    result = fallback
    async for req in flow:
        result = req
    return result


def build_vendor_auth(vendor: str) -> httpx.Auth:
    """One call site, keyed by vendor name, so `upstream.py` never branches on scheme."""
    if vendor == "dining":
        return DiningKeyAuth()
    if vendor == "amenities":
        return BasicAuth()
    if vendor == "casino":
        return StaticBearerAuth()
    if vendor == "pms":
        return OAuth2ClientCredentialsAuth(
            token_url=f"{settings.PMS_SIM_URL}/pms/oauth2/token",
            client_id=settings.PMS_CLIENT_ID,
            client_secret=settings.PMS_CLIENT_SECRET,
        )
    if vendor == "ticketing":
        oauth = OAuth2ClientCredentialsAuth(
            token_url=f"{settings.TICKETING_SIM_URL}/oauth/token",
            client_id=settings.TICKETING_CLIENT_ID,
            client_secret=settings.TICKETING_CLIENT_SECRET,
        )
        return ComposedAuth(oauth, HmacSignatureAuth())
    raise ValueError(f"unknown vendor {vendor!r}")
