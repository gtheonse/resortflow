"""T009 — the domain entities and enums of the booking lifecycle.

FR-009, FR-010, FR-023. Transcribed verbatim from ``data-model.md`` and the
frozen OpenAPI specifications under ``docs/contracts/openapi/`` — where this
module and a vendor specification disagree, the specification wins and this
module is a defect (data-model.md's own framing).

Every model sets ``extra="forbid"``: an unexpected key is a contract
violation, not a tolerated extra. Fields carrying personal data or health
data are marked with ``Sensitivity.PII`` / ``Sensitivity.HEALTH`` as
``Annotated`` field metadata (FR-023) — readable back via
``Model.model_fields[name].metadata`` — so redaction and encryption
enumerate sensitive fields from the model itself rather than a hand
maintained list that drifts as models grow.

``LedgerEntry`` is deliberately absent from this module: data-model.md
states it is "Not part of ResortFlowState" — it is the durable row defined by
``infra/sql/001_hold_ledger.sql`` (User Story 2 / T017), not a state entity.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from graph.errors import ErrorClass

# --- Sensitivity markers (FR-023) ------------------------------------------


class Sensitivity(str, Enum):
    """Field-level data-sensitivity marker, carried as ``Annotated`` metadata."""

    PII = "PII"
    HEALTH = "HEALTH"


# --- Enumerations ------------------------------------------------------------
# Transcribed verbatim from data-model.md's "Enumerations" section and
# cross-checked against dining-sim.openapi.yaml's DietaryRestriction /
# DietaryRequirement schemas (14 restriction values, 4 severities).


class DietaryRestriction(str, Enum):
    SHELLFISH = "shellfish"
    TREE_NUT = "tree_nut"
    PEANUT = "peanut"
    DAIRY = "dairy"
    GLUTEN = "gluten"
    EGG = "egg"
    SOY = "soy"
    SESAME = "sesame"
    FISH = "fish"
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    HALAL = "halal"
    KOSHER = "kosher"
    LOW_SODIUM = "low_sodium"


class DietarySeverity(str, Enum):
    PREFERENCE = "preference"
    INTOLERANCE = "intolerance"
    ALLERGY_MODERATE = "allergy_moderate"
    ALLERGY_SEVERE = "allergy_severe"


class Domain(str, Enum):
    """`gaming` is read-only by construction — no mutating tool for it
    exists in any registry."""

    DINING = "dining"
    ENTERTAINMENT = "entertainment"
    STAY = "stay"
    WELLNESS = "wellness"
    GAMING = "gaming"


class HoldStatus(str, Enum):
    PENDING = "pending"
    HELD = "held"
    CONFIRMED = "confirmed"
    RELEASED = "released"
    EXPIRED = "expired"
    ORPHAN_RELEASED = "orphan_released"


TERMINAL_HOLD_STATUSES: Final[frozenset[HoldStatus]] = frozenset(
    {
        HoldStatus.CONFIRMED,
        HoldStatus.RELEASED,
        HoldStatus.EXPIRED,
        HoldStatus.ORPHAN_RELEASED,
    }
)
"""Terminal dispositions. Never regressed to `pending`/`held` by
`graph.state.merge_holds` — see data-model.md's Hold state machine."""


# --- Base model ---------------------------------------------------------------


class ResortFlowModel(BaseModel):
    """Shared config: an unexpected key is a contract violation."""

    model_config = ConfigDict(extra="forbid")


# --- Domain entities -----------------------------------------------------------


class Guest(ResortFlowModel):
    """The person the request is for. Carries personal data, marked as such
    at field level."""

    guest_id: str
    display_name: Annotated[str, Sensitivity.PII]
    phone: Annotated[str | None, Sensitivity.PII] = None
    email: Annotated[str | None, Sensitivity.PII] = None
    loyalty_tier: str | None = None
    """Bounded; safe as a metric dimension."""
    entitlements: list[str] = Field(default_factory=list)


class Stay(ResortFlowModel):
    """The guest's presence at a property, bounding the dates and location a
    request can address."""

    stay_id: str
    guest_id: str
    tenant_id: str
    """`MGV` or `MHB`."""
    arrival: date
    departure: date
    room_id: str | None = None
    """Null before assignment."""

    @model_validator(mode="after")
    def _departure_after_arrival(self) -> Stay:
        if self.departure <= self.arrival:
            raise ValueError("departure must be after arrival")
        return self


