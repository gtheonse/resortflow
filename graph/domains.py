"""Composition root: the one place that knows which concrete domains exist.

Everything else in `graph/` talks to the `DomainRegistry` abstraction
(`graph/participants.py`). Registration order is load-bearing (see
`DomainRegistry.fanout_domains`): dining, entertainment, wellness, stay.
"""

from __future__ import annotations

from functools import lru_cache

from agents.dining.participant import DiningParticipant
from agents.entertainment.participant import EntertainmentParticipant
from agents.gaming.participant import GamingDomain
from agents.stay.participant import StayParticipant
from agents.wellness.participant import WellnessParticipant
from graph.participants import DomainRegistry


@lru_cache(maxsize=1)
def default_registry() -> DomainRegistry:
    registry = DomainRegistry()
    for participant in (
        DiningParticipant(),
        EntertainmentParticipant(),
        WellnessParticipant(),
        StayParticipant(),
        GamingDomain(),
    ):
        registry.register(participant)
    return registry
