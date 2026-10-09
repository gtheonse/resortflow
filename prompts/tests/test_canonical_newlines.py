"""Regression tests for the canonical hash's newline handling (FR-005; found by Hypothesis during T040).

`test_canonical.py::test_property_hash_is_deterministic_and_lf_crlf_stable` failed on a counterexample: the
content CR CR LF. With a single-pass CRLF -> LF replacement that content normalises to CR LF, while the same
content pushed through an LF checkout and a CRLF checkout normalises to LF -- two different hashes for "the
same" prompt. Universal-newline normalisation (CRLF and a lone CR both become LF) is idempotent, so it cannot.
"""

from __future__ import annotations

from hypothesis import example, given
from hypothesis import strategies as st

from prompts.canonical import canonical_json, sha256_of

CFG: dict[str, object] = {"temperature": 0}


def _msg(text: str) -> list[dict[str, str]]:
    return [{"role": "user", "content": text}]


def _lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def test_the_hypothesis_counterexample_cr_cr_lf() -> None:
    original = "\r\r\n"
    lf_checkout = "\n\n"  # what an LF checkout of the same content looks like
    crlf_checkout = lf_checkout.replace("\n", "\r\n")  # what a CRLF checkout looks like
    assert sha256_of(_msg(crlf_checkout), CFG) == sha256_of(_msg(lf_checkout), CFG)
    assert sha256_of(_msg(original), CFG) == sha256_of(_msg(lf_checkout), CFG)


def test_lone_cr_is_a_line_ending() -> None:
    assert sha256_of(_msg("a\rb"), CFG) == sha256_of(_msg("a\nb"), CFG) == sha256_of(_msg("a\r\nb"), CFG)


def test_mixed_endings_all_normalise_to_lf() -> None:
    assert '"a\\nb\\nc\\nd"' in canonical_json(_msg("a\r\nb\rc\nd"), CFG)


def test_no_carriage_return_survives_normalisation() -> None:
    assert "\\r" not in canonical_json(_msg("x\r\ry\r\n\r\n\rz\r"), CFG)


@given(st.text(alphabet=st.sampled_from(["a", "b", " ", "\r", "\n"]), max_size=12))
@example("\r\r\n")
@example("\r\n\r")
@example("\n\r")
def test_property_line_ending_style_never_changes_the_hash(text: str) -> None:
    """Whatever mixture of CR/LF/CRLF a text contains, converting it to LF or to CRLF gives the same hash."""
    as_lf = _lf(text)
    as_crlf = as_lf.replace("\n", "\r\n")
    assert sha256_of(_msg(text), CFG) == sha256_of(_msg(as_lf), CFG) == sha256_of(_msg(as_crlf), CFG)
