"""The saga engine: ordered commit, independent verification, recovery and release. Registry-driven — no per-domain branches."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage

from graph import nodes as N
from graph.errors import ErrorClass
from graph.ledger import HoldLedger
from graph.models import (
    Confirmation,
    Domain,
    Hold,
    HoldStatus,
    Proposal,
    SagaEvent,
    ToolFailure,
)
from graph.participants import ConfirmContext
from graph.state import ResortFlowState
from graph.supervisor.common import _REGISTRY, _find_hold_for_proposal, _next_saga_sequence


def make_release_node(tools_factory: dict[Domain, Callable[..., Any]], ledger: HoldLedger) -> Any:
    """T065 (ADR-026, spec.md FR-022): proactively release every outstanding
    hold for the itinerary immediately — covers both an explicit decline and
    an explicit guest cancellation, which this topology treats as the same
    event (no separate signal)."""

    async def release(state: ResortFlowState) -> dict[str, Any]:
        released_holds: list[Hold] = []
        saga_events: list[SagaEvent] = []
        for hold in state.get("holds", []):
            if hold.status != HoldStatus.HELD or hold.hold_id is None:
                continue
            factory = tools_factory.get(hold.domain)
            participant = _REGISTRY.participant(hold.domain)
            if factory is None or participant is None:
                continue
            tools = factory(state["tenant_id"], state["guest_id"])
            try:
                await participant.release(tools, hold)
            except Exception:  # noqa: BLE001, S110 — best-effort; vendor TTL is the backstop either way
                pass
            # The durable ledger row, independent of conversation state
            # (Constitution Principle V) — FR-022 is about this row, not
            # only the in-graph `Hold.status` field.
            await ledger.mark_released(idempotency_key=hold.idempotency_key)
            released_holds.append(hold.model_copy(update={"status": HoldStatus.RELEASED}))
            saga_events.append(
                SagaEvent(
                    event_id=uuid.uuid4().hex,
                    saga_id=state["saga_id"],
                    sequence=_next_saga_sequence(state) + len(saga_events),
                    kind="released",
                    domain=hold.domain,
                    occurred_at=datetime.now(UTC),
                    detail={"idempotency_key": hold.idempotency_key},
                )
            )
        update: dict[str, Any] = {"messages": [AIMessage(content="No problem — everything has been released.")]}
        if released_holds:
            update["holds"] = released_holds
        if saga_events:
            update["saga_log"] = saga_events
        return update

    return release


def make_commit_node(tools_factory: dict[Domain, Callable[..., Any]], ledger: HoldLedger) -> Any:
    """T019 — sequential, saga-ordered commit. Each domain's held proposal is
    confirmed one at a time in the registry's commit order; a failure does not abort
    the loop (later domains still get their chance) — `CommitVerify` is what
    decides whether the overall outcome needs `Recovery`."""

    async def commit(state: ResortFlowState) -> dict[str, Any]:
        confirmations: list[Confirmation] = []
        failures: list[ToolFailure] = []
        updated_holds: list[Hold] = []
        saga_events: list[SagaEvent] = []

        by_domain: dict[Domain, list[tuple[Proposal, Hold]]] = {}
        for p in state.get("proposals", []):
            hold = _find_hold_for_proposal(state, p)
            if hold is not None and hold.status == HoldStatus.HELD:
                by_domain.setdefault(p.domain, []).append((p, hold))

        for domain in _REGISTRY.commit_order():
            factory = tools_factory.get(domain)
            participant = _REGISTRY.participant(domain)
            for proposal, hold in by_domain.get(domain, []):
                seq = _next_saga_sequence(state) + len(saga_events)
                saga_events.append(
                    SagaEvent(
                        event_id=uuid.uuid4().hex, saga_id=state["saga_id"], sequence=seq,
                        kind="commit_attempted", domain=domain, occurred_at=datetime.now(UTC),
                        detail={"idempotency_key": hold.idempotency_key},
                    )
                )
                try:
                    tools = factory(state["tenant_id"], state["guest_id"]) if factory else None
                    if tools is None or participant is None:
                        raise RuntimeError(f"no tools factory registered for {domain}")
                    result = await participant.confirm(
                        tools, ledger,
                        ConfirmContext(proposal=proposal, hold=hold, guest_id=state["guest_id"], saga_id=state["saga_id"]),
                    )
                except Exception as exc:  # noqa: BLE001 — every domain's own ToolError type differs
                    detail = getattr(exc, "detail", str(exc))
                    failures.append(
                        ToolFailure(domain=domain, tool="commit", error_class=ErrorClass.TERMINAL, detail=detail)
                    )
                    saga_events.append(
                        SagaEvent(
                            event_id=uuid.uuid4().hex, saga_id=state["saga_id"], sequence=seq + 1,
                            kind="commit_failed", domain=domain, occurred_at=datetime.now(UTC),
                            detail={"idempotency_key": hold.idempotency_key, "detail": str(detail)},
                        )
                    )
                    continue

                domain_confirmations = result.get("confirmations") or []
                domain_holds = result.get("holds") or []
                domain_failures = result.get("failures") or []
                confirmations.extend(domain_confirmations)
                updated_holds.extend(domain_holds)
                failures.extend(domain_failures)
                if domain_confirmations:
                    saga_events.append(
                        SagaEvent(
                            event_id=uuid.uuid4().hex, saga_id=state["saga_id"], sequence=seq + 1,
                            kind="confirmed", domain=domain, occurred_at=datetime.now(UTC),
                            detail={"idempotency_key": hold.idempotency_key},
                        )
                    )
                else:
                    # The domain's own confirm node revalidated and found the
                    # hold no longer good (HOLD_EXPIRED) or the vendor confirm
                    # call itself failed — either way, no Confirmation was
                    # produced, so this is a saga-level commit failure.
                    detail = domain_failures[0].detail if domain_failures else "confirm produced no result"
                    saga_events.append(
                        SagaEvent(
                            event_id=uuid.uuid4().hex, saga_id=state["saga_id"], sequence=seq + 1,
                            kind="commit_failed", domain=domain, occurred_at=datetime.now(UTC),
                            detail={"idempotency_key": hold.idempotency_key, "detail": str(detail)},
                        )
                    )

        return {
            "confirmations": confirmations,
            "failures": failures,
            "holds": updated_holds,
            "saga_log": saga_events,
        }

    return commit


def make_commit_verify_node(tools_factory: dict[Domain, Callable[..., Any]]) -> Any:
    """T019/T074 — every commit independently verified. A domain in the plan
    whose hold never reached `CONFIRMED` in `Commit`'s own pass is, by
    definition, unverified (structural check, every domain). Entertainment
    additionally gets a genuine second vendor round-trip (see module
    docstring) — a mismatch there is NOT routed through `RECOVERY`: a
    `Confirmation` is already permanently recorded in the append-only
    `confirmations` channel (frozen reducer, `graph/state.py` — it cannot be
    retracted), so attempting an automated alternative on top of an
    ambiguous vendor state risks a real double-booking. A verification
    mismatch escalates straight to `HUMAN_HANDOFF` for manual reconciliation
    instead (Constitution Principle III's dead-letter-and-escalate
    requirement applies to this anomaly too, not only to a clean
    `commit_failed`)."""

    async def commit_verify(state: ResortFlowState) -> dict[str, Any]:
        saga_events: list[SagaEvent] = []
        for confirmation in state.get("confirmations", []):
            participant = _REGISTRY.participant(confirmation.domain)
            factory = tools_factory.get(confirmation.domain)
            if participant is None or factory is None:
                continue
            tools = factory(state["tenant_id"], state["guest_id"])
            verified = await participant.verify(tools, confirmation)
            if verified is None or verified:  # None: no independent vendor check exists (disclosed gap)
                continue
            saga_events.append(
                SagaEvent(
                    event_id=uuid.uuid4().hex, saga_id=state["saga_id"],
                    sequence=_next_saga_sequence(state) + len(saga_events),
                    kind="verification_mismatch", domain=confirmation.domain, occurred_at=datetime.now(UTC),
                    detail={
                        "confirmation_id": confirmation.confirmation_id,
                        "reason": "independent vendor round-trip did not confirm this commitment",
                    },
                )
            )
        if not saga_events:
            return {}
        return {
            "escalate": True,
            "escalate_reason": "compensation_failed",
            "saga_log": saga_events,
        }

    return commit_verify


def _route_from_commit_verify(state: ResortFlowState) -> str:
    if state.get("escalate"):
        return N.HUMAN_HANDOFF
    recent_kinds = {e.kind for e in (state.get("saga_log") or [])[-10:]}
    return N.RECOVERY if "commit_failed" in recent_kinds else N.POLICY_POSTFLIGHT


def make_recovery_node(tools_factory: dict[Domain, Callable[..., Any]], ledger: HoldLedger) -> Any:
    """T020/T072 — on partial commit failure, search a real, domain-specific
    alternative for every failed mutating domain (generalized from dining-
    only per `/speckit-converge`'s C1 finding against FR-013), preserving
    every already-committed piece. A domain with no alternative found (or no
    searcher registered at all) escalates — the dead-letter path is the
    existing Postgres-mirrored `saga_log`, filtered by `escalate_reason`,
    per ADR-027."""

    async def recovery(state: ResortFlowState) -> dict[str, Any]:
        # Option B of two considered (demo report, plan recorded in
        # specs/010-tiered-intent-routing/research.md): this node has no
        # edge back to `COMMIT` (see `build_supervisor_graph`'s
        # `COMMIT_VERIFY`/`RECOVERY` conditional-edge maps) — an alternative
        # found here is *never* itself held, committed, or verified. It used
        # to be treated as a resolved outcome (`escalate` stayed `False`)
        # whenever a candidate was merely found, silently orphaning that
        # proposal forever with no one — guest or human — ever told whether
        # it worked. Every recovery pass now escalates unconditionally: a
        # found alternative is reported (by name, in the saga log and the
        # message below) as a *lead* for the human who picks this up, not a
        # promise. Option A (loop back through `COMMIT`, bounded by a
        # retry-count guard mirroring `PLANNER`'s `MAX_REPLAN_COUNT`, so the
        # alternative gets the same real two-phase-mutation guarantee as
        # everything else) is the architecturally complete fix, deliberately
        # not done here — it needs its own guard logic and re-verification
        # that `COMMIT`'s re-entry doesn't re-touch already-succeeded
        # domains, which is a bigger, separate change.
        recent = (state.get("saga_log") or [])[-10:]
        failed_domains = {e.domain for e in recent if e.kind == "commit_failed" and e.domain is not None}

        new_proposals: list[Proposal] = []
        messages: list[AIMessage] = []
        saga_events: list[SagaEvent] = []

        for domain in failed_domains:
            failed_proposal = next(
                (p for p in state.get("proposals", []) if p.domain == domain), None
            )
            participant = _REGISTRY.participant(domain)
            alt: Proposal | None = None
            if participant is not None:
                tools = tools_factory[domain](state["tenant_id"], state["guest_id"])
                alt = await participant.find_alternative(tools, failed_proposal)

            if alt is None:
                saga_events.append(
                    SagaEvent(
                        event_id=uuid.uuid4().hex, saga_id=state["saga_id"],
                        sequence=_next_saga_sequence(state) + len(saga_events),
                        kind="compensation_failed", domain=domain, occurred_at=datetime.now(UTC),
                        detail={
                            "reason": "no_alternative_slot_found" if participant else "no_alternative_search_available_for_domain"
                        },
                    )
                )
                messages.append(
                    AIMessage(
                        content=f"Your original {domain.value} booking couldn't be confirmed, and no alternative was found."
                    )
                )
                continue

            # Kept in `proposals` (not committed, not held — see the
            # function docstring above) purely so the human handling the
            # hand-off can see the full candidate, not just its id/title in
            # the saga log below.
            new_proposals.append(alt)
            messages.append(
                AIMessage(
                    content=(
                        f"Your original {domain.value} booking couldn't be confirmed — a possible "
                        f"alternative ({alt.title}) was found; a team member will confirm it with you."
                    )
                )
            )
            saga_events.append(
                SagaEvent(
                    event_id=uuid.uuid4().hex, saga_id=state["saga_id"],
                    sequence=_next_saga_sequence(state) + len(saga_events),
                    kind="compensation_lead_found", domain=domain, occurred_at=datetime.now(UTC),
                    detail={"alternative_proposal_id": alt.proposal_id, "alternative_title": alt.title},
                )
            )

        return {
            "escalate": True,
            "escalate_reason": "compensation_failed",
            "proposals": new_proposals,
            "messages": messages,
            "saga_log": saga_events,
        }

    return recovery


def _route_from_recovery(state: ResortFlowState) -> str:
    # Option B (demo report): `recovery()` now always sets `escalate=True`
    # (see its own docstring) — this always routes to `HUMAN_HANDOFF`.
    # `POLICY_POSTFLIGHT` is kept as the destination map's other branch
    # only because Option A (looping back through `COMMIT` instead of
    # escalating) would need it again; removing it now would just have to
    # be re-added then.
    return N.HUMAN_HANDOFF if state.get("escalate") else N.POLICY_POSTFLIGHT
