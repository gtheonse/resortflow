"""Feature 010 — the booking read-model.

Reads `ResortFlowState`'s existing `proposals`/`holds`/`confirmations` channels and
joins them on `idempotency_key` into one deduplicated `BookingRecord` per
booking (research R-12, data-model.md). Used by: the reply composer and
console (`turn_outcomes`), the lookup node (`match_bookings`, `LookupAnswer`),
and the modify hand-off node (`match_bookings`). No new `ResortFlowState` key is
needed — every field already exists.

See `specs/010-tiered-intent-routing/contracts/turn-replies.md` for the
reply-wording contract this module's callers depend on.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date as date_type
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from graph.models import TERMINAL_HOLD_STATUSES, Confirmation, Domain, Hold, Proposal
from graph.routing.temporal import parse_date, parse_time, time_matches


class BookingRecord(BaseModel):
    """One deduplicated booking (data-model.md `BookingRecord`). `key` is
    the shared `idempotency_key` across `Proposal.hold_idempotency_key` /
    `Hold.idempotency_key` / `Confirmation.idempotency_key` — the identity
    `booking_view` dedupes by (FR-033)."""

    model_config = ConfigDict(extra="forbid")

    key: str
    service: Domain
    venue: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    """Feature 011: only set for a stay (its departure); `None` for every service with no end. Sourced from
    the matching `Proposal.ends_at`, which the stay `hold_placement` stamps."""
    party_size: int | None = None
    status: str
    """`"confirmed"`, or the `Hold.status` value (`"pending"`, `"held"`,
    `"released"`, `"expired"`, `"orphan_released"`)."""
    hold_id: str | None = None
    expires_at: datetime | None = None
    """Never set when `status` is terminal (FR-034) — a finished hold has
    nothing left to count down."""
    confirmation_code: str | None = None


def booking_view(state: Mapping[str, Any]) -> list[BookingRecord]:
    """Joins `proposals` + `holds` + `confirmations` on `idempotency_key`
    and returns **one `BookingRecord` per key** — never more, regardless of
    how many raw confirmation entries share that key (research R-12: this
    is what stops today's `_deterministic_core` copy-back bug's duplicated
    confirmations from ever reaching a guest as two bookings).

    Enumeration order: holds first (chronologically the earlier event),
    then any confirmation whose key has no corresponding hold (should not
    happen on the normal booking path, but this function never assumes a
    hold exists — the read-model is defensive by construction, not by
    convention)."""
    holds: list[Hold] = list(state.get("holds") or [])
    proposals: list[Proposal] = list(state.get("proposals") or [])
    confirmations: list[Confirmation] = list(state.get("confirmations") or [])

    proposals_by_key: dict[str, Proposal] = {
        p.hold_idempotency_key: p for p in proposals if p.hold_idempotency_key
    }
    holds_by_key: dict[str, Hold] = {h.idempotency_key: h for h in holds}
    # First occurrence wins — the same dedupe convention as
    # `graph.state.dedupe_by_id` uses for retrieved chunks.
    first_confirmation_by_key: dict[str, Confirmation] = {}
    for confirmation in confirmations:
        first_confirmation_by_key.setdefault(confirmation.idempotency_key, confirmation)

    ordered_keys: list[str] = []
    seen: set[str] = set()
    for hold in holds:
        if hold.idempotency_key not in seen:
            seen.add(hold.idempotency_key)
            ordered_keys.append(hold.idempotency_key)
    for confirmation in confirmations:
        if confirmation.idempotency_key not in seen:
            seen.add(confirmation.idempotency_key)
            ordered_keys.append(confirmation.idempotency_key)

    records: list[BookingRecord] = []
    for key in ordered_keys:
        matched_hold: Hold | None = holds_by_key.get(key)
        proposal = proposals_by_key.get(key)
        matched_confirmation: Confirmation | None = first_confirmation_by_key.get(key)

        if proposal is not None:
            service = proposal.domain
            venue = proposal.title
            starts_at = proposal.starts_at
            ends_at = proposal.ends_at if proposal.domain == Domain.STAY else None
            party_size = proposal.party_size
        else:
            # Defensive fallback — the normal booking path always leaves a
            # matching proposal behind, but this read-model must not raise
            # on a fixture/edge case that omits one. `matched_confirmation`
            # is guaranteed non-None here: this branch only runs when
            # `proposal is None`, and `ordered_keys` only ever contains a
            # key from `holds` or `confirmations` — so if `matched_hold` is
            # also None, `matched_confirmation` must be the one that
            # produced this key.
            assert matched_hold is not None or matched_confirmation is not None
            service = (
                matched_hold.domain if matched_hold is not None else matched_confirmation.domain  # type: ignore[union-attr]
            )
            venue = service.value
            starts_at = None
            ends_at = None
            party_size = None

        if matched_confirmation is not None:
            status = "confirmed"
        elif matched_hold is not None:
            status = matched_hold.status.value
        else:  # pragma: no cover — a confirmation always implies "confirmed" above
            status = "confirmed"

        terminal = matched_hold is not None and matched_hold.status in TERMINAL_HOLD_STATUSES
        records.append(
            BookingRecord(
                key=key,
                service=service,
                venue=venue,
                starts_at=starts_at,
                ends_at=ends_at,
                party_size=party_size,
                status=status,
                hold_id=matched_hold.hold_id if matched_hold is not None else None,
                expires_at=matched_hold.expires_at if (matched_hold is not None and not terminal) else None,
                confirmation_code=matched_confirmation.confirmation_id if matched_confirmation is not None else None,
            )
        )
    return records


def stay_period(records: list[BookingRecord]) -> tuple[date_type, date_type] | None:
    """Feature 011 (research R-04, FR-019): the arrival and departure of the guest's most recent **held or
    confirmed** room in this conversation, or `None`. Derived from the booking view only — never from message
    text, never assumed — so "during my stay" cannot disagree with what a lookup would report. A released,
    expired or dateless stay does not count, and a later inactive stay does not mask an earlier active one."""
    for record in reversed(records):
        if (
            record.service == Domain.STAY
            and record.status in ("confirmed", "held")
            and record.starts_at is not None
            and record.ends_at is not None
        ):
            return (record.starts_at.date(), record.ends_at.date())
    return None


def match_bookings(
    records: list[BookingRecord],
    *,
    services: list[Domain],
    text: str,
    today: date_type,
) -> list[BookingRecord]:
    """Pure, deterministic booking lookup matcher (FR-014, research R-10;
    analyze finding I1 — shared by US1's follow-up carry-over, US2's
    `BOOKING_LOOKUP`, and US4's modify hand-off, so it lives here in
    Foundational rather than in any one of them).

    - `services` given: keep only records for those services. Empty
      `services` means "the guest didn't name a service" — every service
      stays in play.
    - A date parsed from `text` (`graph.routing.temporal.parse_date`):
      keep only records whose `starts_at` falls on that date. A time
      parsed alongside it further narrows to records whose `starts_at`
      matches that time (bare-hour AM/PM tolerant, `time_matches`).
    - Neither a service nor a date/time is found in `text`: every record
      is kept (a general "what have I booked?" question)."""
    candidates = records
    if services:
        wanted = set(services)
        candidates = [r for r in candidates if r.service in wanted]

    target_date = parse_date(text, today=today)
    if target_date is not None:
        candidates = [
            r for r in candidates if r.starts_at is not None and r.starts_at.date() == target_date
        ]
        candidates = [
            r
            for r in candidates
            if r.starts_at is None or time_matches(text, (r.starts_at.hour, r.starts_at.minute))
            or _time_token_absent(text)
        ]
    return candidates


def _time_token_absent(text: str) -> bool:
    """True when `text` names no time at all (so a date-only match is not
    further, spuriously, filtered by a time it never mentioned)."""
    return parse_time(text) is None


def turn_outcomes(state: Mapping[str, Any]) -> list[BookingRecord] | None:
    """Feature 010 (FR-031, research R-12, contracts/turn-replies.md):
    "this turn's outcome" — new confirmations, plus holds that are new or
    whose status changed, compared against `state["turn_baseline"]` (the
    snapshot `INTENT_ROUTER` took at the start of this turn).

    Returns `None` — never a guessed answer — when `turn_baseline` is
    absent (a pre-010 conversation's first post-upgrade turn): reporting
    "every record is new" would report an old confirmation as confirmed by
    this turn, which FR-031 forbids outright. The reply composer
    (`_compose_respond_message`) falls back to the lookup-style "here's
    where things stand" presentation in that case instead
    (`contracts/turn-replies.md`).

    Pulled forward from its nominal US6 phase into US1's build, because
    `INTENT_ROUTER` (T043) needs it to determine one-turn follow-up
    carry-over eligibility (FR-008a) — the same function both consumers
    need, not two divergent ones."""
    baseline = state.get("turn_baseline")
    if baseline is None:
        return None
    outcomes: list[BookingRecord] = []
    for record in booking_view(state):
        if record.status == "confirmed":
            if record.key not in set(baseline.confirmation_keys):
                outcomes.append(record)
        else:
            prior_status = baseline.hold_statuses.get(record.key)
            if prior_status is None or prior_status != record.status:
                outcomes.append(record)
    return outcomes


class LookupAnswer(BaseModel):
    """Feature 010 US2 (FR-014 to FR-016). `recheck` covers only records
    that were `held`/`pending` when matched — a confirmed item is not
    re-checked (per spec), and a released/expired one has nothing to
    re-check."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    matched: list[BookingRecord]
    all_records: list[BookingRecord]
    """Every non-terminal-or-confirmed... no: every record the guest
    currently has (FR-016's "lists the bookings that do exist"), used when
    `matched` is empty."""
    recheck: dict[str, str]
    """`key` -> `"active" | "not_active" | "unverifiable" | "error"`."""


async def build_lookup_answer(
    state: Mapping[str, Any],
    *,
    services: list[Domain],
    text: str,
    today: date_type,
    dining_tools: Any | None,
) -> LookupAnswer:
    """FR-014/FR-015: matches deterministically (`match_bookings`, no
    model), then live-re-checks any matched **dining** hold before it is
    ever reported (`DiningTools.check_dining_status`) — the only service
    whose gateway exposes a hold-status tool (research R-10); the other
    three are reported as "status could not be confirmed right now"
    rather than silently claimed held."""
    records = booking_view(state)
    matched = match_bookings(records, services=services, text=text, today=today)
    recheck: dict[str, str] = {}
    for record in matched:
        if record.status not in ("held", "pending"):
            continue
        if record.service is Domain.DINING and record.hold_id and dining_tools is not None:
            try:
                dining_status = await dining_tools.check_dining_status(kind="hold", hold_id=record.hold_id)
            except Exception:  # noqa: BLE001 — a re-check failure is reported, never raised to the guest
                recheck[record.key] = "error"
                continue
            active = dining_status.hold is not None and getattr(dining_status.hold, "status", None) == "active"
            recheck[record.key] = "active" if active else "not_active"
        else:
            recheck[record.key] = "unverifiable"
    return LookupAnswer(matched=matched, all_records=records, recheck=recheck)
