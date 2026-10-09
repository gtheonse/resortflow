"""Feature 010, T049 — the console approve/decline flow reports the real
`booking_outcome` (FR-035), never "Confirmed" merely because a resume
finished without raising (analyze finding G1's console-badge bug). Python
only, no browser/JS runtime — drives `ui/app.py`'s real FastAPI routes
against fake dining/entertainment tools, the same "fast unit test" bar
`graph/tests/test_api.py` sets for the production endpoint.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agents.dining.tests.fakes import FakeDiningTools
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.gaming.tests.fakes import FakeGamingTools
from agents.tests.conftest import FIXED_ISO_DATE, thread_config
from graph.models import Domain
from graph.supervisor import DEFAULT_TOOLS_FACTORY, new_session_state


@pytest.fixture
def client(monkeypatch):
    import ui.app  # noqa: F401

    monkeypatch.delenv("POSTGRES_URL", raising=False)

    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    gaming = FakeGamingTools()
    monkeypatch.setitem(DEFAULT_TOOLS_FACTORY, Domain.DINING, lambda t, g: dining)
    monkeypatch.setitem(DEFAULT_TOOLS_FACTORY, Domain.ENTERTAINMENT, lambda t, g: entertainment)
    # Every turn reaches `OFFER_AGENT`/`_apply_offer_agent` (Constitution
    # Principle IV self-exclusion gate) regardless of this test's own
    # domain — without this, the real `DEFAULT_TOOLS_FACTORY` gaming entry
    # makes a live vendor call and fails with a connection error.
    monkeypatch.setitem(DEFAULT_TOOLS_FACTORY, Domain.GAMING, lambda t, g: gaming)

    from ui.app import app

    with TestClient(app) as c:
        yield c, entertainment


def _poll_receipt(client: TestClient, token: str) -> dict:
    for _ in range(20):
        res = client.get(f"/api/receipts/{token}")
        data = res.json()
        if data["status"] != "confirming":
            return data
        import time

        time.sleep(0.1)
    raise AssertionError("receipt never left 'confirming'")


async def _start_turn(client: TestClient, thread_id: str) -> dict:
    from ui.app import _rt

    app_graph = _rt().app_for_thread(thread_id)
    state = new_session_state(
        tenant_id="MGV", guest_id="g1", text=f"dinner and a show on {FIXED_ISO_DATE} at 8pm, 2 tickets"
    )
    cfg = thread_config(thread_id)
    return await app_graph.ainvoke(state, config=cfg)


@pytest.mark.asyncio
async def test_continuing_unknown_thread_id_gives_clear_error_not_a_crash(client):
    """Found live (demo report): a `thread_id` with no matching checkpoint — a stale
    browser tab, a deleted/reset Postgres volume, a mistyped reconnect id, or a client
    that never actually started the conversation — used to reach the graph with only
    `messages` in state (no `tenant_id`/`guest_id`, which only an existing checkpoint or
    `new_session_state` ever supplies), crashing deep inside `intent_router` with a bare
    `KeyError: 'tenant_id'` instead of a clear, actionable error."""
    c, _entertainment = client
    res = c.post(
        "/api/turns",
        json={"thread_id": "never-started-thread-id", "text": "hello"},
    )
    assert res.status_code == 200  # SSE streams 200 even for an in-band error event
    body = res.text
    assert "event: error" in body
    assert "KeyError" not in body
    assert "never-started-thread-id" in body
    assert "start a new conversation" in body.lower()


@pytest.mark.asyncio
async def test_approve_reports_confirmed_badge_outcome(client):
    c, _entertainment = client
    result = await _start_turn(c, "console-outcome-approve")
    assert result["awaiting_approval"] is True

    res = c.post(
        "/api/threads/console-outcome-approve/approve",
        json={"action": "approved", "approved_domains": []},
    )
    token = res.json()["receipt_token"]
    receipt = _poll_receipt(c, token)

    assert receipt["status"] == "succeeded"
    assert receipt["result"]["booking_outcome"]["state"] == "confirmed"


@pytest.mark.asyncio
async def test_decline_reports_released_badge_outcome(client):
    c, _entertainment = client
    result = await _start_turn(c, "console-outcome-decline")
    assert result["awaiting_approval"] is True

    res = c.post(
        "/api/threads/console-outcome-decline/approve",
        json={"action": "declined", "approved_domains": []},
    )
    token = res.json()["receipt_token"]
    receipt = _poll_receipt(c, token)

    assert receipt["status"] == "succeeded"
    assert receipt["result"]["booking_outcome"]["state"] == "released"


@pytest.mark.asyncio
async def test_expired_hold_recovers_via_alternative_never_silently_confirms_the_original(client):
    """The entertainment leg's hold expires before commit; `RECOVERY`
    finds and confirms an alternative (its own proven, pre-existing
    behaviour — `agents/tests/test_hold_revalidation_resume.py`).
    `"confirmed"` is the CORRECT outcome here — this test's job is only to
    prove the reply discloses the substitution rather than silently
    reporting the original as confirmed."""
    c, entertainment = client
    result = await _start_turn(c, "console-outcome-expired")
    assert result["awaiting_approval"] is True

    entertainment.hold_expired_on_revalidate = True
    res = c.post(
        "/api/threads/console-outcome-expired/approve",
        json={"action": "approved", "approved_domains": []},
    )
    token = res.json()["receipt_token"]
    receipt = _poll_receipt(c, token)

    assert receipt["status"] == "succeeded"
    if receipt["result"]["booking_outcome"]["state"] == "confirmed":
        assert "alternative" in receipt["result"]["reply"].lower()
