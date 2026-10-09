"""FR-011/SC-001: amenities-sim contract-conformance suite, driven directly from the
frozen `docs/contracts/openapi/amenities-sim.openapi.yaml` (T009)."""

from __future__ import annotations

import base64

from sims.amenities.main import AUTH_PASSWORD, AUTH_USERNAME, app
from sims.tests.fixtures.contract_helpers import run_contract_suite


def test_amenities_contract_conformance() -> None:
    token = base64.b64encode(f"{AUTH_USERNAME}:{AUTH_PASSWORD}".encode()).decode()

    def headers_for_case(case: object) -> dict[str, str]:
        return {"Authorization": f"Basic {token}"}

    failures = run_contract_suite(
        contract_filename="amenities-sim.openapi.yaml",
        app=app,
        headers_for_case=headers_for_case,
    )
    assert failures == [], failures
