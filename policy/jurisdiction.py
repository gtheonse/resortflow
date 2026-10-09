"""Deterministic jurisdiction-mandated messaging (ADR-034, FR-012).

`JURISDICTION_MESSAGES` is static per-tenant, per-trigger data — legal copy
maintained by whoever owns compliance sign-off, not a rule this codebase
evaluates. The *decision* to append it (which triggers require it, that it
is deterministic and never omitted for tone) is code, in the caller.

Wording sourced from each tenant's own `content/corpus/{mgv,mhb}/
policies.yaml` responsible-gaming clause, matching the helpline/regulator
names already used there and in `agents/gaming/tests/fakes.py`'s fixture.
"""

from __future__ import annotations

from typing import Literal

Trigger = Literal["gaming_content", "offer"]

JURISDICTION_MESSAGES: dict[str, dict[Trigger, str]] = {
    "NV": {
        "gaming_content": (
            "Nevada Gaming Control Board (NGCB) notice: if you or someone you "
            "know has a gambling problem, help is available. Contact the "
            "Nevada Council on Problem Gambling at 1-800-522-4700."
        ),
        "offer": (
            "This offer is provided under Nevada Gaming Control Board (NGCB) "
            "regulation. Guests enrolled in Nevada's self-exclusion program "
            "are not eligible for gaming-related offers or inducements. "
            "Problem gambling help: Nevada Council on Problem Gambling, "
            "1-800-522-4700."
        ),
    },
    "MA": {
        "gaming_content": (
            "Massachusetts Gaming Commission (MGC) notice: if you or someone "
            "you know has a gambling problem, help is available. Contact the "
            "GameSense helpline at 1-800-327-5050."
        ),
        "offer": (
            "This offer is provided under Massachusetts Gaming Commission "
            "(MGC) regulation. Guests enrolled in the statewide Voluntary "
            "Self-Exclusion (VSE) program are not eligible for gaming-related "
            "offers or inducements. Problem gambling help: GameSense, "
            "1-800-327-5050."
        ),
    },
}
"""FR-019: jurisdiction-parameterized by design — the two tenants' own
regulators (NGCB, MGC) mandate different wording and different helplines.
This is the only static-data table in `policy/`; ADR-034 explains why a
lookup table with no evaluation semantics of its own does not reopen
ADR-028's rejection of rules-as-data."""


def mandated_message_for(jurisdiction: str, trigger: Trigger) -> str | None:
    """Deterministic lookup — never generated, never conditionally
    suppressed by anything other than "does an entry exist for this
    (jurisdiction, trigger) pair" (FR-012). Returns `None` if no message is
    configured; callers append nothing in that case, they never synthesize
    a substitute."""
    return JURISDICTION_MESSAGES.get(jurisdiction, {}).get(trigger)
