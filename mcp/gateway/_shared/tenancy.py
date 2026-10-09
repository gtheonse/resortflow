"""Server-side tenant injection (FR-009, `[CRIT]`).

Every upstream request gets `X-Tenant-Id` from the verified `CallerIdentity` only — never
from a tool argument. Any tool input model carrying a tenant field is rejected at schema
level by `assert_no_tenant_field`, called once per registered tool at import time so the
violation fails at startup, not at request time.
"""

from __future__ import annotations

from typing import get_type_hints

from pydantic import BaseModel

# Any tool input field named one of these is a caller attempting to supply tenancy
# itself (FR-009) — rejected at registration time, not silently ignored at request time.
FORBIDDEN_FIELD_NAMES = frozenset({"tenant_id", "tenant", "x_tenant_id"})


class TenantFieldViolation(Exception):
    """Raised at tool-registration time when an input model declares a tenant field."""


def assert_no_tenant_field(model: type[BaseModel], *, tool_name: str) -> None:
    fields = set(get_type_hints(model, include_extras=True))
    offending = fields & FORBIDDEN_FIELD_NAMES
    if offending:
        raise TenantFieldViolation(
            f"tool {tool_name!r} input model {model.__name__} declares forbidden "
            f"tenant field(s) {sorted(offending)} — tenant_id must come from "
            f"CallerIdentity only (FR-009)"
        )


def tenant_headers(tenant_id: str) -> dict[str, str]:
    """The one place an upstream request's tenant header is constructed."""
    return {"X-Tenant-Id": tenant_id}
