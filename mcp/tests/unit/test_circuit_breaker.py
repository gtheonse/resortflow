"""T046 — breaker state-machine unit tests (`gateway._shared.resilience.CircuitBreaker`,
FR-031).

Covers the full state machine documented on the class:
``CLOSED --[5 consecutive failures]--> OPEN --[30s elapsed]--> HALF_OPEN``
``HALF_OPEN --[probe succeeds]--> CLOSED``; ``HALF_OPEN --[probe fails]--> OPEN``
(cooldown resets) — plus uniform blocking while OPEN, with **no** idempotency-key
exemption (FR-031): `before_call` takes no key argument at all, so this is proven by
showing blocking is unconditional regardless of any key the caller might otherwise
attach to the call.

A fake monotonic clock is injected via the dataclass's `clock` field so the cooldown
transition is tested deterministically, with no real sleeping.
"""

from __future__ import annotations

import pytest
from gateway._shared.resilience import (
    BREAKER_COOLDOWN_SECONDS,
    BREAKER_FAILURE_THRESHOLD,
    BreakerOpen,
    CircuitBreaker,
)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def make_breaker(clock: FakeClock | None = None) -> CircuitBreaker:
    clock = clock or FakeClock()
    return CircuitBreaker(upstream="test-vendor", clock=clock)


# --- CLOSED -> OPEN on threshold -----------------------------------------------------


def test_starts_closed() -> None:
    breaker = make_breaker()
    assert breaker.state == "CLOSED"
    breaker.before_call()  # does not raise


def test_stays_closed_below_threshold() -> None:
    breaker = make_breaker()
    for _ in range(BREAKER_FAILURE_THRESHOLD - 1):
        breaker.record_failure()
    assert breaker.state == "CLOSED"
    breaker.before_call()  # still admits calls


def test_opens_at_exactly_the_failure_threshold() -> None:
    breaker = make_breaker()
    for _ in range(BREAKER_FAILURE_THRESHOLD - 1):
        breaker.record_failure()
    assert breaker.state == "CLOSED"
    breaker.record_failure()  # the Nth consecutive failure
    assert breaker.state == "OPEN"


def test_consecutive_failures_counter_tracks_calls() -> None:
    breaker = make_breaker()
    assert breaker.consecutive_failures == 0
    breaker.record_failure()
    assert breaker.consecutive_failures == 1
    breaker.record_success()
    assert breaker.consecutive_failures == 0


def test_a_success_before_threshold_resets_the_failure_count() -> None:
    """Only *consecutive* failures count — an interleaved success resets the streak."""
    breaker = make_breaker()
    for _ in range(BREAKER_FAILURE_THRESHOLD - 1):
        breaker.record_failure()
    breaker.record_success()
    assert breaker.consecutive_failures == 0
    for _ in range(BREAKER_FAILURE_THRESHOLD - 1):
        breaker.record_failure()
    assert breaker.state == "CLOSED"  # still short of threshold after the reset


# --- OPEN blocks every call uniformly, including a known idempotency key -----------


def test_open_breaker_blocks_before_call() -> None:
    breaker = make_breaker()
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    assert breaker.state == "OPEN"
    with pytest.raises(BreakerOpen):
        breaker.before_call()


def test_open_breaker_blocks_uniformly_with_no_idempotency_key_exemption() -> None:
    """FR-031: `before_call` accepts no key argument and grants no exemption — an
    OPEN breaker refuses every call the same way regardless of what idempotency key
    the caller intends to attach afterward. Proven by calling it repeatedly, as a
    caller retrying the *same* known key would, and observing uniform refusal."""
    breaker = make_breaker()
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    known_key = "resortflow:sess-1:hold-1:1"
    for _ in range(5):
        # The breaker API takes no key; repeating the same "logical retry with a
        # known key" scenario N times must refuse every single time.
        with pytest.raises(BreakerOpen):
            breaker.before_call()
    assert known_key  # the key itself is irrelevant to the breaker — never consulted


def test_open_breaker_does_not_flip_back_before_cooldown_elapses() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock)
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    assert breaker.state == "OPEN"
    clock.advance(BREAKER_COOLDOWN_SECONDS - 0.01)
    assert breaker.state == "OPEN"
    with pytest.raises(BreakerOpen):
        breaker.before_call()


# --- OPEN -> HALF_OPEN after cooldown -----------------------------------------------


def test_open_transitions_to_half_open_after_cooldown() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock)
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    clock.advance(BREAKER_COOLDOWN_SECONDS + 0.01)
    assert breaker.state == "HALF_OPEN"


def test_half_open_admits_exactly_one_probe_and_blocks_concurrent_others() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock)
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    clock.advance(BREAKER_COOLDOWN_SECONDS + 0.01)
    assert breaker.state == "HALF_OPEN"
    breaker.before_call()  # the probe is admitted
    with pytest.raises(BreakerOpen):
        breaker.before_call()  # a second, concurrent caller is refused


# --- HALF_OPEN -> CLOSED on probe success -------------------------------------------


def test_half_open_probe_success_closes_the_breaker() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock)
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    clock.advance(BREAKER_COOLDOWN_SECONDS + 0.01)
    assert breaker.state == "HALF_OPEN"
    breaker.before_call()
    breaker.record_success()
    assert breaker.state == "CLOSED"
    assert breaker.consecutive_failures == 0
    breaker.before_call()  # fully admits calls again


# --- HALF_OPEN -> OPEN on probe failure, cooldown resets ----------------------------


def test_half_open_probe_failure_reopens_and_resets_cooldown() -> None:
    clock = FakeClock()
    breaker = make_breaker(clock)
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    clock.advance(BREAKER_COOLDOWN_SECONDS + 0.01)
    assert breaker.state == "HALF_OPEN"
    breaker.before_call()
    breaker.record_failure()  # probe fails
    assert breaker.state == "OPEN"
    # Cooldown reset: immediately after the failed probe, still OPEN even though the
    # *original* cooldown window had already elapsed.
    with pytest.raises(BreakerOpen):
        breaker.before_call()
    # A fresh full cooldown must elapse again before HALF_OPEN returns.
    clock.advance(BREAKER_COOLDOWN_SECONDS + 0.01)
    assert breaker.state == "HALF_OPEN"


def test_half_open_probe_failure_clears_probe_in_flight_flag() -> None:
    """A failed probe must not leave `_half_open_probe_in_flight` stuck true forever
    (which would wedge every future HALF_OPEN transition)."""
    clock = FakeClock()
    breaker = make_breaker(clock)
    for _ in range(BREAKER_FAILURE_THRESHOLD):
        breaker.record_failure()
    clock.advance(BREAKER_COOLDOWN_SECONDS + 0.01)
    breaker.before_call()
    breaker.record_failure()
    clock.advance(BREAKER_COOLDOWN_SECONDS + 0.01)
    assert breaker.state == "HALF_OPEN"
    breaker.before_call()  # admitted again -- not permanently wedged
