"""Pytest fixtures providing a reset-to-baseline TestClient per sim.

Each sim's SQLite file lives at a fixed path (`sims/<name>/data/state.db`) that
persists across test runs, so every fixture resets that sim to its seeded baseline
(`POST /__sim__/reset`) before handing back the client — this both guarantees test
isolation and doubles as incidental FR-014 exercise on every test run.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def amenities_client() -> Iterator[TestClient]:
    from sims.amenities.main import app

    with TestClient(app) as client:
        client.post("/__sim__/reset")
        yield client


@pytest.fixture
def casino_client() -> Iterator[TestClient]:
    from sims.casino.main import app

    with TestClient(app) as client:
        client.post("/__sim__/reset")
        yield client


@pytest.fixture
def dining_client() -> Iterator[TestClient]:
    from sims.dining.main import app

    with TestClient(app) as client:
        client.post("/__sim__/reset")
        yield client


@pytest.fixture
def pms_client() -> Iterator[TestClient]:
    from sims.pms.main import app

    with TestClient(app) as client:
        client.post("/__sim__/reset")
        yield client


@pytest.fixture
def ticketing_client() -> Iterator[TestClient]:
    from sims.ticketing.main import app

    with TestClient(app) as client:
        client.post("/__sim__/reset")
        yield client


def amenities_auth_headers() -> dict[str, str]:
    from sims.amenities.main import AUTH_PASSWORD, AUTH_USERNAME

    token = base64.b64encode(f"{AUTH_USERNAME}:{AUTH_PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def casino_auth_headers() -> dict[str, str]:
    from sims.casino.main import AUTH_TOKEN

    return {"Authorization": f"Bearer {AUTH_TOKEN}"}


def dining_auth_headers() -> dict[str, str]:
    from sims.dining.main import API_KEY

    return {"X-Dining-Key": API_KEY}


def pms_auth_headers(client: TestClient) -> dict[str, str]:
    from sims.pms.main import CLIENT_ID, CLIENT_SECRET

    resp = client.post(
        "/pms/oauth2/token",
        data={"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
    )
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def ticketing_auth_headers(client: TestClient, body: dict | None = None) -> dict[str, str]:
    from sims.ticketing.main import CLIENT_ID, CLIENT_SECRET, compute_signature

    resp = client.post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
    )
    token = resp.json()["access_token"]
    raw = json.dumps(body if body is not None else {}).encode("utf-8")
    return {"Authorization": f"Bearer {token}", "X-TM-Signature": compute_signature(raw)}
