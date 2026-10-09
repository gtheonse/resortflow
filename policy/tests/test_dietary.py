"""T024 — property tests for `check_dietary_fidelity` (ADR-029, FR-006)."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from graph.models import DietaryRequirement, DietaryRestriction, DietarySeverity, Domain, Proposal
from policy.dietary import check_dietary_fidelity

_restrictions = st.sampled_from(list(DietaryRestriction))
_severities = st.sampled_from(list(DietarySeverity))


def _requirement(restriction: DietaryRestriction, severity: DietarySeverity) -> DietaryRequirement:
    return DietaryRequirement(restriction=restriction, severity=severity)


def _proposal(dietary: list[DietaryRequirement]) -> Proposal:
    return Proposal(proposal_id="p1", domain=Domain.DINING, tenant_id="MGV", title="Venue", dietary=dietary)


@given(
    restrictions=st.lists(_restrictions, min_size=1, max_size=5, unique=True),
    severity=_severities,
)
def test_every_conversation_restriction_present_in_payload_is_fidelity_ok(
    restrictions: list[DietaryRestriction], severity: DietarySeverity
) -> None:
    """For any set of restrictions, if every one mentioned in conversation
    is also on the proposal's structured payload, fidelity holds — severity
    need not match exactly (the structured payload is authoritative for
    severity)."""
    conversation = [_requirement(r, severity) for r in restrictions]
    proposal = _proposal([_requirement(r, DietarySeverity.ALLERGY_SEVERE) for r in restrictions])

    result = check_dietary_fidelity(proposal, conversation)

    assert result.fidelity_ok is True
    assert result.missing == []


@given(
    present=st.lists(_restrictions, min_size=0, max_size=3, unique=True),
    missing_only=st.lists(_restrictions, min_size=1, max_size=3, unique=True),
)
def test_a_conversation_restriction_absent_from_payload_is_never_fidelity_ok(
    present: list[DietaryRestriction], missing_only: list[DietaryRestriction]
) -> None:
    """For any restriction mentioned in conversation but genuinely absent
    from the structured payload, fidelity_ok is always False and that
    restriction always appears in `missing`."""
    # Ensure no overlap between the two sets, so `missing_only` is truly absent.
    missing_only = [r for r in missing_only if r not in present]
    if not missing_only:
        return  # degenerate case from hypothesis's shrinking; skip
    conversation = [_requirement(r, DietarySeverity.ALLERGY_MODERATE) for r in present + missing_only]
    proposal = _proposal([_requirement(r, DietarySeverity.ALLERGY_MODERATE) for r in present])

    result = check_dietary_fidelity(proposal, conversation)

    assert result.fidelity_ok is False
    missing_restrictions = {r.restriction for r in result.missing}
    assert missing_restrictions == set(missing_only)


def test_empty_conversation_restrictions_is_always_fidelity_ok() -> None:
    proposal = _proposal([])
    result = check_dietary_fidelity(proposal, [])
    assert result.fidelity_ok is True
    assert result.missing == []


def test_severity_mismatch_alone_does_not_fail_fidelity() -> None:
    """The structured payload is authoritative for severity (AGENTS.md) —
    this check only verifies the *restriction* was carried forward, not that
    severity matches exactly."""
    conversation = [_requirement(DietaryRestriction.SHELLFISH, DietarySeverity.PREFERENCE)]
    proposal = _proposal([_requirement(DietaryRestriction.SHELLFISH, DietarySeverity.ALLERGY_SEVERE)])

    result = check_dietary_fidelity(proposal, conversation)

    assert result.fidelity_ok is True
