"""Tool registration: name-regex validation, entitlement, idempotency, tracing, error
normalization, and composite fan-out support.

One decorator, applied uniformly to every tool in every domain, so a tool cannot be
registered while skipping any of them. Composite-tool partial failure (FR-030) is built
here and proven by T063 (the only test of T024's sub_call_key mechanism).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any

from gateway._shared.entitlements import EntitlementAction, check_entitlement
from gateway._shared.errors import (
    NormalizedFailure,
    UpstreamFailure,
    build_normalized_failure,
)
from gateway._shared.idempotency import IdempotencyKeyError, validate_idempotency_key
from gateway._shared.middleware import get_current_identity
from gateway._shared.tenancy import TenantFieldViolation, assert_no_tenant_field
from gateway._shared.tracing import get_tracer
from observability.attributes import (
    IDEMPOTENCY_KEY,
    OUTCOME,
    RESORTFLOW_NAMESPACE,
    SESSION_ID,
    TENANT_ID,
)

TOOL_NAME_RE = re.compile(r"^resortflow\.(dining|entertainment|stay|wellness|gaming)\.[a-z_]+$")


@dataclass
class SubCallSpec:
    """Definition of one slot in a composite tool."""

    slot_name: str
    vendor_operation: str
    required_for_success: bool = True


class ToolRegistrationError(Exception):
    """Raised at registration time (import/startup) for any validation failure."""


def register_tool(
    *,
    domain: str,
    entitlement_action: EntitlementAction,
    is_mutating: bool,
    requires_idempotency_key: bool | None = None,
    sub_calls: list[SubCallSpec] | None = None,
) -> Callable:
    """Decorator: registers a tool with validation, entitlement check, and optional
    composite support. If `requires_idempotency_key` is not specified, it defaults to
    `is_mutating`."""

    if requires_idempotency_key is None:
        requires_idempotency_key = is_mutating

    def decorator(func: Callable) -> Callable:
        tool_name = func.__name__

        # Validate name format
        full_name = f"resortflow.{domain}.{tool_name}"
        if not TOOL_NAME_RE.match(full_name):
            raise ToolRegistrationError(
                f"tool name {full_name!r} does not match required pattern "
                f"resortflow.<domain>.<verb>_<noun>"
            )

        # Validate input model has no tenant field
        if hasattr(func, "__annotations__"):
            for param_name, param_type in func.__annotations__.items():
                if param_name in ("input_model", "self", "return"):
                    continue
                try:
                    if hasattr(param_type, "model_fields"):
                        assert_no_tenant_field(param_type, tool_name=full_name)
                except TenantFieldViolation as exc:
                    raise ToolRegistrationError(str(exc)) from exc

        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            identity = get_current_identity()
            tracer = get_tracer("gateway.tools")

            # Check entitlement
            try:
                check_entitlement(domain, entitlement_action)
            except Exception as exc:  # noqa: BLE001 — any entitlement failure is a terminal denial
                return NormalizedFailure(
                    error_class="TERMINAL",
                    domain=domain,
                    detail=f"entitlement check failed: {exc}",
                )

            # Validate idempotency key if required
            idempotency_key = kwargs.get("idempotency_key")
            if requires_idempotency_key and idempotency_key is None:
                return NormalizedFailure(
                    error_class="TERMINAL",
                    domain=domain,
                    detail="idempotency_key is required for this tool",
                )
            if requires_idempotency_key and idempotency_key is not None:
                try:
                    validate_idempotency_key(idempotency_key)
                except IdempotencyKeyError as exc:
                    return NormalizedFailure(
                        error_class="TERMINAL",
                        domain=domain,
                        detail=str(exc),
                    )

            # Emit span
            with tracer.start_as_current_span(full_name) as span:
                span.set_attribute(f"{RESORTFLOW_NAMESPACE}.node", full_name)
                span.set_attribute(f"{RESORTFLOW_NAMESPACE}.domain", domain)
                span.set_attribute(TENANT_ID, identity.tenant_id)
                # No dedicated gateway session concept exists yet (this feature never
                # threads a caller-supplied session id through the token contract) — the
                # verified caller subject is the closest stand-in available today and is
                # emitted under the frozen SESSION_ID name rather than inventing a new one.
                span.set_attribute(SESSION_ID, identity.sub)
                if idempotency_key is not None:
                    span.set_attribute(IDEMPOTENCY_KEY, idempotency_key)

                try:
                    result = await func(*args, **kwargs)
                    span.set_attribute(OUTCOME, "success")
                    return result
                except UpstreamFailure as exc:
                    span.set_attribute(OUTCOME, exc.error_class)
                    trace_id = span.get_span_context().trace_id
                    return build_normalized_failure(
                        exc,
                        idempotency_key=idempotency_key,
                        trace_id=f"{trace_id:032x}" if trace_id else None,
                    )
                except Exception:
                    span.set_attribute(OUTCOME, "ERROR")
                    raise

        wrapper.full_name = full_name  # type: ignore[attr-defined]
        wrapper.domain = domain  # type: ignore[attr-defined]
        wrapper.entitlement_action = entitlement_action  # type: ignore[attr-defined]
        wrapper.is_mutating = is_mutating  # type: ignore[attr-defined]
        wrapper.sub_calls = sub_calls or []  # type: ignore[attr-defined]
        # `@wraps(func)` above copies `func.__name__` (the bare Python name,
        # e.g. "search_events") onto `wrapper`. Every server module applies
        # `@app.tool()` *outside* this decorator with no explicit `name=`,
        # so `MCPServer.tool()` falls back to `wrapper.__name__` as the
        # actual wire-callable tool name — which, left alone, silently
        # registers every tool under its bare name instead of the frozen
        # `resortflow.<domain>.<verb>_<noun>` contract this module just validated
        # `full_name` against (`mcp-tool-contract.md`,
        # `entertainment-mcp-tool-contract.md`'s own tool inventory tables).
        # Overriding `__name__` here, in the one shared decorator every tool
        # in every domain passes through, is what actually makes the
        # contract's tool names the real ones on the wire — found via
        # `agents/tests/test_tenant_isolation.py`'s real-wire-protocol test,
        # invisible under every fake-tools-backed test because those never
        # go over MCP at all.
        wrapper.__name__ = full_name
        return wrapper

    return decorator
