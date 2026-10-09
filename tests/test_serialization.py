"""T020 — every entity and the assembled `ResortFlowState` survive
`JsonPlusSerializer` with no pickle fallback, and 100% of PII/HEALTH fields
are identifiable from the model definition alone.

Depends on T009 (graph/models.py) and T015 (graph/state.py). Spans all
three user stories' output — this is the whole-phase "does the frozen
module contain what the design doc says" check for the domain model layer,
analogous to T011's state-key guard and T030's node-registry guard.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from graph.models import (
    Chunk,
    Citation,
    Confirmation,
    DietaryRequirement,
    DietaryRestriction,
    DietarySeverity,
    Domain,
    Guest,
    Hold,
    HoldStatus,
    Plan,
    PlanTask,
    PolicyFlag,
    Proposal,
    SagaEvent,
    Sensitivity,
    Stay,
    ToolFailure,
)
from graph.state import ResortFlowState

UTC_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)

serde = JsonPlusSerializer()


def _roundtrip(value: Any) -> Any:
    type_tag, payload = serde.dumps_typed(value)
    assert type_tag != "pickle", f"pickle fallback used for {type(value)!r}"
    return serde.loads_typed((type_tag, payload))


# --- One representative, fully-populated instance per entity ----------------

GUEST = Guest(
    guest_id="g1",
    display_name="Jane Doe",
    phone="555-0100",
    email="jane@example.com",
    loyalty_tier="platinum",
    entitlements=["spa_access"],
)
STAY = Stay(
    stay_id="s1", guest_id="g1", tenant_id="MGV",
    arrival=date(2026, 9, 10), departure=date(2026, 9, 12), room_id="1204",
)
DIETARY = DietaryRequirement(
    restriction=DietaryRestriction.SHELLFISH,
    severity=DietarySeverity.ALLERGY_SEVERE,
    applies_to_guest="guest 2 of 2",
    detail="anaphylaxis risk",
)
PROPOSAL = Proposal(
    proposal_id="p1", domain=Domain.DINING, tenant_id="MGV", title="Lumiere 6:30",
    starts_at=UTC_NOW, ends_at=UTC_NOW, location_zone="north", party_size=2,
    price=Decimal("120.50"), hold_idempotency_key="idem-1", dietary=[DIETARY],
)
HOLD = Hold(
    idempotency_key="idem-1", hold_id="vendor-h1", domain=Domain.DINING,
    tenant_id="MGV", saga_id="saga-1", status=HoldStatus.HELD, expires_at=UTC_NOW,
    proposal_id="p1",
)
CONFIRMATION = Confirmation(
    confirmation_id="conf-1", idempotency_key="idem-1", domain=Domain.DINING,
    confirmed_at=UTC_NOW, cancellation_deadline=UTC_NOW,
)
SAGA_EVENT = SagaEvent(
    event_id="e1", saga_id="saga-1", sequence=1, kind="hold_placed",
    domain=Domain.DINING, occurred_at=UTC_NOW, detail={"venue": "Lumiere"},
)
PLAN_TASK = PlanTask(
    task_id="t1", domain=Domain.DINING, depends_on=[],
    constraints={"latest_end": "19:45"},
)
PLAN = Plan(plan_id="plan-1", tasks=[PLAN_TASK], created_at=UTC_NOW)
POLICY_FLAG = PolicyFlag(
    flag_id="f1", rule="allergen_alert", disposition="messaged",
    jurisdiction="NV", raised_at=UTC_NOW,
)
TOOL_FAILURE = ToolFailure(
    domain=Domain.DINING, tool="place_hold", error_class="RETRYABLE",
    vendor_code="UPSTREAM_ERROR", http_status=503, retry_after_seconds=5,
    detail="upstream timeout",
)
CHUNK = Chunk(
    chunk_id="c1", tenant_id="MGV", content="text", source_uri="uri",
    valid_from=UTC_NOW, valid_to=UTC_NOW, content_hash="hash",
)
CITATION = Citation(chunk_id="c1", source_uri="uri", quoted_span="span")

ALL_ENTITIES = [
    GUEST, STAY, DIETARY, PROPOSAL, HOLD, CONFIRMATION, SAGA_EVENT,
    PLAN_TASK, PLAN, POLICY_FLAG, TOOL_FAILURE, CHUNK, CITATION,
]


@pytest.mark.parametrize("entity", ALL_ENTITIES, ids=[type(e).__name__ for e in ALL_ENTITIES])
def test_entity_roundtrips_without_pickle_fallback(entity: Any) -> None:
    result = _roundtrip(entity)
    assert result == entity
    assert type(result) is type(entity)


def test_resortflow_state_roundtrips_without_pickle_fallback() -> None:
    state = ResortFlowState(
        tenant_id="MGV", guest_id="g1", session_id="s1", jurisdiction="NV",
        entitlements=["spa_access"], saga_id="saga-1", messages=[], plan=PLAN,
        replan_count=1, proposals=[PROPOSAL], holds=[HOLD],
        confirmations=[CONFIRMATION], failures=[TOOL_FAILURE],
        retrieved=[CHUNK], citations=[CITATION], awaiting_approval=True,
        approval_scope="dining", policy_flags=[POLICY_FLAG], escalate=False,
        saga_log=[SAGA_EVENT],
    )
    result = _roundtrip(state)
    assert result == state


# --- PII / HEALTH field markers (FR-023, SC-011) -----------------------------
#
# Ground truth transcribed from data-model.md's per-entity field tables.
# Every model field is enumerated — not just the sensitive ones — so this
# is both a positive check (sensitive fields ARE marked) and a negative
# check (nothing else is accidentally marked).

EXPECTED_SENSITIVITY: dict[type, dict[str, Sensitivity | None]] = {
    Guest: {
        "guest_id": None, "display_name": Sensitivity.PII, "phone": Sensitivity.PII,
        "email": Sensitivity.PII, "loyalty_tier": None, "entitlements": None,
    },
    Stay: {
        "stay_id": None, "guest_id": None, "tenant_id": None, "arrival": None,
        "departure": None, "room_id": None,
    },
    DietaryRequirement: {
        "restriction": Sensitivity.HEALTH, "severity": Sensitivity.HEALTH,
        "applies_to_guest": Sensitivity.PII, "detail": Sensitivity.HEALTH,
    },
    Proposal: {
        "proposal_id": None, "domain": None, "tenant_id": None, "title": None,
        "starts_at": None, "ends_at": None, "location_zone": None,
        "party_size": None, "price": None, "hold_idempotency_key": None,
        "dietary": None,
    },
    Hold: {
        "idempotency_key": None, "hold_id": None, "domain": None,
        "tenant_id": None, "saga_id": None, "status": None, "expires_at": None,
        "proposal_id": None,
    },
    Confirmation: {
        "confirmation_id": None, "idempotency_key": None, "domain": None,
        "confirmed_at": None, "cancellation_deadline": None,
    },
    SagaEvent: {
        "event_id": None, "saga_id": None, "sequence": None, "kind": None,
        "domain": None, "occurred_at": None, "detail": None,
    },
    PlanTask: {
        "task_id": None, "domain": None, "depends_on": None, "constraints": None,
    },
    Plan: {"plan_id": None, "tasks": None, "created_at": None},
    PolicyFlag: {
        "flag_id": None, "rule": None, "disposition": None,
        "jurisdiction": None, "raised_at": None,
    },
    ToolFailure: {
        "domain": None, "tool": None, "error_class": None, "vendor_code": None,
        "http_status": None, "retry_after_seconds": None, "detail": None,
    },
    Chunk: {
        "chunk_id": None, "tenant_id": None, "content": None, "source_uri": None,
        "valid_from": None, "valid_to": None, "content_hash": None,
    },
    Citation: {"chunk_id": None, "source_uri": None, "quoted_span": None},
}


def _field_sensitivity(model: type, field: str) -> Sensitivity | None:
    metadata = model.model_fields[field].metadata
    markers = [m for m in metadata if isinstance(m, Sensitivity)]
    assert len(markers) <= 1, f"{model.__name__}.{field} carries multiple sensitivity markers"
    return markers[0] if markers else None


@pytest.mark.parametrize(
    "model,field,expected",
    [
        (model, field, expected)
        for model, fields in EXPECTED_SENSITIVITY.items()
        for field, expected in fields.items()
    ],
    ids=[
        f"{model.__name__}.{field}"
        for model, fields in EXPECTED_SENSITIVITY.items()
        for field in fields
    ],
)
def test_field_sensitivity_marker_matches_data_model(
    model: type, field: str, expected: Sensitivity | None
) -> None:
    assert _field_sensitivity(model, field) == expected


def test_every_model_field_is_accounted_for() -> None:
    """Guards against a field being added to a model without also being
    added to EXPECTED_SENSITIVITY above — closes the gap this test exists
    to close (SC-011: 100% of sensitive fields marked, provably, not by
    omission)."""
    for model, expected_fields in EXPECTED_SENSITIVITY.items():
        actual_fields = set(model.model_fields.keys())
        assert actual_fields == set(expected_fields.keys()), (
            f"{model.__name__}'s fields drifted from this test's ground truth"
        )
