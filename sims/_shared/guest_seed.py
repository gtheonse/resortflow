"""Shared 50-guest-profile seed (T041), per
`specs/002-simulation-ecosystem/contracts/seed-data-spec.md`: 30 MGV / 20 MHB, generated
with a fixed random seed so re-seeding is deterministic (byte-for-byte comparable across
runs, per the seed-data-spec's Regeneration note).

`casino/seed.py` is this module's primary consumer (loyalty + responsible-gaming status
records keyed to every profile here). The other four sims accept a caller-supplied
`guest_id` in requests without requiring it to already exist in their own seeded data
(none of the frozen contracts require existence-validation of a guest reference at
booking time), so this module is "referenced by" them only in the sense that a
downstream test/demo scenario can pick a `guest_id` from here and expect casino-sim to
already have loyalty/RG data for it.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

SEED = 42  # fixed — never derived from wall-clock time (seed-data-spec.md Regeneration)

TENANT_COUNTS = [("MGV", 30), ("MHB", 20)]
JURISDICTION = {"MGV": "NV", "MHB": "MA"}

# Weighted toward lower tiers — realistic loyalty distribution shape.
TIER_WEIGHTS = (
    ["MEMBER"] * 10
    + ["SILVER"] * 6
    + ["GOLD"] * 3
    + ["PLATINUM"] * 2
    + ["CHAIRMAN"] * 1
)

MANDATED_MESSAGE = {
    "MGV": "Nevada Council on Problem Gambling: 1-800-522-4700. Gambling problem? Call NCPG.",
    "MHB": "Massachusetts GameSense/MGC: call or text the Massachusetts Problem Gambling Helpline 1-800-327-5050.",
}


@dataclass(frozen=True)
class GuestProfile:
    guest_id: str
    tenant_id: str
    display_name: str
    phone: str
    email: str
    tier: str
    points_balance: int
    comp_dollars: float
    self_excluded: bool
    exclusion_scope: str | None
    limit_flags: list[str] = field(default_factory=list)
    offers_permitted: bool = True
    gaming_content_permitted: bool = True


def _build_profiles() -> list[GuestProfile]:
    rng = random.Random(SEED)
    profiles: list[GuestProfile] = []
    for tenant_id, count in TENANT_COUNTS:
        for i in range(count):
            guest_id = f"guest-{tenant_id.lower()}-{i + 1:03d}"
            # First two guests per tenant are self-excluded: guarantees >= 1 per tenant
            # (mandatory, seed-data-spec.md) while keeping the overall total at 4 of 50
            # — comfortably inside the "small minority, e.g. 2-3 of 50" guidance.
            self_excluded = i < 2
            profiles.append(
                GuestProfile(
                    guest_id=guest_id,
                    tenant_id=tenant_id,
                    display_name=f"Guest {tenant_id} {i + 1:03d}",
                    phone=f"+1-555-{rng.randint(1000, 9999):04d}",
                    email=f"{guest_id}@example-synthetic.test",
                    tier=rng.choice(TIER_WEIGHTS),
                    points_balance=rng.randint(0, 50_000),
                    comp_dollars=round(rng.uniform(0, 2000), 2),
                    self_excluded=self_excluded,
                    exclusion_scope="property" if self_excluded else None,
                    limit_flags=["cooling_off"] if self_excluded else [],
                    offers_permitted=not self_excluded,
                    gaming_content_permitted=not self_excluded,
                )
            )
    return profiles


GUEST_PROFILES: list[GuestProfile] = _build_profiles()

assert len(GUEST_PROFILES) == 50
assert sum(1 for g in GUEST_PROFILES if g.tenant_id == "MGV") == 30
assert sum(1 for g in GUEST_PROFILES if g.tenant_id == "MHB") == 20
assert sum(1 for g in GUEST_PROFILES if g.self_excluded) >= 1
assert all(
    any(g.self_excluded for g in GUEST_PROFILES if g.tenant_id == t) for t in ("MGV", "MHB")
)
