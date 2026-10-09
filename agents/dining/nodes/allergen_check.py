"""T049 — Allergen fidelity check (policy-critical). Cross-checks the
selected `Proposal.dietary` (`list[graph.models.DietaryRequirement]`,
already structured by `intent_parser` — this node never re-derives it from
free text) against venue menu content.

Feature 006 (ADR-029, FR-006) added a second, independent check alongside
the existing menu-content matching: `policy.dietary.check_dietary_fidelity`
verifies every dietary restriction mentioned *anywhere in the conversation*
(not just the current turn's parsed intent) made it into the proposal's
structured `dietary` field — catching the case where a guest states an
allergy in an early turn, but a later booking request in the same
conversation doesn't repeat it, so the freshly-built `Proposal.dietary`
never carried it forward.

Either check finding a block is now **non-overridable**: `policy_blocked`
is set (never `awaiting_approval`), a `PolicyFlag` records it, and the
guest-facing message is produced directly here — the flow never reaches
`human_approval`'s approve/decline interrupt. This is the fix ADR-029
describes: before this feature, `human_approval` could be resumed with
`{"approved": true}` straight past a blocking allergen conflict, since
nothing distinguished "awaiting an approvable decision" from "structurally
blocked" at the routing level.

`PREFERENCE` / `INTOLERANCE` conflicts are informational only — they are
still recorded in `dietary_conflicts` (useful to the guest-facing message
and the eval set) but never block.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage

from agents.dining.menu import default_menu_query
from agents.dining.nodes.intent_parser import RESTRICTION_KEYWORDS, _extract_dietary
from agents.dining.nodes.venue_discovery import venue_id_from_proposal_id
from agents.dining.state import DietaryConflict, DiningState
from graph.models import DietarySeverity, Domain, PolicyFlag, Proposal, SagaEvent
from policy.dietary import check_dietary_fidelity
from policy.types import DietaryFidelityResult

_BLOCKING_SEVERITIES = frozenset({DietarySeverity.ALLERGY_SEVERE, DietarySeverity.ALLERGY_MODERATE})

_BLOCK_MESSAGE = (
    "I can't confirm this booking yet — a dietary restriction you mentioned isn't "
    "reflected in the request behind it. This can only be resolved by correcting "
    "the structured record, not by approving past it; please update the "
    "restriction and I'll try again."
)


def _conversation_dietary_mentions(state: Any) -> list:
    """Scan every human message in the conversation for a dietary mention,
    using `intent_parser`'s own extraction logic — not just the latest
    turn's already-parsed `intent`, which `venue_discovery` already copies
    onto the proposal directly (so comparing against it would be
    tautological)."""
    mentions = []
    for message in state.get("messages") or []:
        content = getattr(message, "content", None)
        msg_type = getattr(message, "type", None)
        if content and msg_type in (None, "human") and isinstance(content, str):
            mentions.extend(_extract_dietary(content))
    return mentions


@dataclass(frozen=True)
class AllergenVerdict:
    """The single allergen decision shared by the standalone dining subgraph and
    the cross-domain fan-out leg — so both paths enforce identical policy."""

    blocking: bool
    conflicts: list[DietaryConflict]
    fidelity: DietaryFidelityResult


def evaluate_allergens(
    state: Any, proposal: Proposal, menu_query: Callable[..., Any] = default_menu_query
) -> AllergenVerdict:
    """Pure policy decision: structural fidelity (every restriction mentioned in
    the conversation reached `proposal.dietary`) plus menu-content matching.
    Fidelity runs even when `proposal.dietary` is empty — an EMPTY structured
    payload despite a conversation mention is exactly the failure it catches
    (FR-006)."""
    fidelity = check_dietary_fidelity(proposal, _conversation_dietary_mentions(state))
    conflicts: list[DietaryConflict] = []
    menu_blocking = False
    if proposal.dietary:
        venue_id = venue_id_from_proposal_id(proposal.proposal_id)
        chunks = menu_query(state["tenant_id"], venue_id)
        for requirement in proposal.dietary:
            keywords = RESTRICTION_KEYWORDS.get(requirement.restriction, ())
            for chunk in chunks:
                lowered = f" {chunk.content.lower()} "
                if not any(kw in lowered for kw in keywords):
                    continue
                conflicts.append(
                    DietaryConflict(
                        restriction=requirement.restriction.value,
                        severity=requirement.severity.value,
                        menu_item=chunk.content,
                        chunk_id=chunk.chunk_id,
                        source_uri=chunk.source_uri,
                    )
                )
                if requirement.severity in _BLOCKING_SEVERITIES:
                    menu_blocking = True
    return AllergenVerdict(blocking=menu_blocking or not fidelity.fidelity_ok, conflicts=conflicts, fidelity=fidelity)


def block_policy_flags(jurisdiction: str | None) -> list[PolicyFlag]:
    """The blocked + escalated flag pair recorded for any non-overridable allergen block."""
    return [
        PolicyFlag(
            flag_id=uuid.uuid4().hex, rule="allergen_fidelity_mismatch", disposition=disposition,
            jurisdiction=jurisdiction, raised_at=datetime.now(UTC),
        )
        for disposition in ("blocked", "escalated")
    ]


def block_saga_event(saga_id: str, sequence: int, proposal: Proposal, verdict: AllergenVerdict) -> SagaEvent:
    return SagaEvent(
        event_id=uuid.uuid4().hex,
        saga_id=saga_id,
        sequence=sequence,
        kind="dietary_conflict_flagged",
        domain=Domain.DINING,
        occurred_at=datetime.now(UTC),
        detail={
            "proposal_id": proposal.proposal_id,
            "conflict_count": str(len(verdict.conflicts)),
            "fidelity_missing_count": str(len(verdict.fidelity.missing)),
        },
    )


def make_allergen_check_node(menu_query=default_menu_query):
    """`menu_query` defaults to `default_menu_query` (Track D's real
    Qdrant-backed `rag.menu.get_venue_chunks`, falling back to the
    deterministic stub if Qdrant is unreachable) but is injectable — tests
    pass the raw stub or a fake directly for determinism."""

    def allergen_check(state: DiningState) -> dict:
        selected_id = state.get("selected_proposal_id")
        proposal: Proposal | None = next(
            (c for c in state.get("proposals", []) if c.proposal_id == selected_id), None
        )
        if proposal is None:
            return {}

        verdict = evaluate_allergens(state, proposal, menu_query)
        update: dict = {
            "dietary_conflicts": verdict.conflicts,
            # Always explicitly set, never omitted — a stale True from an
            # earlier attempt must never persist once the guest corrects the
            # record and the check re-runs clean (FR-006).
            "policy_blocked": verdict.blocking,
            "awaiting_approval": False,
            "approval_scope": None,
        }
        if verdict.blocking:
            update["saga_log"] = [
                block_saga_event(state["saga_id"], len(state.get("saga_log") or []) + 1, proposal, verdict)
            ]
            update["policy_flags"] = block_policy_flags(state.get("jurisdiction"))
            update["messages"] = [AIMessage(content=_BLOCK_MESSAGE)]
        return update

    return allergen_check
