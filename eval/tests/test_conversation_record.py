"""T038 — proves `build_conversation_record` is a pure projection over an
existing checkpointer's state history (FR-012's "single source of truth"):
since the CLI (`eval/cli.py`) and the scorers both call this same function,
determinism here is what makes "same conversation -> same record" hold
regardless of caller. Exercises a real compiled LangGraph app
(`agents.dining.graph.compile_dining_graph` with `FakeDiningTools` and a
`MemorySaver` checkpointer) rather than a hand-built fixture, so this
actually proves the projection against a real checkpoint history shape, not
just a mock.

Uses the standalone dining subgraph (no policy_preflight node) deliberately
— the full supervisor graph pulls in Presidio/spaCy transitively, which is
known to fail to import in some sandboxes lacking native NLP libraries; the
domain graph alone avoids that dependency entirely.
"""

from __future__ import annotations

from datetime import date

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.dining.graph import compile_dining_graph, new_session_state
from agents.dining.nodes.intent_parser import RuleBasedIntentExtractor
from agents.dining.tests.fakes import FakeDiningTools
from eval.conversation_record import build_conversation_record
from graph.models import HoldStatus

TODAY = date(2026, 9, 11)


def _make_extractor():
    class _Fixed(RuleBasedIntentExtractor):
        def extract(self, text, *, today):
            return super().extract(text, today=TODAY)

    return _Fixed()


@pytest.mark.asyncio
async def test_build_conversation_record_is_a_consistent_pure_projection():
    tools = FakeDiningTools()
    checkpointer = MemorySaver()
    app = compile_dining_graph(
        tools, checkpointer=checkpointer, intent_extractor=_make_extractor(), trace=False
    )
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text="table for 4 tomorrow at 7pm, steakhouse"
    )
    config = {"configurable": {"thread_id": "record-consistency-1"}}
    result = await app.ainvoke(state, config=config)
    assert result["holds"][0].status == HoldStatus.CONFIRMED  # sanity: happy path completed

    first = build_conversation_record(app, config, tenant_id="MGV")
    second = build_conversation_record(app, config, tenant_id="MGV")

    # Same checkpoint history in -> structurally identical record out, called
    # twice: not stateful, not randomized. This is the property FR-012 relies
    # on to treat "the CLI's view" and "the scorer's view" as the same object.
    assert first.conversation_id == second.conversation_id == "record-consistency-1"
    assert first.tenant_id == second.tenant_id == "MGV"
    assert len(first.turns) == len(second.turns) == 1

    turn_a, turn_b = first.turns[0], second.turns[0]
    assert turn_a.input == turn_b.input == "table for 4 tomorrow at 7pm, steakhouse"
    assert turn_a.decisions == turn_b.decisions
    assert turn_a.tool_calls == turn_b.tool_calls
    assert turn_a.output == turn_b.output
    assert turn_a.decisions  # a proposal decision was actually recorded
    assert any(tc["kind"] == "hold" for tc in turn_a.tool_calls)
    assert any(tc["kind"] == "confirm" for tc in turn_a.tool_calls)
