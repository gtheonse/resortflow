"""Policy pre/post-flight gates, the blocked reply, the Offer Agent and the human hand-off."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import AIMessage

from graph import nodes as N
from graph.models import (
    Domain,
    PolicyFlag,
)
from graph.state import ResortFlowState
from graph.supervisor.common import DEFAULT_TOOLS_FACTORY, _latest_human_text
from policy.distress import check_distress_signal
from policy.gaming import require_gaming_clear
from policy.jurisdiction import mandated_message_for


def make_policy_preflight_node() -> Any:
    """Real since feature 006 (ADR-036, FR-004, FR-018): screens the latest
    guest utterance for gambling-related distress. No model judgment
    participates — `check_distress_signal` is a deterministic corpus/
    embedding screen. Both the confident and ambiguous outcomes escalate;
    there is no third, "unsure, proceed normally" outcome."""

    async def policy_preflight(state: ResortFlowState) -> dict[str, Any]:
        text = _latest_human_text(state)
        check = check_distress_signal(text)
        if not (check.flagged or check.ambiguous):
            return {}

        return {
            "escalate": True,
            "escalate_reason": "distress_signal",
            "policy_flags": [
                PolicyFlag(
                    flag_id=uuid.uuid4().hex,
                    rule="distress_signal",
                    disposition="escalated",
                    jurisdiction=state.get("jurisdiction"),
                    raised_at=datetime.now(UTC),
                )
            ],
        }

    return policy_preflight


def _route_from_policy_preflight(state: ResortFlowState) -> str:
    if state.get("escalate") and state.get("escalate_reason") == "distress_signal":
        return N.HUMAN_HANDOFF
    # Clear to proceed: continue to IntentRouter's normal CLARIFY /
    # DETERMINISTIC / SUPERVISOR fork exactly as if this node were absent.
    return N.INTENT_ROUTER


_BLOCK_REASON_MESSAGES: dict[str, str] = {
    "allergen_fidelity_mismatch": (
        "I can't confirm this booking yet — a dietary restriction you mentioned "
        "isn't reflected in the request behind it. This can only be resolved by "
        "correcting the structured record, not by approving past it; please "
        "update the restriction and I'll try again."
    ),
    "dependency_unavailable": (
        "That part of your request is temporarily unavailable while a required "
        "check is unreachable. I've flagged this for follow-up — the rest of "
        "your request is unaffected."
    ),
    "injection_detected": (
        "I wasn't able to use part of the content I retrieved to answer that — "
        "it didn't pass a content-safety check. I can still help with what "
        "remains, or you can rephrase your question."
    ),
}
"""Feature 006 (ADR-029, ADR-030): one guest-facing message per closed-vocabulary
`rule` (data-model.md). `self_exclusion_active` and `distress_signal` are
deliberately absent — the gaming subgraph's own `lookup` node already returns
`ResponsibleGamingStatus.mandated_message` inline (agents/gaming/nodes/lookup.py),
and a distress signal routes to `HUMAN_HANDOFF`, never `BLOCKED` — so neither
reaches this node in practice. `BLOCKED` therefore ended up
dining-allergen-specific rather than the more general router the
original graph-topology-v006.md contract sketched."""


def make_blocked_node() -> Any:
    """Block-reason-aware since feature 006 (ADR-029): reads the most recently
    raised `PolicyFlag` with `disposition == "blocked"` from `state["policy_flags"]`
    and surfaces a message specific to that rule, replacing the single fixed
    string this node returned through feature 005."""

    async def blocked(state: ResortFlowState) -> dict[str, Any]:
        flags = state.get("policy_flags", [])
        blocking_flags = [f for f in flags if f.disposition == "blocked"]
        message = "This request cannot be completed as policy stands."
        if blocking_flags:
            latest = blocking_flags[-1]
            message = _BLOCK_REASON_MESSAGES.get(latest.rule, message)
        return {"messages": [AIMessage(content=message)]}

    return blocked


async def _apply_policy_postflight(state: dict[str, Any]) -> dict[str, Any]:
    """Core logic behind `POLICY_POSTFLIGHT` (ADR-034, FR-012, SC-006),
    extracted to a plain function so `make_deterministic_node` can call it
    directly (feature 006 convergence, T052) — the `DETERMINISTIC` path
    never routes through the `POLICY_POSTFLIGHT` *node*, since that would
    require also routing through `RESPOND` and risk it re-summarizing a
    booking `DETERMINISTIC`'s own inner subgraph already confirmed. Calling
    this same function directly from both places means there is exactly one
    implementation of "does this response need the jurisdiction message,"
    not two that could drift apart.

    Applies `mandated_message_for` to any response about to reach the guest
    whose confirmations or held proposals touch the gaming domain — the
    `"gaming_content"` trigger. The `"offer"` trigger is applied by
    `OFFER_AGENT` itself at the point an offer is composed (ADR-030), not
    here, since this function runs once per turn regardless of which
    domain(s) were touched and has no view of an offer that hasn't been
    assembled yet.

    Deliberately does NOT re-message gaming's own self-exclusion suppression
    path — `ResponsibleGamingStatus.mandated_message` (returned inline by
    `agents/gaming/nodes/lookup.py`) already IS the jurisdiction-mandated
    text for that case; appending a second one would double the message.
    This only fires when the guest actually received gaming content or a
    gaming confirmation, not when it was withheld."""
    touched_gaming = any(
        c.domain == Domain.GAMING for c in state.get("confirmations", [])
    ) or any(p.domain == Domain.GAMING for p in state.get("proposals", []))
    if not touched_gaming:
        return {}

    message = mandated_message_for(state["jurisdiction"], "gaming_content")
    if message is None:
        return {}

    return {
        "messages": [AIMessage(content=message)],
        "policy_flags": [
            PolicyFlag(
                flag_id=uuid.uuid4().hex,
                rule="jurisdiction_message_gaming_content",
                disposition="messaged",
                jurisdiction=state["jurisdiction"],
                raised_at=datetime.now(UTC),
            )
        ],
    }


def make_policy_postflight_node() -> Any:
    """Thin node wrapper around `_apply_policy_postflight` — see that
    function's docstring for the real logic and why it's a standalone
    function, not inlined here."""

    async def policy_postflight(state: ResortFlowState) -> dict[str, Any]:
        return await _apply_policy_postflight(state)

    return policy_postflight


async def _apply_offer_agent(
    state: dict[str, Any], tools_factory: dict[Domain, Callable[..., Any]]
) -> dict[str, Any]:
    """Core logic behind `OFFER_AGENT` (ADR-020, ADR-030), extracted to a
    plain function so `make_deterministic_node` can call it directly
    (feature 006 convergence, T052) — the same reasoning as
    `_apply_policy_postflight`: `DETERMINISTIC` never routes through the
    `OFFER_AGENT` *node* (that would require also routing through `RESPOND`),
    but the self-exclusion gate before "the Offer Agent runs at all, on
    every turn" (Constitution Principle IV) must still apply on that path —
    calling this same function from both places is what makes that true
    without a second, divergent implementation.

    `require_gaming_clear` (ADR-030/037) runs first, every invocation, never
    cached across turns (FR-016) — before `GamingTools.get_loyalty_status`
    is ever called.

    Structurally cannot present a gaming-linked offer: `get_loyalty_status`
    is a composite of profile + non-gaming comp entitlements only (that
    tool's own gateway docstring), and this function calls nothing else
    (`contracts/offer-agent-tool-registry-contract.md`, ADR-037) — the
    guarantee is the absence of a second tool call, not a check on the
    result. Silent (`{}`) whenever there's nothing offer-worthy to surface —
    this runs every turn regardless of topic to satisfy the self-exclusion
    timing requirement, but does not spam every response with a loyalty
    summary."""
    from agents.gaming.tools import GamingToolError

    factory = tools_factory.get(Domain.GAMING)
    if factory is None:
        return {}
    tools = factory(state["tenant_id"], state["guest_id"])

    clearance = await require_gaming_clear(tools, guest_id=state["guest_id"])
    if not clearance.permitted:
        return {}

    try:
        profile = await tools.get_loyalty_status(guest_id=state["guest_id"])
    except GamingToolError:
        return {}
    if not profile.entitlements:
        return {}

    jurisdiction = (clearance.status.jurisdiction if clearance.status else None) or state.get("jurisdiction")
    content = (
        f"As a {profile.tier} member, you have access to: "
        f"{', '.join(profile.entitlements)}."
    )
    mandated = mandated_message_for(jurisdiction, "offer")
    update: dict[str, Any] = {}
    if mandated:
        content = f"{content} {mandated}"
        update["policy_flags"] = [
            PolicyFlag(
                flag_id=uuid.uuid4().hex, rule="jurisdiction_message_offer",
                disposition="messaged", jurisdiction=jurisdiction, raised_at=datetime.now(UTC),
            )
        ]
    update["messages"] = [AIMessage(content=content)]
    return update


def make_offer_agent_node(tools_factory: dict[Domain, Callable[..., Any]] | None = None) -> Any:
    """Thin node wrapper around `_apply_offer_agent` — see that function's
    docstring for the real logic and why it's a standalone function, not
    inlined here."""
    tools_factory = tools_factory or DEFAULT_TOOLS_FACTORY

    async def offer_agent(state: ResortFlowState) -> dict[str, Any]:
        return await _apply_offer_agent(state, tools_factory)

    return offer_agent


def make_human_handoff_node() -> Any:
    """T016 — stop all automated processing. `escalate` is expected already
    set by whichever node routed here; this node only guarantees it (a
    direct version-mismatch route to `RELEASE` never sets `escalate_reason`,
    since that vocabulary is closed to the three ADR-023 values — a version
    mismatch is deliberately not a fourth value; see `_route_from_entry`)."""

    async def human_handoff(state: ResortFlowState) -> dict[str, Any]:
        update: dict[str, Any] = {}
        if not state.get("escalate"):
            update["escalate"] = True
        text = "This conversation has been handed off to a team member and will not continue automatically."
        if state.get("escalate_reason") == "distress_signal":
            # Constitution IV: a gambling-distress handoff surfaces the jurisdiction's responsible-gaming resources,
            # appended deterministically from `policy.jurisdiction` (never generated, never omitted for tone).
            resources = mandated_message_for(state.get("jurisdiction") or "", "gaming_content")
            if resources:
                text = f"{text} {resources}"
        update["messages"] = [AIMessage(content=text)]
        return update

    return human_handoff