class DietaryRequirement(ResortFlowModel):
    """SAFETY-CRITICAL. `allergy_severe` must reach the kitchen as an
    allergen alert, not a preference note. `restriction` and `severity` are
    both required — free text (`detail`) may accompany them and can never
    substitute for them."""

    restriction: Annotated[DietaryRestriction, Sensitivity.HEALTH]
    severity: Annotated[DietarySeverity, Sensitivity.HEALTH]
    applies_to_guest: Annotated[str | None, Sensitivity.PII] = None
    """e.g. "guest 2 of 2"."""
    detail: Annotated[str | None, Sensitivity.HEALTH] = None
    """Free text. Never the sole carrier of a restriction."""


class Proposal(ResortFlowModel):
    """A candidate item a workstream is offering, before any commitment
    exists. Merge key for `graph.state.merge_proposals`: `proposal_id`."""

    proposal_id: str
    domain: Domain
    tenant_id: str
    title: str
    """Guest-facing."""
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    """Feeds the deterministic conflict checker."""
    location_zone: str | None = None
    """Travel-time input."""
    party_size: int | None = None
    price: Decimal | None = None
    hold_idempotency_key: str | None = None
    """→ `Hold`, when one has been placed."""
    dietary: list[DietaryRequirement] = Field(default_factory=list)


class Hold(ResortFlowModel):
    """A reversible claim on external inventory, identified before the
    external system responds. Merge key for `graph.state.merge_holds`:
    `idempotency_key` — never `hold_id`, which is null until the vendor
    responds."""

    idempotency_key: str
    """Required. Exists before the vendor responds."""
    hold_id: str | None = None
    """Vendor-assigned; null until the vendor answers."""
    domain: Domain
    tenant_id: str
    saga_id: str
    """→ ledger rows."""
    status: HoldStatus
    expires_at: datetime | None = None
    """Null while `pending`. Must be present once `status` is `held`."""
    proposal_id: str | None = None

    @model_validator(mode="after")
    def _expires_at_present_when_held(self) -> Hold:
        if self.status == HoldStatus.HELD and self.expires_at is None:
            raise ValueError("expires_at must be present once status is 'held'")
        return self


class Confirmation(ResortFlowModel):
    """An irreversible commitment that an external system has acknowledged.
    Append-only in state — never rewritten."""

    confirmation_id: str
    """Vendor-issued."""
    idempotency_key: str
    """→ the `Hold` it was promoted from."""
    domain: Domain
    confirmed_at: datetime
    cancellation_deadline: datetime | None = None


class SagaEvent(ResortFlowModel):
    """An append-only record of a step in a multi-system transaction, used
    to reconstruct what happened and to drive recovery. Mirrored to
    Postgres so compensation survives process death."""

    event_id: str
    saga_id: str
    """Spans multiple turns and multiple traces."""
    sequence: int
    """Monotonic within a saga."""
    kind: str
    """`hold_placed`, `commit_attempted`, `commit_failed`, `compensated`, …"""
    domain: Domain | None = None
    occurred_at: datetime
    detail: dict[str, str] = Field(default_factory=dict)
    """Bounded scalars only — no nested payloads."""


class PlanTask(ResortFlowModel):
    task_id: str
    domain: Domain
    depends_on: list[str] = Field(default_factory=list)
    """Task ids; expresses the fan-out/sequence boundary."""
    constraints: dict[str, str] = Field(default_factory=dict)
    """e.g. dinner ends >=45 min before curtain."""


class Plan(ResortFlowModel):
    """The decomposition of a request into tasks with their dependencies
    and constraints."""

    plan_id: str
    tasks: list[PlanTask] = Field(default_factory=list)
    created_at: datetime


class PolicyFlag(ResortFlowModel):
    """A compliance determination attached to a conversation, recording
    what was enforced and why."""

    flag_id: str
    rule: str
    """Which rule fired."""
    disposition: str
    """`blocked`, `redacted`, `escalated`, `messaged`."""
    jurisdiction: str | None = None
    """`NV` or `MA`."""
    raised_at: datetime


