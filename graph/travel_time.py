"""T010 — the deterministic conflict checker's travel-time input (ADR-025).

A static, per-tenant zone-to-zone minutes matrix, checked into version control like any
other configuration — not a live routing/maps service (ADR-025's Option 3, chosen: "two
resort properties, not a city; zone-level granularity is the right precision for a
scheduling conflict checker, not a walking-directions app"). `graph.models.Proposal`
already carries `location_zone` for exactly this purpose ("Travel-time input," feature
001) — this module is what resolves a pair of those into minutes.

Zone names below are transcribed from the actual seed data each domain writes
(`sims/*/seed.py`), not invented — `graph.supervisor.CONFLICT_CHECK` only ever sees zone
strings that a real `Proposal` populated from a real vendor response, so the matrix must
cover exactly those literal strings, per tenant, or fall back to the documented default.
"""

from __future__ import annotations

from typing import Final

# --- MGV (Meridian Grand, Las Vegas) zones -----------------------------------
# dining: sims/dining/seed.py VENUES_MGV; casino: sims/casino/seed.py FLOOR_GAMES["MGV"];
# entertainment: sims/ticketing/seed.py VENUE_NAMES["MGV"]; amenities: category + "_zone";
# stay: sims/pms/main.py's "main_tower" default.

_MGV_ZONES: Final[tuple[str, ...]] = (
    "tower_a",
    "tower_b",
    "poolside",
    "casino_floor",
    "main_floor",
    "high_limit_salon",
    "poker_room",
    "arena_zone",
    "theater_zone",
    "spa_zone",
    "fitness_zone",
    "cabana_zone",
    "golf_zone",
    "main_tower",
)

# --- MHB (Meridian Harbor, Boston) zones -------------------------------------

_MHB_ZONES: Final[tuple[str, ...]] = (
    "waterfront",
    "lobby_level",
    "main_floor",
    "poker_room",
    "hall_zone",
    "spa_zone",
    "fitness_zone",
    "cabana_zone",
    "golf_zone",
    "main_tower",
)

# --- Zone-to-zone minutes, one property at a time ----------------------------
# Hand-curated per ADR-025 ("a small, defensible table any engineer could sanity-check
# against a property map in thirty seconds," not a computed distance). Symmetric by
# construction (`_lookup` tries both orderings); same-zone pairs are always 0 and are not
# repeated below. Unlisted pairs fall back to `_DEFAULT_CROSS_ZONE_MINUTES` — see that
# constant's docstring for why a default exists at all.

