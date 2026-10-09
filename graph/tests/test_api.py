"""Feature 008.5 gap closure: exercises `graph/api.py` (the production
orchestrator entrypoint) end to end against fake domain tools -- no live MCP
gateway/sims/Postgres/Qdrant stack needed, the same "fast unit test" bar
`ui/tests/test_app_endpoints.py` sets for its own routes (that file's own
docstring: routes needing the real stack aren't exercised there either).

Forces the `MemorySaver`/`NullHoldLedger` fallback path by deleting
`POSTGRES_URL` from the test environment before `graph.api`'s lifespan runs
(mirrors `ui/tests/test_app_endpoints.py`'s `client` fixture exactly), and
substitutes fake dining/entertainment tools for `graph.supervisor
.DEFAULT_TOOLS_FACTORY`'s real `Mcp*Tools` entries via `monkeypatch.setitem`
-- `graph.runtime.GraphRuntime.app_for_thread` compiles against that same
dict object (imported by reference, not copied), so patching entries in
place reaches the compiled graph regardless of which module's name it was
imported through.

The multi-domain "dinner and a show" scenario and its dining+entertainment
fakes are the exact ones proven live in `agents/tests/test_commit_verification
.py`/`agents/tests/test_dietary_propagation_multidomain.py` to reach the
cross-domain `HumanApproval` interrupt and resolve on
`Command(resume={"action": "approved", ...})`. The single-domain
"book me a nice evening" clarify scenario is the one proven live in
`agents/tests/test_clarification_loop.py`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agents.dining.tests.fakes import FakeDiningTools
from agents.entertainment.tests.fakes import FakeEntertainmentTools
from agents.gaming.tests.fakes import FakeGamingTools
from graph.models import Domain
from graph.supervisor import DEFAULT_TOOLS_FACTORY

FIXED_ISO_DATE = "2026-09-12"


@pytest.fixture
def client(monkeypatch):
    # Import first so that if a POSTGRES_URL happened to be exported in this
    # shell, deleting it below is what sticks (mirrors
    # ui/tests/test_app_endpoints.py's own fixture, and the ordering bug its
    # comment documents finding once .env loading entered the picture).
    import graph.api  # noqa: F401

    monkeypatch.delenv("POSTGRES_URL", raising=False)

    dining = FakeDiningTools()
    entertainment = FakeEntertainmentTools()
    gaming = FakeGamingTools()
    monkeypatch.setitem(DEFAULT_TOOLS_FACTORY, Domain.DINING, lambda t, g: dining)
    monkeypatch.setitem(DEFAULT_TOOLS_FACTORY, Domain.ENTERTAINMENT, lambda t, g: entertainment)
    # `OFFER_AGENT`/`_apply_offer_agent` runs on every turn (Constitution
    # Principle IV self-exclusion gate) regardless of domain — without this,
    # the real `DEFAULT_TOOLS_FACTORY` gaming entry makes a live vendor call
    # and fails with a connection error (pre-existing gap, not caused by
    # feature 010 — confirmed by reproducing on the pre-010 baseline).
    monkeypatch.setitem(DEFAULT_TOOLS_FACTORY, Domain.GAMING, lambda t, g: gaming)

    from graph.api import app

    with TestClient(app) as c:
        yield c


def test_healthz(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_new_thread_turn_reaches_approval_then_resumes_on_approve(client):
    res = client.post(
        "/v1/turns",
        json={
            "thread_id": None,
            "tenant_id": "MGV",
            "guest_id": "g1",
            "text": f"dinner and a show on {FIXED_ISO_DATE} at 7pm, party of 2",
        },
    )
    assert res.status_code == 200
    body = res.json()
    thread_id = body["thread_id"]
    assert thread_id
    assert body["interrupt"]["pending"] is True
    assert body["interrupt"]["kind"] == "approval"

    res2 = client.post(
        f"/v1/threads/{thread_id}/approve",
        json={"action": "approved", "approved_domains": []},
    )
    assert res2.status_code == 200
    body2 = res2.json()
    assert body2["thread_id"] == thread_id
    assert body2["escalate"] is False
    assert body2["interrupt"]["pending"] is False
    confirmed = {c["domain"] for c in body2["tool_calls"] if c["kind"] == "confirmed"}
    assert confirmed == {"dining", "entertainment"}


def test_new_thread_turn_clarifies_then_resumes_on_answer(client):
    res = client.post(
        "/v1/turns",
        json={
            "thread_id": None,
            "tenant_id": "MGV",
            "guest_id": "g1",
            "text": "book me a nice evening",
        },
    )
    assert res.status_code == 200
    body = res.json()
    thread_id = body["thread_id"]
    assert body["interrupt"]["pending"] is True
    assert body["interrupt"]["kind"] == "clarify"

    res2 = client.post(
        f"/v1/threads/{thread_id}/clarify",
        json={"answer": f"dinner on {FIXED_ISO_DATE} at 7pm for 2"},
    )
    assert res2.status_code == 200
    body2 = res2.json()
    assert body2["thread_id"] == thread_id
    confirmed = {c["domain"] for c in body2["tool_calls"] if c["kind"] == "confirmed"}
    assert confirmed == {"dining"}


def test_reconnect_after_a_turn_returns_current_state(client):
    res = client.post(
        "/v1/turns",
        json={
            "thread_id": None,
            "tenant_id": "MGV",
            "guest_id": "g1",
            "text": "book me a nice evening",
        },
    )
    thread_id = res.json()["thread_id"]

    res2 = client.get(f"/v1/threads/{thread_id}")
    assert res2.status_code == 200
    body2 = res2.json()
    assert body2["thread_id"] == thread_id
    assert body2["interrupt"]["pending"] is True
    assert body2["interrupt"]["kind"] == "clarify"
    assert isinstance(body2["transcript"], list)
    assert body2["transcript"][0]["content"] == "book me a nice evening"


def test_reconnect_unknown_thread_returns_404(client):
    res = client.get("/v1/threads/does-not-exist")
    assert res.status_code == 404
