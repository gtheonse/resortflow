"""Console-only enrichment on top of `graph/turn_summary.py`'s shared
turn-outcome shaping (feature 008.5 gap closure): adds a live groundedness
(faithfulness) score and a cost placeholder, neither of which belongs in the
production `graph/api.py` response (see `graph/turn_summary.py`'s module
docstring for why those two fields were split out rather than moved
verbatim).

Groundedness is a direct library import of `eval.retrieval_scorer
.score_faithfulness` (not a subprocess, not an HTTP call to the eval
service — there is no such service) — the same scoring function the offline
eval gate uses, run live against this turn's actual retrieved context and
response. It degrades to "unavailable" on `JudgeUnavailableError` exactly
like the eval gate's own `INCOMPLETE` verdict does when no judge provider is
configured (feature 007) — this console surfaces that as an honest status,
never a fabricated score.
"""
from __future__ import annotations

from typing import Any

from eval.judge import JudgeUnavailableError
from eval.retrieval_scorer import score_faithfulness
from graph.turn_summary import _classify_interrupt
from graph.turn_summary import summarize_turn as _base_summarize_turn


def _groundedness(result: dict[str, Any], *, thread_id: str) -> dict[str, Any]:
    retrieved = result.get("retrieved") or []
    messages = result.get("messages") or []
    if not retrieved or not messages:
        return {"status": "not_applicable", "reason": "no retrieved context on this turn"}

    answer = getattr(messages[-1], "content", "") or ""
    context = [getattr(chunk, "content", "") for chunk in retrieved]
    try:
        score = score_faithfulness(
            answer=str(answer),
            retrieved_context=context,
            domain=str(result.get("_domain_hint", "dining")),
            conversation_id=thread_id,
        )
    except JudgeUnavailableError as exc:
        return {"status": "unavailable", "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 — a scoring failure must never break the console
        return {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}

    return {
        "status": "scored",
        "score": score.mean,
        "judge_model": score.judge_model.model_id,
        "judge_provider": score.judge_model.provider,
    }


def summarize_turn(
    result: dict[str, Any], *, thread_id: str, node_order: list[list[str]]
) -> dict[str, Any]:
    summary = _base_summarize_turn(result, thread_id=thread_id, node_order=node_order)
    summary["groundedness"] = _groundedness(result, thread_id=thread_id)
    summary["cost"] = {"status": "not_instrumented", "reason": "cost panel deferred, see feature 008 handoff"}
    return summary


__all__ = ["_classify_interrupt", "summarize_turn"]