_MGV_MINUTES: Final[dict[tuple[str, str], int]] = {
    ("tower_a", "tower_b"): 8,
    ("tower_a", "poolside"): 6,
    ("tower_a", "casino_floor"): 5,
    ("tower_a", "main_floor"): 5,
    ("tower_a", "high_limit_salon"): 7,
    ("tower_a", "poker_room"): 6,
    ("tower_a", "arena_zone"): 10,
    ("tower_a", "theater_zone"): 9,
    ("tower_a", "spa_zone"): 4,
    ("tower_a", "fitness_zone"): 5,
    ("tower_a", "cabana_zone"): 7,
    ("tower_a", "golf_zone"): 15,
    ("tower_a", "main_tower"): 3,
    ("tower_b", "poolside"): 7,
    ("tower_b", "casino_floor"): 4,
    ("tower_b", "main_floor"): 4,
    ("tower_b", "high_limit_salon"): 6,
    ("tower_b", "poker_room"): 5,
    ("tower_b", "arena_zone"): 9,
    ("tower_b", "theater_zone"): 8,
    ("tower_b", "spa_zone"): 6,
    ("tower_b", "fitness_zone"): 6,
    ("tower_b", "cabana_zone"): 8,
    ("tower_b", "golf_zone"): 16,
    ("tower_b", "main_tower"): 4,
    ("poolside", "casino_floor"): 6,
    ("poolside", "main_floor"): 6,
    ("poolside", "high_limit_salon"): 8,
    ("poolside", "poker_room"): 7,
    ("poolside", "arena_zone"): 11,
    ("poolside", "theater_zone"): 10,
    ("poolside", "spa_zone"): 3,
    ("poolside", "fitness_zone"): 4,
    ("poolside", "cabana_zone"): 2,
    ("poolside", "golf_zone"): 14,
    ("poolside", "main_tower"): 6,
    ("casino_floor", "main_floor"): 2,
    ("casino_floor", "high_limit_salon"): 3,
    ("casino_floor", "poker_room"): 3,
    ("casino_floor", "arena_zone"): 7,
    ("casino_floor", "theater_zone"): 6,
    ("casino_floor", "spa_zone"): 7,
    ("casino_floor", "fitness_zone"): 8,
    ("casino_floor", "cabana_zone"): 9,
    ("casino_floor", "golf_zone"): 18,
    ("casino_floor", "main_tower"): 5,
    ("main_floor", "high_limit_salon"): 3,
    ("main_floor", "poker_room"): 3,
    ("main_floor", "arena_zone"): 6,
    ("main_floor", "theater_zone"): 5,
    ("main_floor", "spa_zone"): 7,
    ("main_floor", "fitness_zone"): 8,
    ("main_floor", "cabana_zone"): 9,
    ("main_floor", "golf_zone"): 18,
    ("main_floor", "main_tower"): 5,
    ("high_limit_salon", "poker_room"): 2,
    ("high_limit_salon", "arena_zone"): 8,
    ("high_limit_salon", "theater_zone"): 7,
    ("high_limit_salon", "spa_zone"): 9,
    ("high_limit_salon", "fitness_zone"): 10,
    ("high_limit_salon", "cabana_zone"): 11,
    ("high_limit_salon", "golf_zone"): 19,
    ("high_limit_salon", "main_tower"): 6,
    ("poker_room", "arena_zone"): 7,
    ("poker_room", "theater_zone"): 6,
    ("poker_room", "spa_zone"): 9,
    ("poker_room", "fitness_zone"): 10,
    ("poker_room", "cabana_zone"): 11,
    ("poker_room", "golf_zone"): 19,
    ("poker_room", "main_tower"): 6,
    ("arena_zone", "theater_zone"): 4,
    ("arena_zone", "spa_zone"): 12,
    ("arena_zone", "fitness_zone"): 12,
    ("arena_zone", "cabana_zone"): 13,
    ("arena_zone", "golf_zone"): 20,
    ("arena_zone", "main_tower"): 9,
    ("theater_zone", "spa_zone"): 10,
    ("theater_zone", "fitness_zone"): 11,
    ("theater_zone", "cabana_zone"): 12,
    ("theater_zone", "golf_zone"): 19,
    ("theater_zone", "main_tower"): 8,
    ("spa_zone", "fitness_zone"): 3,
    ("spa_zone", "cabana_zone"): 4,
    ("spa_zone", "golf_zone"): 16,
    ("spa_zone", "main_tower"): 4,
    ("fitness_zone", "cabana_zone"): 5,
    ("fitness_zone", "golf_zone"): 17,
    ("fitness_zone", "main_tower"): 5,
    ("cabana_zone", "golf_zone"): 15,
    ("cabana_zone", "main_tower"): 7,
    ("golf_zone", "main_tower"): 14,
}

