"""T039 -- SC-009: `resolve()` adds no more than 5 ms median overhead per model call on a cache hit.

The SDK owns the registry cache (FR-006), so "a cache hit" is modelled here by an adapter that returns
instantly: what is measured is everything the PROJECT adds per call -- lock checks, hashing, variable checks and
single-pass rendering. The assertion is on the median, not the mean, so one slow outlier (GC, a noisy CI
neighbour) cannot fail the build.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

import pytest

from prompts.registry import resolve
from prompts.tests.helpers import (
    PROMPT_ID,
    REQUIRED,
    FakeAdapter,
    hash_of,
    registry_prompt,
    settings,
    state_with,
    template_dict,
    write_templates,
)

CALLS = 1000
BUDGET_MS = 5.0


@pytest.fixture
def templates_dir(tmp_path: Path) -> Path:
    return write_templates(tmp_path / "templates", template_dict())


def _median_ms(run: object, calls: int = CALLS) -> float:
    samples: list[float] = []
    for _ in range(calls):
        start = time.perf_counter_ns()
        run()  # type: ignore[operator]
        samples.append((time.perf_counter_ns() - start) / 1_000_000)
    return statistics.median(samples)


def test_registry_hit_median_overhead_is_within_budget(templates_dir: Path) -> None:
    approved = registry_prompt("{{dietary_restrictions}} {{dietary_severities}} approved wording", version=5)
    adapter = FakeAdapter(approved)
    state = state_with(approved=[hash_of(approved)])
    cfg = settings("langfuse")

    def call() -> None:
        got = resolve(
            PROMPT_ID,
            required_vars=REQUIRED,
            runtime_vars={"guest_message": "table for two, no shellfish"},
            settings=cfg,
            adapter=adapter,
            state=state,
            templates_dir=templates_dir,
        )
        assert got.source == "langfuse"

    call()  # warm the template cache
    median = _median_ms(call)
    assert median <= BUDGET_MS, f"median resolve() overhead {median:.3f} ms exceeds {BUDGET_MS} ms"


def test_git_source_median_overhead_is_within_budget(templates_dir: Path) -> None:
    state = state_with()
    cfg = settings("git")

    def call() -> None:
        resolve(
            PROMPT_ID,
            required_vars=REQUIRED,
            runtime_vars={"guest_message": "table for two"},
            settings=cfg,
            state=state,
            templates_dir=templates_dir,
        )

    call()
    median = _median_ms(call)
    assert median <= BUDGET_MS, f"median resolve() overhead {median:.3f} ms exceeds {BUDGET_MS} ms"
