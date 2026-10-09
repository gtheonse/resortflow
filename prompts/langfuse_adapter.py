"""T032 -- the ONLY module in the repository that imports `langfuse` (Principle II; ADR-053; enforced by
tests/test_langfuse_import_boundary.py).

* The SDK client is built with `tracing_enabled=False`. Constructed normally it would start its own tracer
  provider and attach a span processor, double-exporting the spans ADR-051's exporter already sends. With
  tracing off, the project's own exporter stays the only exporter (FR-031, test_adapter_tracing_disabled.py).
* An EXPLICIT `LANGFUSE_HOST` (or `LANGFUSE_BASE_URL`) and both keys are required. The SDK's default host is
  Langfuse Cloud, which must never be reached by accident: if any is unset no client is constructed and the
  result is a typed `no_credentials` error, which the registry treats as an unreachable registry.
* The adapter NEVER raises to its caller: every failure becomes an `AdapterError` so a registry outage can
  never fail a guest turn (the registry falls back to the version-controlled copy).
* Caching is owned by the SDK. The adapter only passes the configured TTL and timeout through; the project
  adds no second cache (FR-006).
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

_logger = logging.getLogger(__name__)

_CHAT_ROLES = frozenset({"system", "user"})


@dataclass(frozen=True)
class RegistryPrompt:
    """One registry chat prompt version, reduced to what the registry needs."""

    name: str
    version: int
    messages: list[dict[str, str]]
    config: dict[str, Any]
    labels: list[str]


@dataclass(frozen=True)
class AdapterError:
    """A typed failure: `fetch_error` (timeout, HTTP, SDK error), `wrong_shape` (not a valid chat prompt) or
    `no_credentials` (host or keys unset)."""

    reason: str


def _default_client_factory(**kwargs: Any) -> Any:
    from langfuse import Langfuse  # the single langfuse import in the repository

    return Langfuse(**kwargs)


class LangfuseAdapter:
    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._env: Mapping[str, str] = os.environ if env is None else env
        self._factory: Callable[..., Any] = client_factory or _default_client_factory
        self._client: Any | None = None
        self._lock = threading.Lock()

    def _credentials(self) -> tuple[str, str, str] | None:
        public = self._env.get("LANGFUSE_PUBLIC_KEY", "").strip()
        secret = self._env.get("LANGFUSE_SECRET_KEY", "").strip()
        host = (self._env.get("LANGFUSE_HOST", "") or self._env.get("LANGFUSE_BASE_URL", "")).strip()
        if not (public and secret and host):
            return None
        return public, secret, host

    def client(self) -> Any | None:
        """The SDK client, constructed lazily and once; `None` when credentials or host are absent."""
        creds = self._credentials()
        if creds is None:
            return None
        with self._lock:
            if self._client is None:
                public, secret, host = creds
                self._client = self._factory(
                    public_key=public, secret_key=secret, host=host, tracing_enabled=False
                )
            return self._client

    def fetch_prompt(self, name: str, label: str, timeout: int, cache_ttl: int) -> RegistryPrompt | AdapterError:
        try:
            client = self.client()
        except Exception:  # an SDK construction failure is a registry outage, never a turn failure
            _logger.warning("prompt registry client could not be constructed", exc_info=True)
            return AdapterError("fetch_error")
        if client is None:
            return AdapterError("no_credentials")
        try:
            fetched = client.get_prompt(
                name,
                label=label,
                type="chat",
                cache_ttl_seconds=cache_ttl,
                fetch_timeout_seconds=timeout,
            )
        except Exception:  # timeout, 404, connection error: all are just "registry unavailable"
            _logger.warning("prompt registry fetch failed for %s@%s", name, label, exc_info=True)
            return AdapterError("fetch_error")
        return self._to_registry_prompt(name, fetched)

    @staticmethod
    def _to_registry_prompt(name: str, fetched: Any) -> RegistryPrompt | AdapterError:
        raw_messages = getattr(fetched, "prompt", None)
        if not isinstance(raw_messages, list) or not raw_messages:
            return AdapterError("wrong_shape")  # a text prompt (str) or an empty chat prompt
        messages: list[dict[str, str]] = []
        for item in raw_messages:
            if not isinstance(item, Mapping):
                return AdapterError("wrong_shape")
            kind = item.get("type", "chatmessage")
            role, content = item.get("role"), item.get("content")
            if kind != "chatmessage" or role not in _CHAT_ROLES or not isinstance(content, str):
                return AdapterError("wrong_shape")  # placeholders and non system/user roles are not supported
            messages.append({"role": str(role), "content": content})
        config = getattr(fetched, "config", None)
        version = getattr(fetched, "version", None)
        labels = getattr(fetched, "labels", None)
        if not isinstance(config, dict) or isinstance(version, bool) or not isinstance(version, int):
            return AdapterError("wrong_shape")
        return RegistryPrompt(
            name=name,
            version=version,
            messages=messages,
            config=dict(config),
            labels=[str(label) for label in labels] if isinstance(labels, list) else [],
        )
