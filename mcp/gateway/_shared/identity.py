"""Caller identity: verification and the dev-only issuer.

Per `specs/003-mcp-tool-layer/contracts/caller-identity-contract.md`. This is the sole
source of tenancy (FR-008) — nothing downstream ever constructs a `CallerIdentity` from a
tool argument, only from a verified bearer token's claims (FR-009, `[CRIT]`).

All five refusal conditions in the contract produce exactly one caller-visible outcome —
``IdentityError`` — deliberately not distinguishing "bad signature" from "expired" from
"unknown tenant": five different messages would let a probing caller fingerprint the
verification logic.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Final

import jwt

from gateway.settings import MCP_DEV_SIGNING_KEY

ALGORITHM: Final[str] = "HS256"
VALID_TENANTS: Final[tuple[str, ...]] = ("MGV", "MHB")
SCOPE_RE: Final[re.Pattern[str]] = re.compile(
    r"^(dining|entertainment|stay|wellness|gaming):(read|hold|commit)$"
)


class IdentityError(Exception):
    """Raised for any of the contract's five refusal conditions. Deliberately carries no
    machine-distinguishable subtype — see module docstring."""


@dataclass(frozen=True)
class CallerIdentity:
    """The verified claim set. Immutable for the lifetime of one request."""

    sub: str
    tenant_id: str
    scopes: tuple[str, ...]
    iat: int
    exp: int

    def has_scope(self, domain: str, action: str) -> bool:
        return f"{domain}:{action}" in self.scopes


def issue_token(
    sub: str, tenant_id: str, scopes: list[str], ttl_seconds: int = 3600
) -> str:
    """Dev-only issuer (research.md D2). Not a network service — a function, signing
    with `MCP_DEV_SIGNING_KEY`. Explicitly not a production identity provider."""
    now = int(time.time())
    payload = {
        "sub": sub,
        "tenant_id": tenant_id,
        "scopes": scopes,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return jwt.encode(payload, MCP_DEV_SIGNING_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> CallerIdentity:
    """Verify signature, expiry, and required-claim shape (FR-007). Raises
    ``IdentityError`` for any of the contract's five refusal conditions; never returns a
    partially-valid identity."""
    try:
        payload = jwt.decode(token, MCP_DEV_SIGNING_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise IdentityError("token failed verification") from exc

    sub = payload.get("sub")
    tenant_id = payload.get("tenant_id")
    scopes = payload.get("scopes")
    iat = payload.get("iat")
    exp = payload.get("exp")

    if not isinstance(sub, str) or not sub:
        raise IdentityError("missing or malformed sub claim")
    if tenant_id not in VALID_TENANTS:
        raise IdentityError("missing or unknown tenant_id claim")
    if not isinstance(scopes, list) or not all(
        isinstance(s, str) and SCOPE_RE.match(s) for s in scopes
    ):
        raise IdentityError("missing or malformed scopes claim")
    if not isinstance(iat, int) or not isinstance(exp, int):
        raise IdentityError("missing or malformed iat/exp claim")

    return CallerIdentity(
        sub=sub, tenant_id=tenant_id, scopes=tuple(scopes), iat=iat, exp=exp
    )


def _cli() -> None:  # pragma: no cover — operator convenience, not exercised by tests
    import argparse

    parser = argparse.ArgumentParser(prog="python -m gateway._shared.identity")
    sub = parser.add_subparsers(dest="command", required=True)
    issue = sub.add_parser("issue")
    issue.add_argument("--sub", default="test-harness")
    issue.add_argument("--tenant", required=True, choices=VALID_TENANTS)
    issue.add_argument("--scopes", required=True, help="comma-separated <domain>:<action> list")
    issue.add_argument("--ttl-seconds", type=int, default=3600)
    args = parser.parse_args()
    if args.command == "issue":
        token = issue_token(
            sub=args.sub,
            tenant_id=args.tenant,
            scopes=[s.strip() for s in args.scopes.split(",") if s.strip()],
            ttl_seconds=args.ttl_seconds,
        )
        print(token)


if __name__ == "__main__":  # pragma: no cover
    _cli()
