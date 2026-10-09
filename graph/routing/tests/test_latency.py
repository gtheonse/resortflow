"""Feature 010, T034 — SC-007: median added time for tiers 0-1 over a
labelled set is under 5 ms per message. An inline fixture, independent of
US5's larger `eval/data/routing_cases.yaml`, so US1 stays independently
testable."""

from __future__ import annotations

import statistics
import time

from graph.routing.kinds import classify_kind
from graph.routing.matcher import match_services

_MESSAGES = [
    "book a restaurent table for 4 at 8pm",
    "can I get tickets for the theater tonight",
    "the shower in my room is broken",
    "dinner and a show tomorrow",
    "I'd like a masage at the spa",
    "what is the restaurent i got reserved for sep 30, 8 PM?",
    "book flight for me to fly to India",
    "cancel my dinner reservation",
    "move my spa appointment to 4pm",
    "tabel for 2 tonight",
    "cancle my dinner",
    "can I zoom in on the room photos",
    "what have I booked?",
    "hi",
    "thanks",
    "what time does the pool close?",
    "reserving a table for tonight",
    "we're dining at 7 tonight",
    "late checkout plus a couples massage",
    "cancel the show tickets please",
] * 5  # 100 messages total


def test_median_tier_0_1_latency_is_under_five_milliseconds() -> None:
    samples: list[float] = []
    for text in _MESSAGES:
        start = time.perf_counter()
        services = match_services(text)
        classify_kind(text, services)
        samples.append((time.perf_counter() - start) * 1000)
    median = statistics.median(samples)
    assert median < 5.0, f"median tier 0-1 latency was {median:.3f} ms"
