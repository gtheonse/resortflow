"""Tenant validation and per-tenant SQLite scoping shared by all five sims.

See ``specs/002-simulation-ecosystem/contracts/sim-control-plane.md`` for the broader
test/ops contract this module sits alongside, and ``data-model.md`` for the Tenant
entity definition (not a stored row — a value validated on every request via
``X-Tenant-Id``).
"""

from __future__ import annotations

from fastapi import Header, HTTPException

VALID_TENANTS = ("MGV", "MHB")


async def require_tenant_id(x_tenant_id: str | None = Header(default=None, alias="X-Tenant-Id")) -> str:
    """FastAPI dependency: validate ``X-Tenant-Id`` against {MGV, MHB}.

    Returns the validated tenant id. Raises 400 if the header is missing or carries
    any value outside the two known tenants — per spec.md's edge case "request omits
    tenant scoping information entirely" and FR-010's tenant-scoping requirement.
    """
    if x_tenant_id is None or x_tenant_id not in VALID_TENANTS:
        raise HTTPException(
            status_code=400,
            detail=f"X-Tenant-Id must be one of {VALID_TENANTS}, got {x_tenant_id!r}",
        )
    return x_tenant_id