_MHB_MINUTES: Final[dict[tuple[str, str], int]] = {
    ("waterfront", "lobby_level"): 5,
    ("waterfront", "main_floor"): 7,
    ("waterfront", "poker_room"): 8,
    ("waterfront", "hall_zone"): 9,
    ("waterfront", "spa_zone"): 4,
    ("waterfront", "fitness_zone"): 5,
    ("waterfront", "cabana_zone"): 3,
    ("waterfront", "golf_zone"): 20,
    ("waterfront", "main_tower"): 6,
    ("lobby_level", "main_floor"): 3,
    ("lobby_level", "poker_room"): 4,
    ("lobby_level", "hall_zone"): 5,
    ("lobby_level", "spa_zone"): 6,
    ("lobby_level", "fitness_zone"): 7,
    ("lobby_level", "cabana_zone"): 7,
    ("lobby_level", "golf_zone"): 22,
    ("lobby_level", "main_tower"): 3,
    ("main_floor", "poker_room"): 2,
    ("main_floor", "hall_zone"): 6,
    ("main_floor", "spa_zone"): 8,
    ("main_floor", "fitness_zone"): 9,
    ("main_floor", "cabana_zone"): 9,
    ("main_floor", "golf_zone"): 24,
    ("main_floor", "main_tower"): 5,
    ("poker_room", "hall_zone"): 7,
    ("poker_room", "spa_zone"): 9,
    ("poker_room", "fitness_zone"): 10,
    ("poker_room", "cabana_zone"): 10,
    ("poker_room", "golf_zone"): 25,
    ("poker_room", "main_tower"): 6,
    ("hall_zone", "spa_zone"): 11,
    ("hall_zone", "fitness_zone"): 12,
    ("hall_zone", "cabana_zone"): 12,
    ("hall_zone", "golf_zone"): 26,
    ("hall_zone", "main_tower"): 8,
    ("spa_zone", "fitness_zone"): 3,
    ("spa_zone", "cabana_zone"): 3,
    ("spa_zone", "golf_zone"): 21,
    ("spa_zone", "main_tower"): 4,
    ("fitness_zone", "cabana_zone"): 4,
    ("fitness_zone", "golf_zone"): 22,
    ("fitness_zone", "main_tower"): 5,
    ("cabana_zone", "golf_zone"): 19,
    ("cabana_zone", "main_tower"): 6,
    ("golf_zone", "main_tower"): 21,
}

_MATRICES: Final[dict[str, dict[tuple[str, str], int]]] = {
    "MGV": _MGV_MINUTES,
    "MHB": _MHB_MINUTES,
}

_KNOWN_ZONES: Final[dict[str, frozenset[str]]] = {
    "MGV": frozenset(_MGV_ZONES),
    "MHB": frozenset(_MHB_ZONES),
}

_DEFAULT_CROSS_ZONE_MINUTES: Final[int] = 20
"""Fallback for any zone pair not explicitly listed above — deliberately
conservative (worse than any real pair on either property) so an
un-curated zone combination fails a tight schedule loudly (a false
conflict `ConflictCheck` surfaces to `Planner`) rather than silently
passing one that might not be feasible (ADR-025's foreclosed
consequence: no per-request precision beyond zone-level granularity —
an unlisted pair is a data gap to fix in this table, not a routing
problem to solve at runtime)."""


class UnknownZoneError(ValueError):
    """Raised when a zone string isn't recognized for the given tenant at
    all (as opposed to a recognized-but-unlisted *pair*, which uses the
    conservative default instead of raising) — this indicates a real data
    problem (a vendor seed added a zone this table has never heard of),
    worth surfacing loudly rather than silently defaulting."""


def travel_minutes(tenant_id: str, zone_a: str, zone_b: str) -> int:
    """Minutes required to travel from `zone_a` to `zone_b` on `tenant_id`'s
    property. Symmetric — order does not matter. Zero for the same zone.

    Raises `UnknownZoneError` if either zone is not a recognized zone for
    this tenant at all. Falls back to `_DEFAULT_CROSS_ZONE_MINUTES` for a
    recognized-but-unlisted pair.
    """
    known = _KNOWN_ZONES.get(tenant_id)
    if known is None:
        raise UnknownZoneError(f"no travel-time matrix configured for tenant {tenant_id!r}")
    if zone_a not in known:
        raise UnknownZoneError(f"zone {zone_a!r} is not a known zone for tenant {tenant_id!r}")
    if zone_b not in known:
        raise UnknownZoneError(f"zone {zone_b!r} is not a known zone for tenant {tenant_id!r}")
    if zone_a == zone_b:
        return 0
    matrix = _MATRICES[tenant_id]
    return matrix.get((zone_a, zone_b)) or matrix.get((zone_b, zone_a)) or _DEFAULT_CROSS_ZONE_MINUTES
