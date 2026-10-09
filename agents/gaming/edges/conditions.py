"""T052 — the gaming subgraph's node names and its one (trivial) routing
function.

Node order: `intent_parser -> lookup -> END`. There is no third node and no
branch: gaming has no hold to place and nothing to submit for approval, so
there is nothing `route_after_lookup` could route *to* except `END` — unlike
`agents.dining.edges.conditions`'s `route_after_hold_placement`/
`route_after_human_approval`, which each choose between multiple next nodes
because a hold-then-confirm saga actually has branches. This function exists
purely so `agents/gaming/graph.py` can wire the edge the same way every
other domain wires its post-lookup edge (`add_conditional_edges` for
call-site parity), not because the routing decision itself is interesting.
"""

from __future__ import annotations

from langgraph.graph import END

from agents.gaming.state import GamingState

INTENT_PARSER = "intent_parser"
LOOKUP = "lookup"


def route_after_lookup(state: GamingState) -> str:
    """Always `END`. Read-only by construction (AGENTS.md hard rule): there
    is no hold to approve or confirm, so there is nothing else this could
    ever route to."""
    _ = state
    return END
