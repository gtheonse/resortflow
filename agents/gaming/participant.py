"""Gaming's `StandaloneDomain` (ADR-061).

Deliberately NOT a `SagaParticipant`: the gaming domain is read-only (AGENTS.md
hard rule — no agent places a wager or executes a gaming transaction), so it has
no hold, confirm or release to implement and can never be registered into a saga.
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from agents.gaming.graph import compile_gaming_graph
from agents.gaming.tools import McpGamingTools
from graph.models import Domain


class GamingDomain:
    domain = Domain.GAMING

    def tools(self, tenant_id: str, guest_id: str) -> McpGamingTools:
        return McpGamingTools(tenant_id=tenant_id, guest_id=guest_id)

    def compile(self, tools: Any, checkpointer: BaseCheckpointSaver[Any] | None) -> Any:
        return compile_gaming_graph(tools, checkpointer=checkpointer)