class ToolFailure(ResortFlowModel):
    """A classified tool/vendor failure, recorded append-only in state."""

    domain: Domain
    tool: str
    error_class: ErrorClass
    vendor_code: str | None = None
    """Null for status-only vendors."""
    http_status: int | None = None
    retry_after_seconds: int | None = None
    detail: str
    """Redacted before persistence."""


class Chunk(ResortFlowModel):
    """A retrieved grounding fragment. Dedupe key for
    `graph.state.dedupe_by_id`: `chunk_id`."""

    chunk_id: str
    tenant_id: str
    """Isolation is enforced at query time, never by the model."""
    content: str
    source_uri: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    """Stale-content filter."""
    content_hash: str


class Citation(ResortFlowModel):
    chunk_id: str
    source_uri: str
    quoted_span: str | None = None


# --- Feature 005 addition: Conflict (data-model.md "New type: Conflict") ----


class Conflict(ResortFlowModel):
    """A detected incompatibility between two or more proposals in the same
    plan. Produced only by the deterministic conflict checker
    (`graph.supervisor`'s `CONFLICT_CHECK` node) — never by an LLM
    (Constitution: DETERMINISM FIRST). Transient: computed each
    conflict-check pass, not carried as its own `ResortFlowState` channel — see
    `data-model.md`'s "New type: Conflict" for why a fourth reducer would be
    redundant with `proposals`."""

    conflict_id: str
    kind: str
    """`schedule_overlap` | `travel_infeasible` | `party_size_mismatch`."""
    proposal_ids: list[str]
    """The two (or more) proposals in conflict."""
    detail: str
    """Human-readable, e.g. "18:30 dinner ends 19:15; 400m/6min travel
    leaves only 4 minutes before 19:19 curtain — insufficient.\""""


# --- Feature 010 addition: tiered intent routing (ADR-055, ADR-056) ---------
#
# `state-schema-amendment-010.md`'s two new `ResortFlowState` keys (`routing`,
# `turn_baseline`) and the enums their fields close over. See
# `specs/010-tiered-intent-routing/data-model.md` for the full field table
# and invariants this module transcribes.


class RequestKind(str, Enum):
    """Exactly one per message (FR-006). `None` on `RoutingDecision.kind` —
    not a seventh member here — means "undecided", which routes to
    `CLARIFY`; it is deliberately not representable as a `RequestKind`
    value so "undecided" can never be silently confused with a real
    decision."""

    BOOK = "book"
    LOOKUP = "lookup"
    MODIFY = "modify"
    FAQ = "faq"
    OUT_OF_SCOPE = "out_of_scope"
    SMALLTALK = "smalltalk"
    BROWSE = "browse"
    """Feature 011: the guest wants to see what is available (venues, events, treatments, rooms, the
    resort's experiences) before choosing. Read-only; see specs/011-browse-requests."""


class RoutingTier(int, Enum):
    """Which tier decided (or last attempted) — FR-029."""

    TOLERANT_MATCH = 0
    KIND_RULES = 1
    LANGUAGE_MODEL = 2


class Tier2Outcome(str, Enum):
    """FR-010's failure matrix, closed. `NOT_CALLED` is the default for
    every decision the deterministic tiers resolved with high confidence —
    `RoutingDecision.tier == LANGUAGE_MODEL` implies this is never
    `NOT_CALLED` (an invariant enforced below)."""

    NOT_CALLED = "not_called"
    DECIDED = "decided"
    INVALID = "invalid"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


HIGH_CONFIDENCE: Final[float] = 0.7
"""FR-009's confidence gate: at or above this, the language-model tier is
never invoked."""


class BrowseFilters(ResortFlowModel):
    """Feature 011 (data-model.md): the narrowing a guest gave while browsing. All optional; an all-`None`
    value is "no filters". `dietary` is the structured enum, never free text (hard rule) — browse only
    *narrows* by it and never asserts a venue is safe."""

    cuisine: str | None = None
    price_band: str | None = None
    dietary: DietaryRestriction | None = None
    category: str | None = None
    party_size: int | None = None


class DateWindow(ResortFlowModel):
    """Feature 011: an inclusive date window and how it was resolved, so the reply can say it (FR-018).
    `needs_dates` is the marker for "my stay" with no stay on record — the node asks instead of assuming."""

    start: date
    end: date
    source: Literal["stated", "default", "stay_period", "needs_dates"]

    @model_validator(mode="after")
    def _ordered(self) -> DateWindow:
        if self.end < self.start:
            raise ValueError("window end must not precede its start")
        return self


