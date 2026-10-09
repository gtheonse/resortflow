"""FR-011/SC-001: casino-sim contract-conformance suite, driven directly from the
frozen `docs/contracts/openapi/casino-sim.openapi.yaml` (T010). Casino is read-only —
this suite has no hold/confirm coverage by design (spec.md Clarifications)."""

from __future__ import annotations

from sims.casino.main import AUTH_TOKEN, app
from sims.tests.fixtures.contract_helpers import run_contract_suite


def test_casino_contract_conformance() -> None:
    def headers_for_case(case: object) -> dict[str, str]:
        return {"Authorization": f"Bearer {AUTH_TOKEN}"}

    failures = run_contract_suite(
        contract_filename="casino-sim.openapi.yaml",
        app=app,
        headers_for_case=headers_for_case,
    )
    assert failures == [], failures
