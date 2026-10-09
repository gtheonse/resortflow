"""T035, T039 — PII redaction tests (ADR-031, FR-010, FR-011, FR-022, SC-004).

`observability/redaction.py`'s own tests live at the top level, matching
`observability/attributes.py`'s existing convention (covered by
`tests/test_contract_registries.py`), not a package-local `tests/` dir.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from observability.redaction import (
    redact_for_trace,
    redact_for_trace_or_suppress,
    redact_text,
)


def test_a_name_and_phone_number_are_redacted() -> None:
    text = "Guest John Smith can be reached at 555-123-4567."
    redacted = redact_text(text)
    assert "John Smith" not in redacted
    assert "555-123-4567" not in redacted


def test_booking_time_is_not_redacted_as_pii() -> None:
    """FR-010 names identity/payment/location categories, not DATE_TIME —
    over-redacting a booking time would strip exactly the operational
    context User Story 4 Scenario 2 requires support staff still be able to
    diagnose an issue from."""
    text = "Table for four at 7pm, no allergies mentioned."
    redacted = redact_text(text)
    assert redacted == text


def test_wholesale_redact_field_becomes_a_fixed_placeholder_not_ner_scrubbed() -> None:
    """`detail` (DietaryRequirement's free-text disclosure field) is
    redacted wholesale — the spec's clarification asks for the disclosure
    itself gone, not just names/phones scrubbed out of it."""
    payload = {"detail": "I get hives from shellfish, my son Alex has the same issue", "restriction": "shellfish"}
    redacted = redact_for_trace(payload)
    assert redacted["detail"] == "[redacted — free-text disclosure]"
    assert redacted["restriction"] == "shellfish"


def test_empty_string_redacts_to_empty_string() -> None:
    assert redact_text("") == ""


# --- Field-name allowlist (T035, contracts/redaction-contract.md) -----------


def test_dietary_restriction_and_severity_survive_redaction() -> None:
    payload = {
        "detail": "Guest Jane Doe has a severe shellfish allergy, call 555-987-6543",
        "restriction": "shellfish",
        "severity": "allergy_severe",
    }
    redacted = redact_for_trace(payload)
    assert redacted["restriction"] == "shellfish"
    assert redacted["severity"] == "allergy_severe"
    assert "Jane Doe" not in redacted["detail"]
    assert "555-987-6543" not in redacted["detail"]


def test_policy_flag_rule_and_disposition_survive_redaction() -> None:
    payload = {"rule": "allergen_fidelity_mismatch", "disposition": "blocked", "note": "Guest Jane Doe called"}
    redacted = redact_for_trace(payload)
    assert redacted["rule"] == "allergen_fidelity_mismatch"
    assert redacted["disposition"] == "blocked"
    assert "Jane Doe" not in redacted["note"]


def test_allowlist_applies_at_any_nesting_depth() -> None:
    payload = {"outer": {"restriction": "peanut", "detail": "guest Bob Lee, 555-111-2222"}}
    redacted = redact_for_trace(payload)
    assert redacted["outer"]["restriction"] == "peanut"
    assert "Bob Lee" not in redacted["outer"]["detail"]


def test_lists_of_strings_are_redacted_element_by_element() -> None:
    payload = {"notes": ["contact Jane Doe at 555-987-6543", "no issues"]}
    redacted = redact_for_trace(payload)
    assert "Jane Doe" not in redacted["notes"][0]
    assert redacted["notes"][1] == "no issues"


# --- Fail-closed suppression (T038, FR-022) ---------------------------------


def test_redact_for_trace_or_suppress_returns_none_on_failure(monkeypatch) -> None:
    """FR-022: if redaction itself raises, the reference helper returns
    `None` (suppress the record) rather than the raw, unredacted payload."""
    import observability.redaction as redaction_module

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated analyzer failure")

    monkeypatch.setattr(redaction_module, "_get_analyzer", _boom)

    # "note" (not "detail") so this actually reaches the NER path rather
    # than short-circuiting into wholesale redaction before the analyzer is
    # ever called.
    result = redact_for_trace_or_suppress({"note": "some free text"})

    assert result is None


def test_redact_for_trace_or_suppress_returns_redacted_payload_on_success() -> None:
    result = redact_for_trace_or_suppress({"note": "contact Jane Doe", "restriction": "peanut"})
    assert result is not None
    assert result["restriction"] == "peanut"


# --- Property tests (T039) --------------------------------------------------


@settings(deadline=None)
@given(st.text(min_size=0, max_size=100))
def test_redact_text_never_raises_on_arbitrary_input(text: str) -> None:
    result = redact_text(text)
    assert isinstance(result, str)


@given(
    restriction=st.sampled_from(
        ["shellfish", "peanut", "tree_nut", "dairy", "gluten", "vegan", "halal"]
    ),
    severity=st.sampled_from(["preference", "intolerance", "allergy_moderate", "allergy_severe"]),
)
def test_structured_fields_always_survive_regardless_of_surrounding_free_text(
    restriction: str, severity: str
) -> None:
    """For any structured restriction/severity pair, paired with any
    free-text PII, the structured pair always survives redaction intact."""
    payload = {
        "restriction": restriction,
        "severity": severity,
        "detail": "Guest Alex Rivera, phone 555-000-1111, mentioned this in conversation",
    }
    redacted = redact_for_trace(payload)
    assert redacted["restriction"] == restriction
    assert redacted["severity"] == severity
