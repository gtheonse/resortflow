"""Golden snapshot of the compiled supervisor graph's node and edge set.

Node names are a persistence contract (AGENTS.md hard rule): a checkpoint written
by one release must resume on the next. This test pins the topology so a
structural refactor of `graph/supervisor` cannot silently rename a node or
rewire an edge. Regenerate deliberately with `RESORTFLOW_UPDATE_GOLDEN=1` and review
the diff as part of an ADR-governed change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from graph.supervisor import compile_supervisor_graph

GOLDEN = Path(__file__).parent / "golden" / "supervisor_topology.json"


def _snapshot() -> dict[str, list]:
    drawable = compile_supervisor_graph().get_graph()
    return {
        "nodes": sorted(drawable.nodes),
        "edges": sorted([e.source, e.target, bool(e.conditional)] for e in drawable.edges),
    }


def test_supervisor_topology_matches_golden() -> None:
    actual = _snapshot()
    if os.environ.get("RESORTFLOW_UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected
