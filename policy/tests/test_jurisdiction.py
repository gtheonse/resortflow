"""T008 — unit tests for `mandated_message_for` (ADR-034, FR-012, SC-006).

Both tenants, both triggers, and the no-entry-configured case.
"""

from __future__ import annotations

import pytest

from policy.jurisdiction import mandated_message_for


@pytest.mark.parametrize("jurisdiction", ["NV", "MA"])
@pytest.mark.parametrize("trigger", ["gaming_content", "offer"])
def test_mandated_message_for_known_jurisdiction_and_trigger(
    jurisdiction: str, trigger: str
) -> None:
    message = mandated_message_for(jurisdiction, trigger)  # type: ignore[arg-type]
    assert message is not None
    assert len(message) > 0


def test_nv_gaming_content_message_names_nv_regulator_and_helpline() -> None:
    message = mandated_message_for("NV", "gaming_content")
    assert message is not None
    assert "Nevada" in message
    assert "1-800-522-4700" in message


def test_ma_gaming_content_message_names_ma_regulator_and_helpline() -> None:
    message = mandated_message_for("MA", "gaming_content")
    assert message is not None
    assert "Massachusetts" in message
    assert "1-800-327-5050" in message


def test_nv_and_ma_messages_are_worded_differently() -> None:
    """SC-006: messages are worded identically *within* a jurisdiction across
    spot checks, but the two jurisdictions' own regulators mandate different
    wording — this is not a single shared default (FR-019)."""
    nv = mandated_message_for("NV", "gaming_content")
    ma = mandated_message_for("MA", "gaming_content")
    assert nv != ma


def test_unknown_jurisdiction_returns_none() -> None:
    assert mandated_message_for("CA", "gaming_content") is None


def test_unknown_trigger_returns_none() -> None:
    assert mandated_message_for("NV", "unknown_trigger") is None  # type: ignore[arg-type]


def test_message_is_deterministic_across_repeated_calls() -> None:
    """FR-012: never generated — repeated calls with the same inputs must
    return the exact same string, not merely an equivalent one."""
    first = mandated_message_for("NV", "offer")
    second = mandated_message_for("NV", "offer")
    assert first == second
    assert first is not None
