"""Domain-participant abstractions for the supervisor (ADR-061).

`graph/` owns these interfaces; each `agents/<domain>/participant.py` implements
them, so the orchestrator depends on abstractions and never on a domain's
internal nodes, vendor verbs or id parsers (Dependency Inversion).

Two narrow protocols, not one wide one (Interface Segregation):

- `StandaloneDomain` — every domain, gaming included: its own tools and its own
  complete compiled subgraph (the `DETERMINISTIC` path).
- `SagaParticipant` — mutating domains only: the two-phase hold/confirm/release
  contract plus recovery and independent verification. Gaming is read-only
  (AGENTS.md hard rule) and deliberately cannot implement it, so it can never be
  registered into a saga.

Vendor vocabulary (`release_seat_hold` vs `release_hold`) stays inside each
participant: an anti-corruption layer over the frozen MCP/OpenAPI contracts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, runtime_checkable

from langgraph.checkpoint.base import BaseCheckpointSaver

from graph.ledger import HoldLedger
from graph.models import Confirmation, Domain, Hold, Proposal
from graph.state import ResortFlowState

ToolsT = TypeVar("ToolsT")


@dataclass(frozen=True)
class ConfirmContext:
    """The minimal slice a domain's confirm step needs — what every domain's
    own `confirm` node reads, and nothing else."""

    proposal: Proposal
    hold: Hold
    guest_id: str
    saga_id: str

    def as_state(self) -> dict[str, Any]:
        return {
            "selected_proposal_id": self.proposal.proposal_id,
            "proposals": [self.proposal],
            "holds": [self.hold],
            "guest_id": self.guest_id,
            "saga_id": self.saga_id,
        }


@runtime_checkable
class StandaloneDomain(Protocol[ToolsT]):
    domain: Domain

    def tools(self, tenant_id: str, guest_id: str) -> ToolsT: ...

    def compile(self, tools: ToolsT, checkpointer: BaseCheckpointSaver[Any] | None) -> Any: ...


@runtime_checkable
class SagaParticipant(StandaloneDomain[ToolsT], Protocol[ToolsT]):
    commit_rank: int
    """Sequential commit order, scarcest/least flexible first (research.md)."""

    async def propose_and_hold(self, state: ResortFlowState, tools: ToolsT, ledger: HoldLedger) -> dict[str, Any]:
        """Phase 1 — a `ResortFlowState` delta (never a bare tuple) so reducer
        semantics are preserved. Must not set `awaiting_approval`/`approval_scope`."""
        ...

    async def confirm(self, tools: ToolsT, ledger: HoldLedger, ctx: ConfirmContext) -> dict[str, Any]:
        """Phase 2 — revalidates against the vendor before confirming (hard rule)."""
        ...

    async def release(self, tools: ToolsT, hold: Hold) -> None:
        """Compensation — idempotent; translates to the vendor's own release verb."""
        ...

    async def find_alternative(self, tools: ToolsT, failed: Proposal | None) -> Proposal | None: ...

    async def verify(self, tools: ToolsT, confirmation: Confirmation) -> bool | None:
        """`True`/`False` from an independent vendor round-trip, or `None` when
        the vendor publishes no status check (a disclosed gap, not a pass)."""
        ...


class DomainRegistry:
    """Single lookup point for per-domain behaviour. Replaces nine per-domain
    tables and `if domain is ...` chains that used to live in the supervisor."""

    def __init__(self) -> None:
        self._domains: dict[Domain, StandaloneDomain[Any]] = {}

    def register(self, participant: StandaloneDomain[Any]) -> None:
        self._domains[participant.domain] = participant

    def standalone(self, domain: Domain) -> StandaloneDomain[Any] | None:
        return self._domains.get(domain)

    def participant(self, domain: Domain) -> SagaParticipant[Any] | None:
        found = self._domains.get(domain)
        return found if isinstance(found, SagaParticipant) else None

    def fanout_domains(self) -> tuple[Domain, ...]:
        """Saga participants in registration order. Order is load-bearing: it fixes
        the fan-out node insertion order, hence the append order of parallel writes."""
        return tuple(d for d in self._domains if self.participant(d) is not None)

    def commit_order(self) -> tuple[Domain, ...]:
        ranked = [p for d in self.fanout_domains() if (p := self.participant(d)) is not None]
        return tuple(p.domain for p in sorted(ranked, key=lambda p: p.commit_rank))

    def tools_factory(self) -> dict[Domain, Callable[..., Any]]:
        """The existing `{domain: (tenant_id, guest_id) -> tools}` override seam —
        tests replace this whole mapping with fakes."""
        return {d: p.tools for d, p in self._domains.items()}
