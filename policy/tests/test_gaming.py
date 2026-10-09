"""T013 — tests for `require_gaming_clear` (ADR-030, ADR-037, FR-001, FR-016, FR-021).

Reuses `agents.gaming.tests.fakes.FakeGamingTools` (the same fake the gaming
subgraph's own tests use) rather than a second, policy-specific fake — one
`GamingTools` test double for the whole project.
"""

from __future__ import annotations

from gateway.gaming.models import ResponsibleGamingStatus
from hypothesis import given
from hypothesis import strategies as st

from agents.gaming.tests.fakes import FakeGamingTools
from agents.gaming.tools import GamingToolError
from policy.gaming import require_gaming_clear


def _status(*, self_excluded: bool, gaming_content_permitted: bool) -> ResponsibleGamingStatus:
    return ResponsibleGamingStatus(
        guest_id="g1",
        jurisdiction="NV",
        self_excluded=self_excluded,
        exclusion_scope="all_gaming" if self_excluded else None,
        limit_flags=["self_exclusion_active"] if self_excluded else [],
        offers_permitted=not self_excluded,
        gaming_content_permitted=gaming_content_permitted,
        mandated_message="You have an active self-exclusion on file." if self_excluded else None,
        support_resources=[],
    )


@given(self_excluded=st.booleans(), gaming_content_permitted=st.booleans())
async def test_self_excluded_or_content_not_permitted_never_permits(
    self_excluded: bool, gaming_content_permitted: bool
) -> None:
    """For any ResponsibleGamingStatus, `permitted` is `False` whenever
    `self_excluded` is `True` OR `gaming_content_permitted` is `False` —
    covering every combination, not just the two named fixture guests."""
    status = _status(self_excluded=self_excluded, gaming_content_permitted=gaming_content_permitted)
    tools = FakeGamingTools(rg_statuses={"g1": status})

    clearance = await require_gaming_clear(tools, guest_id="g1")

    expected_permitted = not (self_excluded or not gaming_content_permitted)
    assert clearance.permitted is expected_permitted
    if not expected_permitted:
        assert clearance.failure_reason == "self_excluded"
        assert clearance.status == status


async def test_good_standing_guest_is_permitted() -> None:
    tools = FakeGamingTools()  # default FAKE_RG_STATUSES fixture
    clearance = await require_gaming_clear(tools, guest_id="guest-good-standing")
    assert clearance.permitted is True
    assert clearance.failure_reason is None
    assert clearance.status is not None


async def test_self_excluded_guest_is_not_permitted() -> None:
    tools = FakeGamingTools()
    clearance = await require_gaming_clear(tools, guest_id="guest-self-excluded")
    assert clearance.permitted is False
    assert clearance.failure_reason == "self_excluded"
    assert clearance.status is not None
    assert clearance.status.mandated_message is not None


async def test_dependency_unavailable_never_raises() -> None:
    """FR-021: a vendor-call failure resolves to a value, not an exception —
    both call sites (gaming lookup, Offer Agent) need something to branch
    on."""
    tools = FakeGamingTools(fail_rg_status=GamingToolError("upstream timeout", http_status=503))

    clearance = await require_gaming_clear(tools, guest_id="anyone")

    assert clearance.permitted is False
    assert clearance.failure_reason == "dependency_unavailable"
    assert clearance.status is None


async def test_check_rg_status_is_actually_called_every_invocation() -> None:
    """FR-016: no caching — a fresh vendor check every call."""
    tools = FakeGamingTools()
    await require_gaming_clear(tools, guest_id="guest-good-standing")
    await require_gaming_clear(tools, guest_id="guest-good-standing")
    assert tools.calls.count("check_rg_status") == 2
