"""Allergen-fidelity checking (ADR-029, FR-006).

`check_dietary_fidelity` verifies every dietary restriction mentioned in
conversation is present in a booking's structured payload — distinct from
`agents/dining/nodes/allergen_check.py`'s existing menu-content matching
(which assumes the structured payload is trustworthy and checks it against
what a venue's menu actually contains). This check catches a different
failure mode: a restriction the guest stated in conversation that never made
it into (or was dropped from) the structured `Proposal.dietary` field.
"""

from __future__ import annotations

from graph.models import DietaryRequirement, Proposal
from policy.types import DietaryFidelityResult


def check_dietary_fidelity(
    proposal: Proposal, conversation_restrictions: list[DietaryRequirement]
) -> DietaryFidelityResult:
    """Every restriction in `conversation_restrictions` must have a matching
    `DietaryRestriction` value somewhere in `proposal.dietary` — severity is
    not compared (a restriction stated as a preference in one turn and
    escalated to an allergy in another is still "present," just under a
    different severity; the structured payload is authoritative for
    severity per AGENTS.md's dietary hard rule, this check only verifies the
    restriction itself was carried forward)."""
    proposal_restrictions = {d.restriction for d in proposal.dietary}
    missing = [r for r in conversation_restrictions if r.restriction not in proposal_restrictions]
    return DietaryFidelityResult(fidelity_ok=not missing, missing=missing)
