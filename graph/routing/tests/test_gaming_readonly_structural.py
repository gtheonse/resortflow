"""Feature 010, T035 — FR-028: no routing module ever imports or
references a gaming tool or mutation call. Structural, source-inspection
style, mirroring `agents/tests/test_injection_structural.py`."""

from __future__ import annotations

import inspect

from graph.routing import classifier, kinds, matcher, router, vocabulary

_MODULES = (matcher, kinds, classifier, router, vocabulary)
_FORBIDDEN_MARKERS = ("GamingTools", "place_wager", "gaming.tools", "casino_transaction")


def test_no_routing_module_references_a_gaming_tool_or_mutation() -> None:
    for module in _MODULES:
        source = inspect.getsource(module)
        for marker in _FORBIDDEN_MARKERS:
            assert marker not in source, f"{module.__name__} references {marker!r} (FR-028)"


def test_gaming_never_coexists_with_modify_at_the_model_level() -> None:
    """The invariant is enforced in `graph.models.RoutingDecision` itself
    (data-model.md) — re-asserted here at the routing-package boundary."""
    import pytest
    from pydantic import ValidationError

    from graph.models import Domain, RequestKind, RoutingDecision

    with pytest.raises(ValidationError):
        RoutingDecision(kind=RequestKind.MODIFY, services=[Domain.GAMING])
