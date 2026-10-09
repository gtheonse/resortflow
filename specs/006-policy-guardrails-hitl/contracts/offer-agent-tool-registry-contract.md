# Contract: Offer Agent's structural tool restriction

Mirrors `mcp/gateway/gaming/server.py`'s own read-only guarantee, which is enforced by
absence (no `hold_*`/`confirm_*` method exists on `GamingTools`) plus a structural test
(`agents/gaming/tests/test_graph.py`, "no method name containing a mutation verb" — T055
in the gateway's own module). This contract states the equivalent guarantee for
`OFFER_AGENT`.

## The guarantee

`OFFER_AGENT`'s tool surface is exactly one call: `GamingTools.get_loyalty_status`
(existing, `mcp/gateway/gaming/server.py`'s `get_loyalty_status`, already a composite of
`getLoyaltyProfile` + optional `getLoyaltyOffers`, already respecting 403 →
`POLICY_BLOCKED` at the gateway per that tool's own docstring). `OFFER_AGENT` has:

- **No** access to any wagering, deposit, or gaming-transaction tool (none exists on
  `GamingTools` at all — the Protocol has exactly three read-only methods, per
  `agents/gaming/tools.py`'s own docstring: "Do not add a fourth method").
- **No** dedicated "offers" MCP domain or tool distinct from the loyalty composite above
  — building one is explicitly out of scope this phase (research.md).

This is enforced by construction (the node's implementation only ever calls
`get_loyalty_status`, nothing else is in scope for it to call), verified by a new
structural test analogous to the gateway's T055: `test_offer_agent_calls_no_gaming_tool`
asserts the compiled `OFFER_AGENT` node's closure/dependencies reference only
`get_loyalty_status`, not `check_rg_status` result content being repackaged as an offer,
nor any hypothetical future gaming-mutation method.

## Precondition (every turn, regardless of topic)

```
OFFER_AGENT node body:
  clearance = await require_gaming_clear(tools, guest_id=guest_id)
  if not clearance.permitted:
      return no-offer response  # get_loyalty_status is never called
  profile = await tools.get_loyalty_status(guest_id=guest_id)
  ... present non-gaming comp offer, mandated_message_for() applied if applicable
```

`require_gaming_clear` runs before `get_loyalty_status` on **every** invocation of this
node, not cached from an earlier turn (FR-016) — this is the same function
`agents/gaming/nodes/lookup.py` calls, not a duplicate check with its own logic that
could drift out of sync.

## What this does not restrict

Non-gaming offers (spa, dining, entertainment upsells) are unaffected by this contract —
only the gaming-linked path is gated. `OFFER_AGENT`'s current scope in this feature is
narrow (loyalty/comp offers only, per research.md); a future feature extending it to
other offer types inherits this same precondition requirement by construction, since
`require_gaming_clear` remains the first call in the node body.
