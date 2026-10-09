"""T051 — the gaming subgraph's only node: discover, then respond.

No hold/approve/confirm saga exists for this domain (AGENTS.md hard rule:
gaming is read-only). `make_lookup_node` dispatches on
`state["intent"]["intent_kind"]`, calls exactly one of the three
`GamingTools` methods, and turns the response into a guest-facing
`AIMessage` in the same superstep — there is nothing to hold between
"discover" and "respond", so there is no reason to split them into two
nodes the way every hold-then-confirm domain must.

Responsible-gaming suppression (Constitution: never bypassed, retried, or
worked around) is enforced here, not in a downstream node, via
`policy.gaming.require_gaming_clear` (feature 006, ADR-030) — called before
**all three** intent branches, not only `rg_status` (the concrete gap this
feature closed: `floor_games`/`loyalty_status` used to call their vendor
tool directly with no self-exclusion check at all). When not permitted, the
produced message surfaces `mandated_message` verbatim (when the vendor sent
one) and stops — no branch below this check ever reaches for a second tool
call. The non-bypass guarantee here is "this branch never reaches for
another tool after suppression is detected," not a filter applied after the
fact.

`mandated_message` is surfaced verbatim (not summarized/paraphrased)
whenever `list_casino_floor` returns one, too — AGENTS.md: these fields
"MUST be surfaced verbatim in any guest-facing message when present, never
dropped or summarized away."
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from gateway.gaming.models import FloorGamesResponse, LoyaltyProfile, ResponsibleGamingStatus
from langchain_core.messages import AIMessage

from agents.gaming.state import GamingState
from agents.gaming.tools import GamingToolError, GamingTools
from graph.errors import classify
from graph.models import Domain, PolicyFlag, ToolFailure
from policy.gaming import require_gaming_clear


def _format_rg_status(status: ResponsibleGamingStatus) -> str:
    if status.self_excluded or not status.gaming_content_permitted:
        lines = ["You currently cannot access gaming content on this account."]
        if status.mandated_message:
            lines.append(status.mandated_message)
        if status.support_resources:
            lines.append(
                "Support resources: "
                + "; ".join(str(resource) for resource in status.support_resources)
            )
        return " ".join(lines)

    lines = [f"Responsible gaming status: in good standing ({status.jurisdiction})."]
    if status.limit_flags:
        lines.append("Active limits: " + ", ".join(status.limit_flags) + ".")
    if status.mandated_message:
        lines.append(status.mandated_message)
    return " ".join(lines)


def _format_loyalty_status(profile: LoyaltyProfile) -> str:
    lines = [
        (f"Loyalty tier: {profile.tier} — {profile.points_balance} points, "
        f"${profile.comp_dollars:.2f} in comp dollars.")
    ]
    if profile.tier_expires:
        lines.append(f"Tier expires {profile.tier_expires}.")
    if profile.entitlements:
        lines.append("Entitlements: " + ", ".join(profile.entitlements) + ".")
    mandated_message = getattr(profile, "mandated_message", None)
    if mandated_message:
        lines.append(mandated_message)
    return " ".join(lines)


def _format_floor_games(response: FloorGamesResponse, *, game_type: str | None) -> str:
    if not response.games:
        scope = f" for {game_type}" if game_type else ""
        lines = [f"No casino floor games currently listed{scope}."]
    else:
        game_lines = []
        for game in response.games:
            minimum = f", ${game.table_minimum:.0f} minimum" if game.table_minimum is not None else ""
            game_lines.append(f"{game.game_type} ({game.location_zone}{minimum}, {game.hours})")
        lines = ["Casino floor: " + "; ".join(game_lines) + "."]
    if response.mandated_message:
        lines.append(response.mandated_message)
    return " ".join(lines)


def _policy_flag(rule: str, disposition: str, jurisdiction: str | None) -> PolicyFlag:
    return PolicyFlag(
        flag_id=uuid.uuid4().hex, rule=rule, disposition=disposition,
        jurisdiction=jurisdiction, raised_at=datetime.now(UTC),
    )


def make_lookup_node(tools: GamingTools) -> Any:
    async def lookup(state: GamingState) -> dict[str, Any]:
        intent = state.get("intent") or {}
        intent_kind = intent.get("intent_kind", "floor_games")
        guest_id = state["guest_id"]

        # Feature 006 (ADR-030): checked before ALL three intent branches,
        # every turn, never cached — closes the gap where floor_games/
        # loyalty_status used to skip this check entirely.
        clearance = await require_gaming_clear(tools, guest_id=guest_id)
        if not clearance.permitted:
            if clearance.failure_reason == "dependency_unavailable":
                return {
                    "messages": [
                        AIMessage(
                            content="Gaming information is temporarily unavailable right now — "
                            "this has been flagged for follow-up."
                        )
                    ],
                    "policy_flags": [
                        _policy_flag("dependency_unavailable", "blocked", None),
                        _policy_flag("dependency_unavailable", "escalated", None),
                    ],
                }
            # self_excluded (or gaming_content_permitted=False for another reason)
            assert clearance.status is not None
            return {
                "messages": [AIMessage(content=_format_rg_status(clearance.status))],
                "policy_flags": [
                    _policy_flag(
                        "self_exclusion_active", "blocked", clearance.status.jurisdiction
                    )
                ],
            }

        try:
            if intent_kind == "rg_status":
                # Already fetched by require_gaming_clear above — reuse
                # rather than calling check_rg_status a second time.
                assert clearance.status is not None
                message = _format_rg_status(clearance.status)
            elif intent_kind == "loyalty_status":
                profile = await tools.get_loyalty_status(guest_id=guest_id)
                message = _format_loyalty_status(profile)
            else:
                game_type = intent.get("game_type")
                floor = await tools.list_casino_floor(game_type=game_type)
                message = _format_floor_games(floor, game_type=game_type)
        except GamingToolError as exc:
            error_class = classify(exc.vendor_code, exc.http_status, None)
            failure = ToolFailure(
                domain=Domain.GAMING,
                tool=(
                    "check_rg_status"
                    if intent_kind == "rg_status"
                    else "get_loyalty_status"
                    if intent_kind == "loyalty_status"
                    else "list_casino_floor"
                ),
                error_class=error_class,
                vendor_code=exc.vendor_code,
                http_status=exc.http_status,
                retry_after_seconds=exc.retry_after_seconds,
                detail=exc.detail,
            )
            return {
                "failures": [failure],
                "messages": [
                    AIMessage(content="Sorry, I couldn't retrieve that gaming information right now.")
                ],
            }

        return {"messages": [AIMessage(content=message)]}

    return lookup
