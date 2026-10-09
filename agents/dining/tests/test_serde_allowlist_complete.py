"""Every enum or model a checkpointed model refers to must be on the msgpack allowlist (Constitution V: no pickle
fallback, nothing blocked on resume).

Found by a user's console: `Blocked deserialization of graph.errors.ErrorClass - not in allowed_msgpack_modules`.
`ToolFailure` was allowlisted but the `ErrorClass` enum it carries lives in `graph/errors.py`, not `graph/models.py`, so it
was missed — the allowlist had been maintained by hand, one feature at a time. This walks the field annotations of every
allowlisted model so the next omission fails a test instead of a conversation."""

from __future__ import annotations

import types
import typing
from enum import Enum

from pydantic import BaseModel

from agents.dining.serde import _ALLOWED_MSGPACK_TYPES


def _referenced_types(annotation: object) -> set[type]:
    found: set[type] = set()
    stack = [annotation]
    while stack:
        item = stack.pop()
        if isinstance(item, type) and (issubclass(item, Enum) or issubclass(item, BaseModel)):
            found.add(item)
        origin = typing.get_origin(item)
        if origin is typing.Annotated:
            stack.append(typing.get_args(item)[0])
        elif origin is not None or isinstance(item, types.UnionType):
            stack.extend(typing.get_args(item))
    return found


def test_every_type_referenced_by_an_allowlisted_model_is_itself_allowlisted() -> None:
    allowed = set(_ALLOWED_MSGPACK_TYPES)
    missing: set[type] = set()
    for model in (t for t in allowed if isinstance(t, type) and issubclass(t, BaseModel)):
        hints = typing.get_type_hints(model)
        for annotation in hints.values():
            missing |= _referenced_types(annotation) - allowed
    assert not missing, "not on the checkpoint allowlist: " + ", ".join(sorted(f"{t.__module__}.{t.__name__}" for t in missing))


def test_a_checkpointed_tool_failure_round_trips_without_anything_being_blocked(caplog) -> None:
    import logging
    import os

    from agents.dining.serde import EncryptedSerializer, plain_serde
    from graph.errors import ErrorClass
    from graph.models import Domain, ToolFailure

    failure = ToolFailure(
        domain=Domain.DINING, tool="hold_table", error_class=ErrorClass.RETRYABLE, vendor_code="X", http_status=503,
        detail="vendor 503",
    )
    for serializer in (plain_serde(), EncryptedSerializer(os.urandom(32))):
        with caplog.at_level(logging.DEBUG):
            back = serializer.loads_typed(serializer.dumps_typed({"failures": [failure]}))
        assert back["failures"][0] == failure and isinstance(back["failures"][0].error_class, ErrorClass)
    assert "Blocked deserialization" not in caplog.text
