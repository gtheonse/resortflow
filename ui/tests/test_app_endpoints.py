"""Feature 008: exercises the ui/app.py routes that don't require a live
MCP gateway/sims/Postgres stack -- static page serving, the fault catalog,
arming/clearing a fault, and receipt polling. Routes that invoke the real
supervisor graph (POST /api/turns, the approval flow) need `make up && make
seed`, per graph/demo.py's own documented requirement -- not exercised here.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    # Import first so ui.app's module-level load_dotenv() (feature 008) has
    # already run -- otherwise, if this is the first test in the session to
    # import ui.app, the import below would re-populate POSTGRES_URL from
    # .env right after monkeypatch deletes it (a real ordering bug this test
    # hit once .env loading was added; import-then-delete, not delete-then-
    # import, is what makes the delete actually stick).
    import ui.app  # noqa: F401

    # Forces the in-memory-checkpointer fallback path (ui/runtime.py) rather
    # than attempting a real Postgres connection this sandbox doesn't have.
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    from ui.app import app

    with TestClient(app) as c:
        yield c


def test_index_serves_static_page(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "ResortFlow Verification Console" in res.text


def test_faults_catalog_matches_real_sim_fault_values(client):
    res = client.get("/api/faults")
    assert res.status_code == 200
    data = res.json()
    assert set(data["values"]) == {
        "timeout", "error_500", "rate_limit", "hold_expired", "inventory_race", "webhook_delay",
        "schema_drift",
    }
    assert set(data["domains"]) == {"dining", "entertainment", "stay", "wellness", "gaming"}


def test_arm_and_clear_fault(client):
    res = client.put("/api/fault", json={"thread_id": "t1", "domain": "dining", "value": "hold_expired"})
    assert res.status_code == 200
    assert res.json()["pending"] == {"dining": "hold_expired"}

    res = client.put("/api/fault", json={"thread_id": "t1", "domain": "dining", "value": None})
    assert res.status_code == 200
    assert res.json()["pending"] == {}


def test_arm_fault_rejects_unknown_value(client):
    res = client.put("/api/fault", json={"thread_id": "t1", "domain": "dining", "value": "not_a_real_fault"})
    assert res.status_code == 400


def test_arm_fault_rejects_unknown_domain(client):
    res = client.put("/api/fault", json={"thread_id": "t1", "domain": "spa", "value": "timeout"})
    assert res.status_code == 400


def test_receipt_not_found(client):
    res = client.get("/api/receipts/does-not-exist")
    assert res.status_code == 404


def test_thread_state_unknown_thread_returns_404(client):
    res = client.get("/api/threads/nonexistent-thread-id/state")
    assert res.status_code == 404
