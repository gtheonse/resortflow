"""The Azure intent extractor resolves relative dates in code, never by asking the model to do date arithmetic.

Found by the eval gate (`intent_extraction_accuracy` 0.72 against a 0.8 floor, first seen in feature 009 and never
calibrated): `extract(text, today=...)` took `today` but the prompt never told the model what today is, so "tomorrow" and
"tonight" came back as `date_str: null` on every case. Downstream, `availability` silently defaults a missing date to
TOMORROW, so a guest asking for "tonight at 8pm" through the UI was booked for the next day. The deterministic parser the
rule-based extractor already uses (`graph.routing.temporal`) is the arithmetic; the model only supplies what it cannot."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from agents.dining.nodes.intent_parser import AzureIntentExtractor

TODAY = date(2026, 1, 15)


def _extractor(payload: dict) -> AzureIntentExtractor:
    extractor = AzureIntentExtractor(endpoint="https://x.example", api_key="k", api_version="2024-10-21", deployment="d")
    reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])
    extractor._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kw: reply)))
    return extractor


@pytest.mark.parametrize(
    "text,expected",
    [("Table for 4 tomorrow at 7pm", "2026-01-16"), ("Party of 2 tonight at 8pm", "2026-01-15")],
)
def test_relative_dates_resolve_against_today_even_when_the_model_returns_none(text: str, expected: str) -> None:
    intent = _extractor({"party_size": 4, "date_str": None, "time_str": "19:00"}).extract(text, today=TODAY)
    assert intent["date_str"] == expected
    assert "date_str" not in intent["ambiguous_fields"]


def test_the_code_resolved_date_wins_over_a_wrong_model_date() -> None:
    intent = _extractor({"date_str": "2024-03-02"}).extract("Table for 2 tomorrow", today=TODAY)
    assert intent["date_str"] == "2026-01-16"


def test_the_model_date_is_kept_when_the_code_finds_none() -> None:
    intent = _extractor({"date_str": "2026-02-14"}).extract("Valentine's dinner for two", today=TODAY)
    assert intent["date_str"] == "2026-02-14"


def test_no_date_anywhere_stays_ambiguous() -> None:
    intent = _extractor({"date_str": None}).extract("Table for 2 at Sakura", today=TODAY)
    assert intent["date_str"] is None and "date_str" in intent["ambiguous_fields"]


@pytest.mark.parametrize("model_value,expected", [("Mexican", "mexican"), ("asian fusion", "asian_fusion"), ("Asian Fusion", "asian_fusion"), (None, None)])
def test_cuisine_is_normalised_to_the_vendor_vocabulary(model_value: str | None, expected: str | None) -> None:
    intent = _extractor({"cuisine": model_value}).extract("Table for 2", today=TODAY)
    assert intent["cuisine"] == expected


def test_a_time_the_model_missed_is_filled_in_by_code() -> None:
    intent = _extractor({"time_str": None}).extract("Table for 2 tomorrow at 7:30pm", today=TODAY)
    assert intent["time_str"] == "19:30"
