"""Self-exclusion enforcement (ADR-030, ADR-037).

`require_gaming_clear` is the single gate both the gaming lookup subgraph
(`agents/gaming/nodes/lookup.py`) and `OFFER_AGENT` (`graph/supervisor.py`)
call, every turn, before any gaming-domain vendor call or offer composition —
never cached across turns (FR-016). It wraps the existing, already-real
`GamingTools.check_rg_status` (ADR-037: no new MCP tool, no new gateway
module — `check_rg_status`'s own docstring already names it "policy-critical,
must pass before gaming content").
"""

from __future__ import annotations

from agents.gaming.tools import GamingToolError, GamingTools
from policy.types import GamingClearance


async def require_gaming_clear(tools: GamingTools, *, guest_id: str) -> GamingClearance:
    """Resolve to exactly one of three outcomes:

    - `permitted=True` — the vendor confirmed the guest is in good standing;
      the caller may proceed with its own gaming-domain tool call or offer.
    - `permitted=False, failure_reason="self_excluded"` — the guest is
      self-excluded or gaming content is not permitted for another reason the
      vendor reports; the caller must withhold gaming content/offers and
      surface `status.mandated_message` (FR-003).
    - `permitted=False, failure_reason="dependency_unavailable"` — the vendor
      call itself failed; the caller fails closed for its own capability
      only, not the whole conversation (FR-021). Never raises — both call
      sites need a value to branch on, not an exception to catch.
    """
    try:
        status = await tools.check_rg_status(guest_id=guest_id)
    except GamingToolError:
        return GamingClearance(permitted=False, status=None, failure_reason="dependency_unavailable")

    if status.self_excluded or not status.gaming_content_permitted:
        return GamingClearance(permitted=False, status=status, failure_reason="self_excluded")

    return GamingClearance(permitted=True, status=status, failure_reason=None)
