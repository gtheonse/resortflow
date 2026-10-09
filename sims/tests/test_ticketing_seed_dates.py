"""Demo-report fix: seeded events used to be hardcoded to November 2026, so a
"what's on in the next 5 days" query could never match anything. They now roll
relative to the seed date (pinnable via SIM_SEED_BASE_DATE)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from sims.ticketing import seed


def test_events_span_the_28_days_after_the_base_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SIM_SEED_BASE_DATE", "2026-09-30")
    days = {date.fromisoformat(seed._event_day(i)) for i in range(60)}
    assert min(days) == date(2026, 10, 1)
    assert max(days) == date(2026, 9, 30) + timedelta(days=28)


def test_unpinned_base_is_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SIM_SEED_BASE_DATE", raising=False)
    first = date.fromisoformat(seed._event_day(0))
    assert first > date.today() - timedelta(days=1)  # noqa: DTZ011 — seeder uses local date
