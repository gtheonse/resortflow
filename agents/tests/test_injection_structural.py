"""T032 — structural guarantee (ADR-032, FR-008): retrieved-content strings
never feed tool-selection/intent-classification logic, in any domain.

Mirrors `agents/gaming/tests/test_graph.py`'s "no mutation verb" structural-
test style: this inspects source code directly rather than fuzzing runtime
behavior, since the property being verified is architectural ("this kind of
data never flows into this kind of decision"), not a behavioral edge case a
dynamic test could exercise more precisely. `policy.injection.
screen_retrieved_content`'s screening accuracy is necessarily probabilistic
against novel phrasings (tested separately, `policy/tests/test_injection.py`
and the corpus sweep, T033) — this test is the architectural backstop that
holds regardless of that screen's recall.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from agents.dining.nodes import intent_parser as dining_intent_parser
from agents.entertainment.nodes import intent_parser as entertainment_intent_parser
from agents.gaming.nodes import intent_parser as gaming_intent_parser
from agents.stay.nodes import intent_parser as stay_intent_parser
from agents.wellness.nodes import intent_parser as wellness_intent_parser
from graph import supervisor
from graph.routing import classifier as routing_classifier
from graph.routing import kinds as routing_kinds
from graph.routing import matcher as routing_matcher
from graph.routing import router as routing_router
from graph.routing import vocabulary as routing_vocabulary

_INTENT_PARSER_MODULES = [
    dining_intent_parser, entertainment_intent_parser, gaming_intent_parser,
    stay_intent_parser, wellness_intent_parser,
]

_ROUTING_MODULES = [
    routing_matcher, routing_kinds, routing_classifier, routing_router, routing_vocabulary,
]
"""Feature 010 (analyze finding — extending this test's coverage to the new
router package, per T037's plan): the same architectural guarantee applies
to every tier of the router, not only the per-domain intent parsers."""

_RETRIEVAL_MARKERS = ("Chunk", "retrieved", "chunk.content", "chunk_content")
"""Any of these appearing in an intent-classification module's source would
signal retrieved content feeding a decision it must never feed."""


@pytest.mark.parametrize("module", _INTENT_PARSER_MODULES, ids=lambda m: m.__name__)
def test_no_domain_intent_parser_references_retrieved_content(module) -> None:
    source = inspect.getsource(module)
    for marker in _RETRIEVAL_MARKERS:
        assert marker not in source, (
            f"{module.__name__} references {marker!r} — intent classification "
            f"must never read retrieved/RAG content (FR-008)"
        )


def test_domain_routing_and_planner_do_not_reference_retrieved_content() -> None:
    """`_detect_domains`/`make_planner_node` (`graph/supervisor.py`) decide
    which domain(s) handle a request and what the plan looks like —
    tool-selection-adjacent decisions that must be equally isolated from
    retrieved content.

    This used to also grep the whole module for any retrieval marker before
    inspecting these two functions specifically. Feature 006 convergence
    added two legitimate, non-routing consumers of retrieved/chunk content
    to this same module — T053's menu-content conflict check in
    `_run_dining_propose_and_hold` (mirrors the already-accepted pattern in
    `agents/dining/nodes/allergen_check.py`, just for the fanout path) and
    T054's `KNOWLEDGE_QA` node (ADR-038), which retrieves and answers from
    chunks by design. Neither is routing or planning, so a whole-module
    scan is no longer the right check — the property this test actually
    guarantees is scoped to `_detect_domains`/`make_planner_node`
    specifically, verified below."""
    detect_domains_source = inspect.getsource(supervisor._detect_domains)
    planner_source = inspect.getsource(supervisor.make_planner_node)
    for fn_source, fn_name in [
        (detect_domains_source, "_detect_domains"),
        (planner_source, "make_planner_node"),
    ]:
        assert "retrieved" not in fn_source, f"{fn_name} references state['retrieved']"
        assert "Chunk" not in fn_source, f"{fn_name} references Chunk"


def test_injection_corpus_file_is_never_imported_by_any_intent_parser() -> None:
    """A second-order structural check: the red-team corpus itself
    (content used to *test* the screen) must never leak into a live
    classification path either."""
    redteam_dir = Path(__file__).resolve().parents[2] / "content" / "redteam"
    assert redteam_dir.is_dir()
    for module in _INTENT_PARSER_MODULES:
        source = inspect.getsource(module)
        assert "redteam" not in source


@pytest.mark.parametrize("module", _ROUTING_MODULES, ids=lambda m: m.__name__)
def test_no_routing_module_references_retrieved_content(module) -> None:
    """Feature 010: no tier of the router (0/1/2) reads retrieved/RAG
    content — `KNOWLEDGE_QA` is reached only AFTER routing has already
    decided this isn't a booking request, never the other way round."""
    source = inspect.getsource(module)
    for marker in _RETRIEVAL_MARKERS:
        assert marker not in source, (
            f"{module.__name__} references {marker!r} — routing must never read retrieved/RAG content"
        )


def test_no_routing_module_imports_the_redteam_corpus() -> None:
    for module in _ROUTING_MODULES:
        source = inspect.getsource(module)
        assert "redteam" not in source
