"""T021 -- behaviour-neutral migration: the templated dining prompt equals the old hard-coded one byte for byte.

`GOLDEN_SYSTEM` is the exact string `AzureIntentExtractor._SCHEMA_INSTRUCTIONS` evaluated to (with both enum
lists interpolated) immediately before it was removed. If this test fails, the prompt the model sees has
changed: that is either a deliberate wording change (update the golden -- it needs compliance approval, FR-042)
or a regression.

Named `test_dietary_*` so the FR-042 ownership glob covers it.
"""

from __future__ import annotations

from agents.dining.nodes.intent_parser import DINING_INTENT_PROMPT_ID, dining_intent_required_vars
from prompts.registry import RegistrySettings, resolve

GOLDEN_SYSTEM = (
    "Extract structured dining-reservation intent from the guest message. Respond with ONLY a JSON object with "
    "keys: cuisine (string|null), party_size (integer|null), date_str (YYYY-MM-DD string|null), time_str (HH:MM "
    "24h string|null), occasion (string|null), dietary (array of {restriction, severity, detail} using "
    "restriction in ['shellfish', 'tree_nut', 'peanut', 'dairy', 'gluten', 'egg', 'soy', 'sesame', 'fish', "
    "'vegetarian', 'vegan', 'halal', 'kosher', 'low_sodium'] and severity in ['preference', 'intolerance', "
    "'allergy_moderate', 'allergy_severe'])."
)

GUEST = "Table for four tomorrow at 7pm, my daughter has a severe peanut allergy"


def _git() -> RegistrySettings:
    return RegistrySettings(source="git", label="production", cache_ttl_seconds=60, fetch_timeout_seconds=2)


def test_system_message_is_byte_identical_to_the_old_constant() -> None:
    resolved = resolve(
        DINING_INTENT_PROMPT_ID,
        required_vars=dining_intent_required_vars(),
        runtime_vars={"guest_message": GUEST},
        settings=_git(),
    )
    assert resolved.source == "git"
    assert resolved.messages[0] == {"role": "system", "content": GOLDEN_SYSTEM}


def test_user_message_is_exactly_the_guest_text() -> None:
    resolved = resolve(
        DINING_INTENT_PROMPT_ID,
        required_vars=dining_intent_required_vars(),
        runtime_vars={"guest_message": GUEST},
        settings=_git(),
    )
    assert resolved.messages[1] == {"role": "user", "content": GUEST}
    assert len(resolved.messages) == 2


def test_config_matches_the_old_call_site() -> None:
    """The old call hard-coded `temperature=0` and `response_format={"type": "json_object"}`."""
    resolved = resolve(
        DINING_INTENT_PROMPT_ID,
        required_vars=dining_intent_required_vars(),
        runtime_vars={"guest_message": GUEST},
        settings=_git(),
    )
    assert resolved.config == {"temperature": 0, "response_format": {"type": "json_object"}}
