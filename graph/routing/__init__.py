"""Feature 010 — Tiered Intent Routing.

Three-tier request router: tier 0 tolerant whole-word/phrase vocabulary matching
(`matcher.py`), tier 1 deterministic request-kind rules and one-turn follow-up
carry-over (`kinds.py`), tier 2 a constrained economy-tier model classifier for
whatever the deterministic tiers cannot decide (`classifier.py`). `router.py`
orchestrates all three into a single `RoutingDecision` per turn.

See ADR-055 and `specs/010-tiered-intent-routing/contracts/router-api.md` for
the design and the tier-2 failure-matrix contract this package must satisfy.
"""

from __future__ import annotations
