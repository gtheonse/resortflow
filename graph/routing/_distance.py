"""Feature 010 — restricted Damerau-Levenshtein (optimal string alignment)
distance, shared by `vocabulary.py` (the load-time collision check) and
`matcher.py` (tier 0 matching), so both apply the identical tolerance rule
(research R-03). Lives in its own module, imported by `vocabulary.py`
first, to avoid a `matcher.py` <-> `vocabulary.py` import cycle — `matcher.py`
needs `Vocabulary`/`DEFAULT`, and `vocabulary.py`'s collision check needs
this distance function, but neither module needs the other."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from graph.routing.vocabulary import ToleranceConfig


def edit_distance(a: str, b: str, max_distance: int | None = None) -> int:
    """OSA distance: insertion, deletion, substitution (cost 1 each), and
    an adjacent transposition (cost 1) — never a "true" Damerau edit that
    revisits already-transposed characters, which OSA deliberately excludes
    (simpler, and the two never differ within the two-edit budgets this
    project uses).

    Rolling-row implementation (`O(len_b)` space, not a full
    `len_a x len_b` matrix) with row-level early abandonment when
    `max_distance` is given: SC-007's 5 ms budget depends on this being
    cheap, since the matcher calls it for every (token, candidate) pair
    within the length-bucketed shortlist. `max_distance`, when given, both
    short-circuits a gross length mismatch up front and stops the DP the
    moment an entire row's minimum already exceeds the budget (the true
    distance is only ever needed exactly when it's within budget — the
    matcher only asks "is this within tolerance", never "what is the exact
    distance" for a rejected candidate)."""
    if a == b:
        return 0
    if max_distance is not None and abs(len(a) - len(b)) > max_distance:
        return max_distance + 1

    len_a, len_b = len(a), len(b)
    two_back = list(range(len_b + 1))
    prev = list(range(len_b + 1))
    for i in range(1, len_a + 1):
        curr = [i] + [0] * len_b
        a_i = a[i - 1]
        for j in range(1, len_b + 1):
            cost = 0 if a_i == b[j - 1] else 1
            best = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a_i == b[j - 2] and a[i - 2] == b[j - 1]:
                best = min(best, two_back[j - 2] + 1)
            curr[j] = best
        if max_distance is not None and min(curr) > max_distance:
            return max_distance + 1
        two_back, prev = prev, curr
    return prev[len_b]


def is_single_adjacent_swap(a: str, b: str) -> bool:
    """True iff `b` is `a` with exactly one pair of adjacent characters
    transposed and nothing else changed — the only edit a four-letter word
    tolerates (research R-03: "a swap-only length")."""
    if len(a) != len(b):
        return False
    diffs = [i for i in range(len(a)) if a[i] != b[i]]
    if len(diffs) != 2:
        return False
    i, j = diffs
    return j == i + 1 and a[i] == b[j] and a[j] == b[i]


def within_tolerance(token: str, candidate: str, *, tolerance: ToleranceConfig) -> bool:
    """Applies research R-03's length-banded rule, keyed off `len(token)`
    (the guest's typed word — the reference tolerance is banded against
    what the guest actually typed, not the canonical vocabulary spelling,
    since a typo can be a character short or long)."""
    if token == candidate:
        return True
    length = len(token)
    if length <= tolerance.exact_max_len:
        return False
    if length == tolerance.swap_only_len:
        return is_single_adjacent_swap(token, candidate)
    if length < tolerance.one_edit_min_len:
        return False
    budget = 2 if length >= tolerance.two_edit_min_len else 1
    return edit_distance(token, candidate, max_distance=budget) <= budget
