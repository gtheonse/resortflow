"""T055 — Resume-after-restart proof (Phase 4 exit criterion).

Scenario: kill the process after `hold_placement`, before `confirm`.
Restart. Verify the graph resumes at `confirm` (not `START`), places no
duplicate hold, and — per AGENTS.md's hard rule ("never trust
`state["holds"]` on resume; revalidate against the vendor") — that
`confirm` actually calls `check_dining_status` again rather than trusting
the checkpointed `Hold.status == "held"` at face value.

"Process death" is simulated the only way a same-process pytest run can:
a brand new `PostgresSaver` connection, a brand new compiled graph object,
and a brand new `FakeDiningTools` instance sharing the *vendor-side* state
(`holds`/`reservations` dicts) with the pre-restart instance — the vendor
survives a process restart, only the dining subgraph process does not. A
fresh in-memory Python object standing in for "the process died and
restarted" is the honest boundary a unit test can draw; requires Postgres
reachable at `POSTGRES_URL` (skipped otherwise).
"""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest

from agents.dining.graph import compile_dining_graph, new_session_state, postgres_checkpointer
from agents.dining.nodes.intent_parser import RuleBasedIntentExtractor
from agents.dining.tests.fakes import FakeDiningTools
from graph.ledger import PostgresHoldLedger
from graph.models import HoldStatus

TODAY = date(2026, 9, 11)


def _fixed_extractor():
    class _Fixed(RuleBasedIntentExtractor):
        def extract(self, text, *, today):
            return super().extract(text, today=TODAY)

    return _Fixed()


def _postgres_reachable() -> bool:
    import os

    try:
        with psycopg.connect(os.environ["POSTGRES_URL"], connect_timeout=2):
            return True
    except Exception:  # noqa: BLE001 — a live-connectivity probe must never raise
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_reachable(), reason="POSTGRES_URL not reachable — run `docker compose up -d postgres`"
)


class _StopAfterHoldPlacement(RuntimeError):
    """Raised deliberately to simulate a process crash right after
    `hold_placement` writes its checkpoint but before `confirm` runs."""


async def _ledger_status(idempotency_key: str) -> str | None:
    """Read a `hold_ledger` row's status directly — the independent proof
    that `hold_placement`/`confirm` actually wrote through
    `graph.ledger.PostgresHoldLedger` (F4-1's fix), not just that
    `state["holds"]` looks right. A test asserting only the checkpointed
    state could pass even if the write-ahead ledger were never touched."""
    async with await psycopg.AsyncConnection.connect(_pg_url()) as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT status FROM hold_ledger WHERE idempotency_key = %s", (idempotency_key,)
        )
        row = await cur.fetchone()
        return row[0] if row else None


def _pg_url() -> str:
    import os

    return os.environ["POSTGRES_URL"]


