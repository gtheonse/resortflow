"""Out-of-band reset-to-seeded-baseline endpoint.

``POST /__sim__/reset`` per ``specs/002-simulation-ecosystem/contracts/sim-control-plane.md``.
Deliberately outside every sim's frozen OpenAPI surface (ADR-008) — no production vendor
exposes a "wipe your booking history" endpoint, so this lives under a path prefix the
contract-conformance suite (FR-011) never exercises.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import APIRouter

ResetFn = Callable[[], Awaitable[None]]

TENANT_IDS = ["MGV", "MHB"]


def build_reset_router(reset_fn: ResetFn) -> APIRouter:
    router = APIRouter()

    @router.post("/__sim__/reset")
    async def reset() -> dict:
        await reset_fn()
        return {"reset": True, "tenant_ids": TENANT_IDS}

    return router
