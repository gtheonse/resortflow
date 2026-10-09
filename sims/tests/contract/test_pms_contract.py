"""FR-011/SC-001: pms-sim contract-conformance suite, driven directly from the frozen
`docs/contracts/openapi/pms-sim.openapi.yaml` (T012)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from sims.pms.main import CLIENT_ID, CLIENT_SECRET, app
from sims.tests.fixtures.contract_helpers import run_contract_suite


def test_pms_contract_conformance() -> None:
    with TestClient(app) as client:
        token_resp = client.post(
            "/pms/oauth2/token",
            data={"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
        )
        access_token = token_resp.json()["access_token"]

    def headers_for_case(case: object) -> dict[str, str]:
        return {"Authorization": f"Bearer {access_token}"}

    failures = run_contract_suite(
        contract_filename="pms-sim.openapi.yaml",
        app=app,
        headers_for_case=headers_for_case,
    )
    assert failures == [], failures
