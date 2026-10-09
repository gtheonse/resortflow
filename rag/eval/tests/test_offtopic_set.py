"""Feature 010, T012 -- the `offtopic:` section of `rag/eval/negative_set.yaml`
(research R-11, ADR-057): >= 10 cases per tenant, both jurisdictions.

Unlike the `cases:` (availability) section above it, these calibrate
`knowledge_relevance_floor` -- they must NOT trip
`rag.retriever.is_availability_query`'s deterministic short-circuit, or they
would never reach the cross-encoder score the floor is calibrated against.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from rag.models import EvalCase
from rag.retriever import is_availability_query

NEGATIVE_SET_PATH = Path(__file__).resolve().parent.parent / "negative_set.yaml"


def _load_offtopic() -> list[EvalCase]:
    data = yaml.safe_load(NEGATIVE_SET_PATH.read_text(encoding="utf-8"))
    return [EvalCase(**case) for case in data["offtopic"]]


def test_at_least_ten_offtopic_cases_per_tenant() -> None:
    cases = _load_offtopic()
    by_tenant: dict[str, int] = {}
    for case in cases:
        by_tenant[case.tenant_id] = by_tenant.get(case.tenant_id, 0) + 1
    assert by_tenant.get("MGV", 0) >= 10
    assert by_tenant.get("MHB", 0) >= 10


def test_both_jurisdictions_represented() -> None:
    tenants = {case.tenant_id for case in _load_offtopic()}
    assert tenants == {"MGV", "MHB"}


def test_offtopic_cases_have_no_gold_chunks() -> None:
    for case in _load_offtopic():
        assert case.gold_chunk_ids == []


def test_verbatim_spec_examples_are_present() -> None:
    questions = {case.question for case in _load_offtopic()}
    assert "Book flight for me to fly to India" in questions
    assert any("weather in Delhi" in q for q in questions)


def test_offtopic_cases_do_not_trip_the_availability_short_circuit() -> None:
    """These must reach the cross-encoder, not the deterministic
    availability short-circuit -- otherwise they calibrate nothing new."""
    for case in _load_offtopic():
        assert not is_availability_query(case.question), (
            f"{case.id!r} trips is_availability_query and belongs in `cases:`, not `offtopic:`"
        )
