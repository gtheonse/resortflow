"""Shared constants and tiny pure helpers every supervisor module depends on."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from graph.domains import default_registry
from graph.models import (
    Domain,
    Hold,
    Proposal,
)
from graph.routing.matcher import match_services
from graph.state import ResortFlowState

GRAPH_VERSION: Final[str] = "5.2"
"""Build-time constant (T021). Compared on resume by major component
(the substring before the first '.') — `contracts/state-schema-amendment-005.md`.
Feature 010 (ADR-056) bumped the MINOR component only: `routing`/`turn_baseline`
are additive `ResortFlowState` keys a pre-010 checkpoint simply lacks (read back as
`None`), which is exactly what a minor bump means here — no major-version
resume-release path is triggered for an existing conversation.
Feature 011 (ADR-060) bumped the MINOR component again, 5.1 -> 5.2: `RoutingDecision` gained two optional
fields (`browse_filters`, `window`) that a 5.1 checkpoint lacks and reads back as `None`."""


def _load_relevance_floor() -> float:
    """Feature 010 (ADR-057): `graph/routing/thresholds.yaml`'s
    `knowledge_relevance_floor`, read once at import — Track C owns this
    file; `rag/eval/calibrate_floor.py` (Track D) only ever prints a
    recommended value, never writes it (analyze finding C2)."""
    import yaml

    path = Path(__file__).resolve().parent.parent / "routing" / "thresholds.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return float(data["knowledge_relevance_floor"])


_RELEVANCE_FLOOR: Final[float] = _load_relevance_floor()

def _detect_domains(text: str) -> list[Domain]:
    """Feature 010 (ADR-055): a thin wrapper over the tier-0 tolerant
    matcher, kept as a named function — `agents/tests/test_injection_structural.py`
    inspects its source to confirm domain detection never consults
    grounding/RAG content. The substring-keyword table this function used
    to hold (`_DOMAIN_KEYWORDS`) is gone; `graph/routing/vocabulary.yaml`
    is the single, reviewable source of service vocabulary now (FR-004)."""
    return match_services(text).services


def _latest_human_text(state: ResortFlowState) -> str:
    for message in reversed(state.get("messages") or []):
        content = getattr(message, "content", None)
        msg_type = getattr(message, "type", None)
        if content and msg_type in (None, "human"):
            return content if isinstance(content, str) else str(content)
    return ""


def _next_saga_sequence(state: ResortFlowState) -> int:
    return len(state.get("saga_log") or []) + 1


def _find_hold_for_proposal(state: ResortFlowState, proposal: Proposal) -> Hold | None:
    if proposal.hold_idempotency_key is None:
        return None
    for hold in state.get("holds", []):
        if hold.idempotency_key == proposal.hold_idempotency_key:
            return hold
    return None


# --- Domain registry (ADR-061) ------------------------------------------------------
# Per-domain behaviour (tools, standalone subgraph, propose-and-hold, confirm, release,
# recovery search, verification) lives in each `agents/<domain>/participant.py`; this
# module only talks to the `DomainRegistry` abstraction (`graph/participants.py`).

_REGISTRY = default_registry()

DEFAULT_TOOLS_FACTORY: dict[Domain, Callable[..., Any]] = _REGISTRY.tools_factory()
"""`tools_factory[domain](tenant_id, guest_id) -> Tools`. Tests override this
whole mapping with fakes (`FakeDiningTools` etc.) — never monkeypatch the
real `Mcp*Tools` classes."""
