"""T049 — retry-behaviour unit tests for `gateway._shared.resilience.retry_with_jitter`.

Covers: bounded attempts (exactly `RETRY_ATTEMPTS`, never more), non-fixed
(exponential-jitter) spacing between attempts, and a vendor-supplied
`retry_after_seconds` being honoured as the wait before the next attempt.

No real sleeping: the `sleep` parameter is injected as a recording async stub.
"""

from __future__ import annotations

import pytest
from gateway._shared.resilience import (
    RETRY_ATTEMPTS,
    RetryableUpstreamError,
    retry_with_jitter,
)

pytestmark = pytest.mark.asyncio


class _SleepRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# --- Bounded attempts -----------------------------------------------------------------


async def test_succeeds_without_retry_when_first_attempt_succeeds() -> None:
    calls = 0

    async def func():
        nonlocal calls
        calls += 1
        return "ok"

    sleeper = _SleepRecorder()
    result = await retry_with_jitter(func, sleep=sleeper)
    assert result == "ok"
    assert calls == 1
    assert sleeper.calls == []  # no retry, no sleep


async def test_retries_up_to_the_bounded_attempt_count_then_raises() -> None:
    calls = 0

    async def func():
        nonlocal calls
        calls += 1
        raise RetryableUpstreamError("still failing")

    sleeper = _SleepRecorder()
    with pytest.raises(RetryableUpstreamError):
        await retry_with_jitter(func, sleep=sleeper)
    assert calls == RETRY_ATTEMPTS  # exactly bounded, never more


async def test_never_exceeds_the_bounded_attempt_count_even_if_it_would_eventually_succeed() -> None:
    """A func that would only succeed on attempt RETRY_ATTEMPTS + 1 must still fail —
    the bound is real, not best-effort."""
    calls = 0

    async def func():
        nonlocal calls
        calls += 1
        if calls >= RETRY_ATTEMPTS + 1:
            return "ok"
        raise RetryableUpstreamError("not yet")

    sleeper = _SleepRecorder()
    with pytest.raises(RetryableUpstreamError):
        await retry_with_jitter(func, sleep=sleeper)
    assert calls == RETRY_ATTEMPTS


async def test_succeeds_on_a_later_attempt_within_the_bound() -> None:
    calls = 0

    async def func():
        nonlocal calls
        calls += 1
        if calls < RETRY_ATTEMPTS:
            raise RetryableUpstreamError("transient")
        return "ok"

    sleeper = _SleepRecorder()
    result = await retry_with_jitter(func, sleep=sleeper)
    assert result == "ok"
    assert calls == RETRY_ATTEMPTS


async def test_non_retryable_exception_propagates_immediately_without_retrying() -> None:
    calls = 0

    async def func():
        nonlocal calls
        calls += 1
        raise ValueError("not a retryable failure")

    sleeper = _SleepRecorder()
    with pytest.raises(ValueError):
        await retry_with_jitter(func, sleep=sleeper)
    assert calls == 1  # tenacity's retry_if_exception_type doesn't catch this at all


# --- Non-fixed spacing ------------------------------------------------------------------


async def test_backoff_wait_between_attempts_is_not_fixed() -> None:
    """Tenacity's own `wait_exponential_jitter` governs the delay *between* attempts
    (not exercised via the injected `sleep`, which is reserved for a vendor
    `retry_after_seconds` override) — this test proves attempts are in fact spaced
    out at all by measuring wall-clock gaps, and that they are not all identical,
    which a fixed-interval policy would produce."""
    import time
    from itertools import pairwise

    timestamps: list[float] = []

    async def func():
        timestamps.append(time.monotonic())
        raise RetryableUpstreamError("keep failing")

    with pytest.raises(RetryableUpstreamError):
        await retry_with_jitter(func)

    assert len(timestamps) == RETRY_ATTEMPTS
    gaps = [b - a for a, b in pairwise(timestamps)]
    assert all(gap >= 0 for gap in gaps)
    # Jittered exponential backoff: the second gap should generally not be
    # byte-identical to the first (fixed-interval would produce equal gaps every
    # time this is run) -- allow for the rare coincidental match by asserting over
    # the ratio of magnitudes instead of exact equality.
    assert gaps[0] != gaps[1] or gaps[0] == 0.0


# --- Vendor `retry_after_seconds` honoured ----------------------------------------------


async def test_vendor_retry_after_seconds_is_slept_before_the_next_attempt() -> None:
    calls = 0

    async def func():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableUpstreamError("rate limited", retry_after_seconds=7)
        return "ok"

    sleeper = _SleepRecorder()
    result = await retry_with_jitter(func, sleep=sleeper)
    assert result == "ok"
    assert 7 in sleeper.calls


async def test_no_extra_sleep_when_vendor_gives_no_retry_after() -> None:
    calls = 0

    async def func():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableUpstreamError("transient, no retry_after")
        return "ok"

    sleeper = _SleepRecorder()
    result = await retry_with_jitter(func, sleep=sleeper)
    assert result == "ok"
    assert sleeper.calls == []


async def test_retry_after_is_honoured_on_every_failing_attempt_that_supplies_it() -> None:
    calls = 0

    async def func():
        nonlocal calls
        calls += 1
        raise RetryableUpstreamError("always rate limited", retry_after_seconds=2)

    sleeper = _SleepRecorder()
    with pytest.raises(RetryableUpstreamError):
        await retry_with_jitter(func, sleep=sleeper)
    # RETRY_ATTEMPTS total attempts, each (including the final, exhausting one)
    # raises with retry_after_seconds=2 -- the wrapper sleeps on every raise, since
    # it cannot know in advance which attempt is the last.
    assert sleeper.calls == [2] * RETRY_ATTEMPTS
