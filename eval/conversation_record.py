"""T009 — the Conversation Record projection (data-model.md).

A read-oriented view built from the *existing* checkpointer's state
history — not a second mutable state store beside the encrypted checkpointer
(Constitution Principle V, ADR-045's explicit boundary). This is the single
source both `eval/cli.py` (FR-011, diagnosis) and the scoring modules
(FR-012, `eval/retrieval_scorer.py`/`eval/trajectory_scorer.py`) read from —
built once, consumed twice, never duplicated.

**Disclosed limitation**: per-step cost/latency here comes from the
`SpanLookup` hook, which has no live backend to query against in this build
(no OTLP collector with a query API is stood up yet — the Deep Dive doc's
fuller design, deferred per plan.md's Complexity Tracking). `cost`/`latency`
fields default to `None` until a real `SpanLookup` implementation exists;
`eval/cost_latency.py` (US4) computes its own budget-relevant timings
directly around tool calls rather than depending on this hook, so this gap
does not block FR-004/005.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Turn:
    input: str
    decisions: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    retrieved_context: list[dict[str, Any]] = field(default_factory=list)
    output: str = ""
    cost: float | None = None
    latency_ms: float | None = None


@dataclass
class ConversationRecord:
    conversation_id: str
    tenant_id: str
    turns: list[Turn] = field(default_factory=list)
    source: str = "evaluated"  # "live" | "evaluated"


class SpanLookup(Protocol):
    """Pluggable hook for per-step cost/latency enrichment from trace data.
    No implementation exists yet in this build (see module docstring) — a
    caller MAY pass one once a queryable trace backend exists."""

    def cost_and_latency_for(self, *, session_id: str, step_index: int) -> tuple[float | None, float | None]: ...


def _human_text(message: Any) -> str | None:
    content = getattr(message, "content", None)
    msg_type = getattr(message, "type", None)
    if content and msg_type in (None, "human"):
        return content if isinstance(content, str) else str(content)
    return None


def _ai_text(message: Any) -> str | None:
    content = getattr(message, "content", None)
    msg_type = getattr(message, "type", None)
    if content and msg_type == "ai":
        return content if isinstance(content, str) else str(content)
    return None


def build_conversation_record(
    app: Any,
    config: dict[str, Any],
    *,
    tenant_id: str,
    source: str = "evaluated",
    span_lookup: SpanLookup | None = None,
) -> ConversationRecord:
    """Build a `ConversationRecord` by replaying a compiled LangGraph app's
    checkpoint history for `config` (`{"configurable": {"thread_id": ...}}`).
    One `Turn` per new human message, aggregating every state delta
    (proposals/holds/confirmations/failures/retrieved) since the previous
    human message into that turn's `decisions`/`tool_calls`/
    `retrieved_context`/`output`.
    """
    thread_id = config["configurable"]["thread_id"]
    # get_state_history yields newest-first; reverse for chronological order.
    snapshots = list(app.get_state_history(config))
    snapshots.reverse()

    turns: list[Turn] = []
    current: Turn | None = None
    seen_proposal_ids: set[str] = set()
    seen_hold_ids: set[str] = set()
    seen_confirmation_count = 0
    seen_failure_count = 0
    seen_retrieved_ids: set[str] = set()
    step_index = -1

    for snapshot in snapshots:
        values = snapshot.values if hasattr(snapshot, "values") else snapshot
        if not isinstance(values, dict):
            continue
        messages = values.get("messages") or []
        for message in messages:
            human = _human_text(message)
            if human is not None and (current is None or human != current.input):
                if current is not None:
                    turns.append(current)
                step_index += 1
                current = Turn(input=human)

        if current is None:
            continue

        for proposal in values.get("proposals") or []:
            pid = getattr(proposal, "proposal_id", None) or str(id(proposal))
            if pid not in seen_proposal_ids:
                seen_proposal_ids.add(pid)
                current.decisions.append(f"proposal:{pid}")

        for hold in values.get("holds") or []:
            hid = getattr(hold, "hold_id", None) or str(id(hold))
            if hid not in seen_hold_ids:
                seen_hold_ids.add(hid)
                current.tool_calls.append({"kind": "hold", "id": hid})

        confirmations = values.get("confirmations") or []
        if len(confirmations) > seen_confirmation_count:
            for confirmation in confirmations[seen_confirmation_count:]:
                current.tool_calls.append(
                    {"kind": "confirm", "id": getattr(confirmation, "confirmation_id", None)}
                )
            seen_confirmation_count = len(confirmations)

        failures = values.get("failures") or []
        if len(failures) > seen_failure_count:
            for failure in failures[seen_failure_count:]:
                current.tool_calls.append(
                    {"kind": "failure", "error_class": getattr(failure, "error_class", None)}
                )
            seen_failure_count = len(failures)

        for chunk in values.get("retrieved") or []:
            cid = getattr(chunk, "id", None) or getattr(chunk, "chunk_id", None) or str(id(chunk))
            if cid not in seen_retrieved_ids:
                seen_retrieved_ids.add(cid)
                current.retrieved_context.append({"id": cid})

        for message in messages:
            ai_text = _ai_text(message)
            if ai_text:
                current.output = ai_text

        if span_lookup is not None:
            session_id = values.get("session_id", thread_id)
            cost, latency_ms = span_lookup.cost_and_latency_for(
                session_id=session_id, step_index=step_index
            )
            current.cost = cost
            current.latency_ms = latency_ms

    if current is not None:
        turns.append(current)

    return ConversationRecord(
        conversation_id=thread_id, tenant_id=tenant_id, turns=turns, source=source
    )