class RoutingDecision(ResortFlowModel):
    """One request's routing decision (data-model.md's `RoutingDecision`).
    Written exactly once per turn, by `INTENT_ROUTER` only — no reducer
    (last-write-wins is correct: no other node ever writes this key in the
    same superstep)."""

    kind: RequestKind | None = None
    """`None` = undecided → `CLARIFY` (FR-010)."""
    services: list[Domain] = Field(default_factory=list)
    secondary_kind: RequestKind | None = None
    """Only `RequestKind.BOOK`, and only when `kind` is `LOOKUP` (FR-020's mixed lookup+book case) or
    `BROWSE` (feature 011's browse+book case); `None` otherwise."""
    secondary_services: list[Domain] = Field(default_factory=list)
    tier: RoutingTier = RoutingTier.TOLERANT_MATCH
    confidence: float = 0.0
    ambiguous: bool = False
    """A typo equidistant from ≥ 2 services (FR-005)."""
    carried_over: bool = False
    """Decided by the one-turn follow-up carry-over rule (FR-008a)."""
    focus_keys: list[str] = Field(default_factory=list)
    """Booking identities (`Hold`/`Confirmation.idempotency_key`) this turn
    refers to: what a lookup matched, or what a carry-over/modify points
    at. Empty otherwise."""
    tier2_outcome: Tier2Outcome = Tier2Outcome.NOT_CALLED
    browse_filters: BrowseFilters | None = None
    """Feature 011: only meaningful when `kind == BROWSE`; `None` on a pre-011 checkpoint."""
    window: DateWindow | None = None
    """Feature 011: only meaningful when `kind == BROWSE`; `None` on a pre-011 checkpoint."""

    @model_validator(mode="after")
    def _invariants(self) -> RoutingDecision:
        if self.kind is None:
            if self.confidence >= HIGH_CONFIDENCE:
                raise ValueError("an undecided (kind=None) decision cannot be high-confidence")
            if self.services:
                raise ValueError("an undecided (kind=None) decision cannot carry services")
        if self.kind in (RequestKind.OUT_OF_SCOPE, RequestKind.SMALLTALK) and self.services:
            raise ValueError(f"{self.kind} decisions must not carry services")
        if self.secondary_kind is not None and (
            self.kind not in (RequestKind.LOOKUP, RequestKind.BROWSE) or self.secondary_kind != RequestKind.BOOK
        ):
            raise ValueError("secondary_kind is only BOOK alongside a LOOKUP or BROWSE primary kind")
        if self.kind != RequestKind.BROWSE and (self.browse_filters is not None or self.window is not None):
            raise ValueError("browse_filters/window are only valid when kind == BROWSE")
        if self.kind == RequestKind.BROWSE and Domain.GAMING in self.services and self.services != [Domain.GAMING]:
            raise ValueError("a browse may include gaming only alone (the gated gaming-only path)")
        if self.tier == RoutingTier.LANGUAGE_MODEL and self.tier2_outcome == Tier2Outcome.NOT_CALLED:
            raise ValueError("tier == LANGUAGE_MODEL requires a tier2_outcome other than NOT_CALLED")
        if Domain.GAMING in self.services and self.kind == RequestKind.MODIFY:
            raise ValueError("gaming is read-only by construction; MODIFY must never include it")
        return self


class TurnBaseline(ResortFlowModel):
    """A snapshot of the booking-record keys/statuses that existed when
    `INTENT_ROUTER` ran this turn (data-model.md's `TurnBaseline`) — what
    "this turn's outcome" is computed against (`graph.bookings.turn_outcomes`,
    research R-12). Written exactly once per turn, by `INTENT_ROUTER` only."""

    confirmation_keys: list[str] = Field(default_factory=list)
    """Sorted, unique `idempotency_key`s of `confirmations` at turn start."""
    hold_statuses: dict[str, HoldStatus] = Field(default_factory=dict)
    """`idempotency_key` → status at turn start."""
    turn_index: int = 1
    """1 on the first routed turn; 1 + the previous baseline's `turn_index`
    thereafter. Not used for reporting in this feature — it is the turn
    identity a future turn-stamped saga event log (ADR-058 option b) would
    stamp on `SagaEvent.detail["turn"]`."""