@pytest.mark.asyncio
async def test_kill_after_hold_placement_resumes_at_confirm_not_start():
    thread_id = f"resume-proof-1-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    vendor_tools = FakeDiningTools()  # stands in for the vendor's own durable state
    ledger = PostgresHoldLedger()

    # --- "Process A": run up to (and including) hold_placement, then die ---
    async with postgres_checkpointer() as checkpointer:
        app = compile_dining_graph(
            vendor_tools,
            checkpointer=checkpointer,
            intent_extractor=_fixed_extractor(),
            trace=False,
            ledger=ledger,
        )
        state = new_session_state(
            tenant_id="MGV", guest_id="g1",
            text="table for 4 tomorrow at 7pm, steakhouse",
            session_id=thread_id,
        )

        async def crash_after_hold(*args, **kwargs):
            raise _StopAfterHoldPlacement

        # Monkeypatch confirm_reservation on the shared vendor double so the
        # graph provably cannot reach a successful confirm in this "process".
        original_confirm = vendor_tools.confirm_reservation
        vendor_tools.confirm_reservation = crash_after_hold  # type: ignore[method-assign]

        with pytest.raises(_StopAfterHoldPlacement):
            await app.ainvoke(state, config=config)

        vendor_tools.confirm_reservation = original_confirm  # restore for "process B"

        # Prove the checkpoint really persisted past hold_placement: a hold
        # exists in Postgres-backed state already, before any resume call.
        snapshot = await app.aget_state(config)
        assert snapshot.values["holds"]
        assert snapshot.values["holds"][0].status == HoldStatus.HELD
        assert snapshot.values["confirmations"] == []
        pre_restart_next = snapshot.next
        assert pre_restart_next == ("confirm",)

        hold_key = snapshot.values["holds"][0].idempotency_key
        # F4-1: the write-ahead ledger, independent of the checkpointer,
        # must already show this hold as HELD — this is what a reaper
        # would see if the process really had died here.
        assert await _ledger_status(hold_key) == "HELD"

    # --- "Process B": fresh connection, fresh compiled graph, resume -------
    vendor_tools.calls.clear()  # only count calls made by the resumed process
    async with postgres_checkpointer() as checkpointer_b:
        app_b = compile_dining_graph(
            vendor_tools,
            checkpointer=checkpointer_b,
            intent_extractor=_fixed_extractor(),
            trace=False,
            ledger=ledger,
        )
        result = await app_b.ainvoke(None, config=config)

    # Resumed at `confirm`, not `START`: no second `hold_table` call.
    assert "hold_table" not in vendor_tools.calls
    # Hard rule: never trust state["holds"] on resume — confirm revalidates.
    assert "check_dining_status" in vendor_tools.calls
    assert len(result["confirmations"]) == 1
    assert result["holds"][0].status == HoldStatus.CONFIRMED
    # Exactly one hold was ever placed for this session.
    assert len({h.idempotency_key for h in result["holds"]}) == 1
    # The ledger reflects the promotion too — proof this wasn't only a
    # `state["holds"]` update.
    assert await _ledger_status(hold_key) == "CONFIRMED"


@pytest.mark.asyncio
async def test_resume_revalidates_and_escalates_when_vendor_hold_gone():
    """The revalidation the hard rule requires must actually be able to
    change the outcome — otherwise it's a call that happens to be made but
    changes nothing, which is not revalidation."""
    thread_id = f"resume-proof-2-{uuid.uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}
    vendor_tools = FakeDiningTools()
    ledger = PostgresHoldLedger()

    async with postgres_checkpointer() as checkpointer:
        app = compile_dining_graph(
            vendor_tools,
            checkpointer=checkpointer,
            intent_extractor=_fixed_extractor(),
            trace=False,
            ledger=ledger,
        )
        state = new_session_state(
            tenant_id="MGV", guest_id="g1",
            text="table for 4 tomorrow at 7pm, steakhouse",
            session_id=thread_id,
        )

        async def crash_after_hold(*args, **kwargs):
            raise _StopAfterHoldPlacement

        original_confirm = vendor_tools.confirm_reservation
        vendor_tools.confirm_reservation = crash_after_hold  # type: ignore[method-assign]
        with pytest.raises(_StopAfterHoldPlacement):
            await app.ainvoke(state, config=config)
        vendor_tools.confirm_reservation = original_confirm

        hold_key = (await app.aget_state(config)).values["holds"][0].idempotency_key

    # Simulate the vendor-side hold independently expiring while the
    # subgraph process was down (e.g. TTL elapsed) — this is exactly the
    # scenario blind trust in the checkpointed Hold would miss.
    vendor_tools.holds.clear()

    async with postgres_checkpointer() as checkpointer_b:
        app_b = compile_dining_graph(
            vendor_tools,
            checkpointer=checkpointer_b,
            intent_extractor=_fixed_extractor(),
            trace=False,
            ledger=ledger,
        )
        result = await app_b.ainvoke(None, config=config)

    assert result["confirmations"] == []
    assert result["awaiting_approval"] is True
    # F4-1: the ledger independently reflects the expiry too.
    assert await _ledger_status(hold_key) == "EXPIRED"
    assert result["approval_scope"].startswith("hold_expired:")
    assert result["holds"][0].status == HoldStatus.EXPIRED
