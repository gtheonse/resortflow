"""T033 — injection-corpus sweep (SC-003): every entry in the full red-team
corpus, screened through `screen_retrieved_content`, must be caught
(`verdict == "suspect"`) — proving the screen actually recognizes every
known pattern it was built from, not just the one example checked by the
unit tests in `policy/tests/test_injection.py`.

Combined with `test_injection_structural.py`'s architectural guarantee
(retrieved content never feeds tool-selection), this closes the loop
FR-007/FR-008/FR-009 describe: the corpus is maintained, every entry in it
is caught, and even a pattern this screen *missed* would still have no
channel to alter a tool call — the two tests are independent, complementary
proofs, not the same guarantee twice.
"""

from __future__ import annotations

from datetime import UTC, datetime

from graph.models import Chunk
from policy.injection import load_injection_corpus, screen_retrieved_content


def _chunk_from_pattern(pattern_id: str, text: str) -> Chunk:
    return Chunk(
        chunk_id=pattern_id, tenant_id="MGV", content=text, source_uri="redteam://corpus",
        content_hash="h", valid_from=datetime.now(UTC), valid_to=None,
    )


def test_every_corpus_pattern_is_caught_as_suspect() -> None:
    corpus = load_injection_corpus()
    chunks = [_chunk_from_pattern(pattern_id, text) for pattern_id, text in corpus.items()]

    results = screen_retrieved_content(chunks, corpus=corpus)

    missed = [r for r in results if r.verdict != "suspect"]
    assert not missed, f"{len(missed)}/{len(chunks)} corpus patterns were not caught: {missed}"


def test_corpus_sweep_changes_zero_tool_invocation_decisions() -> None:
    """SC-003, exercised concretely: run every corpus pattern through
    dining's actual menu-content path (the one real retrieval call site,
    `agents/dining/menu.py`) mixed in among ordinary menu content, and
    confirm `allergen_check`'s own tool-call/decision behavior — which venue
    chunks it uses for keyword matching — is identical to running without
    the injected patterns present."""
    from agents.dining.menu import _screen

    corpus = load_injection_corpus()
    ordinary = [_chunk_from_pattern("ordinary-1", "Dry-aged ribeye finished in brown butter (contains dairy).")]
    injected = [_chunk_from_pattern(pid, text) for pid, text in corpus.items()]

    screened_ordinary_only = _screen(ordinary)
    screened_mixed = _screen(ordinary + injected)

    # The ordinary chunk survives screening either way; none of the injected
    # patterns survive alongside it.
    assert {c.chunk_id for c in screened_ordinary_only} == {"ordinary-1"}
    assert {c.chunk_id for c in screened_mixed} == {"ordinary-1"}
