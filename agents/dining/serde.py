"""T054 — `EncryptedSerializer`, per AGENTS.md's tech-stack line
("Checkpointer: Postgres (`langgraph-checkpoint-postgres`),
`EncryptedSerializer`").

Wraps LangGraph's default `JsonPlusSerializer` and encrypts the resulting
bytes with AES-256-GCM before they ever reach Postgres. Checkpoints of
`DiningState` carry `Sensitivity.HEALTH`-marked fields (`DietaryRequirement`
via `Proposal.dietary`) and `Sensitivity.PII` fields (`Guest`, `Hold`'s
guest-adjacent data) — encrypting the whole checkpoint blob at rest is
simpler and strictly safer than field-level redaction, since a new
sensitive field added later is protected automatically rather than only
once someone remembers to annotate a redaction rule for it.

Key management: `LANGGRAPH_AES_KEY` (`.env`/`.env.example`), a
base64-encoded 32-byte key (`openssl rand -base64 32`, per
`.env.example`'s own comment). No key configured -> `EncryptedSerializer`
refuses to construct rather than silently writing plaintext — a durability
feature that silently drops its own guarantee is worse than one that fails
loudly at startup.
"""

from __future__ import annotations

import base64
import os
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from graph.errors import ErrorClass
from graph.models import (
    BrowseFilters,
    Chunk,
    Citation,
    Confirmation,
    DateWindow,
    DietaryRequirement,
    DietaryRestriction,
    DietarySeverity,
    Domain,
    Guest,
    Hold,
    HoldStatus,
    Plan,
    PlanTask,
    PolicyFlag,
    Proposal,
    RequestKind,
    RoutingDecision,
    RoutingTier,
    SagaEvent,
    Sensitivity,
    Stay,
    Tier2Outcome,
    ToolFailure,
    TurnBaseline,
)

_NONCE_LEN = 12  # AES-GCM standard nonce length

# Every `graph.models` type that can appear inside a checkpointed
# `DiningState` (frozen contract — data-model.md's "ResortFlowState — the frozen
# key set"). `JsonPlusSerializer`'s allowlist matches on exact
# `(module, qualname)` pairs, not module prefixes, so each type is listed
# individually rather than allow-listing the whole module.
_ALLOWED_MSGPACK_TYPES: tuple[type, ...] = (
    Chunk, Citation, Confirmation, DietaryRequirement, DietaryRestriction,
    DietarySeverity, Domain, Guest, Hold, HoldStatus, Plan, PlanTask,
    PolicyFlag, Proposal, SagaEvent, Sensitivity, Stay, ToolFailure,
    # Feature 010 (ADR-056, state-schema-amendment-010.md): routing/
    # turn_baseline's carried types. Missing any of these breaks resume —
    # the same reasoning as every type already above.
    RequestKind, RoutingDecision, RoutingTier, Tier2Outcome, TurnBaseline,
    # Feature 011 (ADR-060, state-schema-amendment-011.md): the browse fields RoutingDecision now carries.
    # Missing either breaks resume of a browse decision.
    BrowseFilters, DateWindow,
    # `ToolFailure.error_class`: the enum lives in graph/errors.py, not graph/models.py, so it was missed when ToolFailure
    # was listed — found by a console warning, now guarded by test_serde_allowlist_complete.py.
    ErrorClass,
)


class EncryptedSerializerConfigError(Exception):
    """Raised at construction time when no usable key is configured."""


class EncryptedSerializer(SerializerProtocol):
    """AES-256-GCM envelope around `JsonPlusSerializer`. The wire format is
    `nonce(12 bytes) || ciphertext+tag`; the inner `type` tag from
    `JsonPlusSerializer.dumps_typed` is preserved so `loads_typed` can
    reconstruct the original Python object exactly."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise EncryptedSerializerConfigError(
                f"AES-256-GCM requires a 32-byte key, got {len(key)} bytes"
            )
        self._aead = AESGCM(key)
        # `graph.models` is the frozen contract module every dining
        # checkpoint's `Proposal`/`Hold`/`Confirmation`/`SagaEvent`/
        # `DietaryRequirement`/... instances come from — explicitly
        # allow-listed so a future LangGraph release that defaults
        # `LANGGRAPH_STRICT_MSGPACK` to block unregistered types doesn't
        # silently break checkpoint round-tripping.
        self._inner = JsonPlusSerializer(allowed_msgpack_modules=list(_ALLOWED_MSGPACK_TYPES))

    @classmethod
    def from_env(cls, env_var: str = "LANGGRAPH_AES_KEY") -> EncryptedSerializer:
        raw = os.environ.get(env_var, "")
        if not raw:
            raise EncryptedSerializerConfigError(
                f"{env_var} is not set — refusing to construct an "
                "EncryptedSerializer that would silently write plaintext "
                "checkpoints. Generate one with `openssl rand -base64 32`."
            )
        try:
            key = base64.b64decode(raw, validate=True)
        except Exception as exc:
            raise EncryptedSerializerConfigError(
                f"{env_var} is not valid base64: {exc}"
            ) from exc
        return cls(key)

    def dumps_typed(self, obj: Any) -> tuple[str, bytes]:
        type_tag, plaintext = self._inner.dumps_typed(obj)
        nonce = os.urandom(_NONCE_LEN)
        ciphertext = self._aead.encrypt(nonce, plaintext, associated_data=type_tag.encode("utf-8"))
        return type_tag, nonce + ciphertext

    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        type_tag, blob = data
        nonce, ciphertext = blob[:_NONCE_LEN], blob[_NONCE_LEN:]
        plaintext = self._aead.decrypt(nonce, ciphertext, associated_data=type_tag.encode("utf-8"))
        return self._inner.loads_typed((type_tag, plaintext))


def plain_serde() -> JsonPlusSerializer:
    """An unencrypted `JsonPlusSerializer` with the same `graph.models`
    allowlist as `EncryptedSerializer` — for `MemorySaver`-backed tests/eval
    runs that don't need encryption but should still exercise (and not spam
    warnings about) the same allowlisted-type path production checkpoints
    use."""
    return JsonPlusSerializer(allowed_msgpack_modules=list(_ALLOWED_MSGPACK_TYPES))
