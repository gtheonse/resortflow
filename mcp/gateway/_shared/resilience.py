"""Retry-with-jitter and the per-upstream circuit breaker (research.md D5).

Both are `[CRIT]`-adjacent: FR-031 requires uniform breaker blocking with **no**
idempotency-key exemption, and getting that wrong turns "one sick vendor" into "the whole
platform is sick" (SC-010). The breaker is hand-rolled rather than pulled from a library
because its contract is narrow and exactly specified — a ~60-line state machine this
precise is cheaper to prove correct with direct unit tests than to verify a library
matches a requirement it wasn't written against.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

RETRY_ATTEMPTS = 3
RETRY_BASE_SECONDS = 0.25
RETRY_CAP_SECONDS = 4.0

BREAKER_FAILURE_THRESHOLD = 5
BREAKER_COOLDOWN_SECONDS = 30.0

BreakerState = Literal["CLOSED", "OPEN", "HALF_OPEN"]


class RetryableUpstreamError(Exception):
    """Marker exception the retry wrapper watches for. `retry_after_seconds`, when the
    vendor supplied one, is honoured as the wait for the *next* attempt (FR-023)."""

    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


async def retry_with_jitter(
    func,
    *args,
    clock=time.monotonic,
    sleep=None,
    **kwargs,
):
    """Bounded retry: 3 attempts total, exponential-jitter backoff (base 0.25s, cap 4s),
    honouring a vendor `retry_after_seconds` when present, always reusing the caller's
    original idempotency key (the caller passes the same request payload on every
    attempt — this wrapper never mutates it)."""
    import asyncio

    async def _sleep(seconds: float) -> None:
        if sleep is not None:
            await sleep(seconds)
        else:
            await asyncio.sleep(seconds)

    last_exc: RetryableUpstreamError | None = None
    async for attempt_state in AsyncRetrying(
        stop=stop_after_attempt(RETRY_ATTEMPTS),
        retry=retry_if_exception_type(RetryableUpstreamError),
        wait=wait_exponential_jitter(initial=RETRY_BASE_SECONDS, max=RETRY_CAP_SECONDS),
        reraise=True,
    ):
        with attempt_state:
            try:
                return await func(*args, **kwargs)
            except RetryableUpstreamError as exc:
                last_exc = exc
                if exc.retry_after_seconds is not None:
                    await _sleep(exc.retry_after_seconds)
                raise
    raise last_exc  # pragma: no cover — AsyncRetrying always re-raises on exhaustion


class BreakerOpen(Exception):
    """Raised when a call is refused immediately because the breaker for that upstream
    is OPEN. Blocking is uniform — no idempotency-key exemption (FR-031)."""


@dataclass
class CircuitBreaker:
    """One instance per upstream vendor, held in the gateway process's memory (no
    cross-process synchronization needed — research.md D1/D5).

    ``CLOSED --[5 consecutive failures]--> OPEN --[30s elapsed]--> HALF_OPEN``
    ``HALF_OPEN --[probe succeeds]--> CLOSED``; ``HALF_OPEN --[probe fails]--> OPEN``
    (cooldown resets).
    """

    upstream: str
    failure_threshold: int = BREAKER_FAILURE_THRESHOLD
    cooldown_seconds: float = BREAKER_COOLDOWN_SECONDS
    clock: staticmethod = time.monotonic

    def __post_init__(self) -> None:
        self._state: BreakerState = "CLOSED"
        self._consecutive_failures = 0
        self._opened_at: float | None = None
        self._half_open_probe_in_flight = False

    @property
    def state(self) -> BreakerState:
        if self._state == "OPEN" and self._opened_at is not None:
            elapsed = self.clock() - self._opened_at
            if elapsed >= self.cooldown_seconds and not self._half_open_probe_in_flight:
                self._state = "HALF_OPEN"
        return self._state

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def before_call(self) -> None:
        """Call before making an upstream request. Raises `BreakerOpen` if this
        upstream is currently blocked — uniformly, regardless of idempotency key."""
        current = self.state
        if current == "OPEN":
            raise BreakerOpen(f"circuit breaker OPEN for upstream {self.upstream!r}")
        if current == "HALF_OPEN":
            if self._half_open_probe_in_flight:
                raise BreakerOpen(
                    f"circuit breaker HALF_OPEN for upstream {self.upstream!r} — probe in flight"
                )
            self._half_open_probe_in_flight = True

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._state = "CLOSED"
        self._opened_at = None
        self._half_open_probe_in_flight = False

    def record_failure(self) -> None:
        """A schema-invalid or unparseable response body counts identically to a 5xx
        (FR-038) — callers pass through this same method for both."""
        self._half_open_probe_in_flight = False
        if self._state == "HALF_OPEN":
            self._state = "OPEN"
            self._opened_at = self.clock()
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._state = "OPEN"
            self._opened_at = self.clock()
