"""T012 -- canonical serialisation and sha256 (research R-05).

The hash is the identity the runtime allow-list trusts; a wrong hash fails silently, so the
properties below are the contract.
"""

from __future__ import annotations

from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from prompts.canonical import canonical_json, sha256_of

MSGS: list[dict[str, Any]] = [
    {"role": "system", "content": "You are strict.\nBe brief."},
    {"role": "user", "content": "{{q}}"},
]
CFG: dict[str, Any] = {"temperature": 0, "response_format": {"type": "json_object"}}

_text = st.text(min_size=1, max_size=40)
_role = st.sampled_from(["system", "user"])


def test_hash_is_lowercase_hex_sha256() -> None:
    digest = sha256_of(MSGS, CFG)
    assert len(digest) == 64 and digest == digest.lower()
    int(digest, 16)


def test_key_order_does_not_change_hash() -> None:
    reordered_msgs = [{"content": m["content"], "role": m["role"]} for m in MSGS]
    reordered_cfg = {"response_format": {"type": "json_object"}, "temperature": 0}
    assert sha256_of(reordered_msgs, reordered_cfg) == sha256_of(MSGS, CFG)


def test_line_endings_are_normalised() -> None:
    crlf = [{"role": m["role"], "content": m["content"].replace("\n", "\r\n")} for m in MSGS]
    assert sha256_of(crlf, CFG) == sha256_of(MSGS, CFG)


def test_hash_covers_config() -> None:
    assert sha256_of(MSGS, {**CFG, "temperature": 0.5}) != sha256_of(MSGS, CFG)
    assert sha256_of(MSGS, {}) != sha256_of(MSGS, CFG)


def test_extra_registry_fields_on_messages_are_ignored() -> None:
    """A registry chat message carries extra keys (e.g. `type`); identity is role + content only."""
    with_extra = [{**m, "type": "chatmessage"} for m in MSGS]
    assert sha256_of(with_extra, CFG) == sha256_of(MSGS, CFG)


def test_one_character_change_flips_hash() -> None:
    changed = [dict(MSGS[0], content=MSGS[0]["content"] + "."), MSGS[1]]
    assert sha256_of(changed, CFG) != sha256_of(MSGS, CFG)


def test_role_change_flips_hash() -> None:
    swapped = [dict(MSGS[0], role="user"), MSGS[1]]
    assert sha256_of(swapped, CFG) != sha256_of(MSGS, CFG)


def test_message_order_matters() -> None:
    assert sha256_of(list(reversed(MSGS)), CFG) != sha256_of(MSGS, CFG)


def test_non_ascii_is_stable_and_not_escaped() -> None:
    msgs = [{"role": "user", "content": "café — 東京 🍣"}]
    assert "café" in canonical_json(msgs, CFG)  # ensure_ascii=False
    assert sha256_of(msgs, CFG) == sha256_of([dict(msgs[0])], dict(CFG))


def test_canonical_form_is_compact_and_sorted() -> None:
    text = canonical_json([{"role": "user", "content": "x"}], {"b": 1, "a": 2})
    assert text == '{"config":{"a":2,"b":1},"messages":[{"content":"x","role":"user"}],"type":"chat"}'


def test_template_helper_matches_free_function() -> None:
    from prompts.canonical import template_sha256
    from prompts.schema import PromptTemplate

    template = PromptTemplate.model_validate(
        {
            "id": "t",
            "family": "dining",
            "type": "chat",
            "resolution": "label",
            "owner": "compliance",
            "config": CFG,
            "variables": {"runtime": ["q"], "tenant": []},
            "messages": MSGS,
        }
    )
    assert template_sha256(template) == sha256_of(MSGS, CFG)


@given(st.lists(st.tuples(_role, _text), min_size=1, max_size=4))
def test_property_hash_is_deterministic_and_lf_crlf_stable(pairs: list[tuple[str, str]]) -> None:
    msgs = [{"role": r, "content": c} for r, c in pairs]
    crlf = [{"role": r, "content": c.replace("\r\n", "\n").replace("\n", "\r\n")} for r, c in pairs]
    lf = [{"role": r, "content": c.replace("\r\n", "\n")} for r, c in pairs]
    assert sha256_of(msgs, CFG) == sha256_of(msgs, CFG)
    assert sha256_of(crlf, CFG) == sha256_of(lf, CFG)


@given(_text, _text)
def test_property_distinct_content_gives_distinct_hash(a: str, b: str) -> None:
    ha = sha256_of([{"role": "user", "content": a}], CFG)
    hb = sha256_of([{"role": "user", "content": b}], CFG)
    if a.replace("\r\n", "\n") != b.replace("\r\n", "\n"):
        assert ha != hb
    else:
        assert ha == hb
