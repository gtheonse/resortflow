"""FR-011/SC-001: dining-sim contract-conformance suite, driven directly from the
frozen `docs/contracts/openapi/dining-sim.openapi.yaml` (T011)."""

from __future__ import annotations

from sims.dining.main import API_KEY, app
from sims.tests.fixtures.contract_helpers import run_contract_suite


def test_dining_contract_conformance() -> None:
    def headers_for_case(case: object) -> dict[str, str]:
        return {"X-Dining-Key": API_KEY}

    failures = run_contract_suite(
        contract_filename="dining-sim.openapi.yaml",
        app=app,
        headers_for_case=headers_for_case,
    )
    assert failures == [], failures
