"""T005 — the five frozen vendor OpenAPI specifications lint clean.

Satisfies the "OpenAPI specs lint clean" phase exit criterion (plan.md §13 /
docs/ResortFlow-Enterprise-Multi-Agent-Plan.md §13). These five files are frozen
inputs to this feature (spec.md Assumptions), not deliverables of it — this
test only proves they are well-formed, never edits them.
"""

from pathlib import Path

import pytest
from openapi_spec_validator import validate
from openapi_spec_validator.readers import read_from_filename

OPENAPI_DIR = Path(__file__).resolve().parents[1] / "docs" / "contracts" / "openapi"

EXPECTED_SPECS = {
    "amenities-sim.openapi.yaml",
    "casino-sim.openapi.yaml",
    "dining-sim.openapi.yaml",
    "pms-sim.openapi.yaml",
    "ticketing-sim.openapi.yaml",
}


def test_all_five_specs_present() -> None:
    found = {p.name for p in OPENAPI_DIR.glob("*.openapi.yaml")}
    assert found == EXPECTED_SPECS


@pytest.mark.parametrize("spec_name", sorted(EXPECTED_SPECS))
def test_spec_lints_clean(spec_name: str) -> None:
    spec_dict, _spec_url = read_from_filename(OPENAPI_DIR / spec_name)
    validate(spec_dict)
