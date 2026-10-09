"""T021 — deterministic tool-selection / unnecessary-step-rate scoring via
sequence diffing (FR-002, ADR-043). The judge model is reserved for exactly
one sub-case: "plan quality" when a case legitimately has more than one
valid reference sequence (e.g. checking availability before vs. after
resolving a dietary conflict) — see `score_plan_quality`. Everything else
here is arithmetic on purpose (constitution: "never put an LLM where
arithmetic will do").

Scoring rule (documented per T021's ask, since this is our call): a `Turn`'s
`tool_calls` (each a `{"kind": ..., ...}` dict, per
`eval/conversation_record.py`) is reduced to its `kind` sequence and
compared against a reference `kind` sequence.

- **tool-selection score** = 1.0 if the actual kind sequence exactly
  equals the reference kind sequence, else the fraction of reference kinds
  that appear anywhere in the actual sequence in the same relative order
  (a longest-common-subsequence ratio over the reference length) — this
  rewards "got the right steps, wrong order/extras" partial credit rather
  than an all-or-nothing 0.0, while still scoring 1.0 only for an exact
  match.
- **unnecessary-step-rate** = (len(actual) - len(LCS)) / len(actual) when
  `actual` is non-empty, else 0.0 — the fraction of actual tool calls that
  were not part of matching the reference plan at all. Reported as a
  `MetricScore` with `gate_type="binary"` semantics inverted at the
  call site (lower is better, like cost/latency) since it is folded into
  `eval.gate`'s `_LOWER_IS_BETTER` set by name.
"""

from __future__ import annotations

from typing import Any

from eval.conversation_record import Turn
from eval.judge import JudgeClient, build_default_judge_client, pinned_model_id, provider_name
from eval.models import JudgeModelRecord, MetricScore
from prompts.registry import resolve

_PLAN_QUALITY_PROMPT_ID = "eval_trajectory_judge_plan_quality"
"""T047/T050: migrated verbatim from this module's original `_SYSTEM_PROMPT` and
`score_plan_quality`'s user f-string — see `prompts/tests/test_judge_templates.py`'s golden fixture."""


def _judge_model_record(*, prompt_id: str | None = None, prompt_sha256: str | None = None, prompt_version: str | None = None) -> JudgeModelRecord:
    return JudgeModelRecord(
        provider=provider_name(), model_id=pinned_model_id(),
        prompt_id=prompt_id, prompt_sha256=prompt_sha256, prompt_version=prompt_version,
    )


def _kinds(tool_calls: list[dict[str, Any]]) -> list[str]:
    return [str(tc.get("kind", "")) for tc in tool_calls]


def _lcs_length(a: list[str], b: list[str]) -> int:
    """Standard O(len(a)*len(b)) longest-common-subsequence length —
    trivially small inputs here (single-digit tool-call counts per turn),
    so no need for a smarter algorithm."""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        curr = [0] * (len(b) + 1)
        for j in range(1, len(b) + 1):
            if a[i - 1] == b[j - 1]:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr
    return prev[len(b)]


def score_tool_selection(
    *,
    actual_tool_calls: list[dict[str, Any]],
    reference_kinds: list[str],
    domain: str,
    conversation_id: str,
    step_id: str | None = None,
) -> MetricScore:
    """FR-002's tool-selection metric — see module docstring for the
    exact-match-vs-LCS-ratio rule."""
    actual_kinds = _kinds(actual_tool_calls)
    if actual_kinds == reference_kinds:
        score = 1.0
    elif reference_kinds:
        score = _lcs_length(actual_kinds, reference_kinds) / len(reference_kinds)
    else:
        # No reference steps expected — perfect score iff none were taken.
        score = 1.0 if not actual_kinds else 0.0
    return MetricScore(
        metric_name="tool_selection",
        domain=domain,
        conversation_id=conversation_id,
        gate_type="statistical",
        raw_values=[score],
        judge_model=_judge_model_record(),
        step_id=step_id,
    )


def score_unnecessary_step_rate(
    *,
    actual_tool_calls: list[dict[str, Any]],
    reference_kinds: list[str],
    domain: str,
    conversation_id: str,
    step_id: str | None = None,
) -> MetricScore:
    """FR-002's unnecessary-step-rate metric. Lower is better — `metric_name`
    is deliberately NOT added to `eval.gate._LOWER_IS_BETTER` automatically;
    callers wiring this into the gate must confirm that set includes
    "unnecessary_step_rate" (it does, see eval/gate.py) before trusting the
    floor direction."""
    actual_kinds = _kinds(actual_tool_calls)
    if not actual_kinds:
        rate = 0.0
    else:
        matched = _lcs_length(actual_kinds, reference_kinds)
        rate = (len(actual_kinds) - matched) / len(actual_kinds)
    return MetricScore(
        metric_name="unnecessary_step_rate",
        domain=domain,
        conversation_id=conversation_id,
        gate_type="statistical",
        raw_values=[rate],
        judge_model=_judge_model_record(),
        step_id=step_id,
    )


def score_plan_quality(
    *,
    actual_tool_calls: list[dict[str, Any]],
    acceptable_reference_sequences: list[list[dict[str, Any]]],
    task_description: str,
    domain: str,
    conversation_id: str,
    step_id: str | None = None,
    judge_client: JudgeClient | None = None,
) -> MetricScore:
    """The one judge-backed trajectory metric (ADR-043): used only when a
    case has genuinely more than one valid reference plan
    (`acceptable_reference_sequences`, a `list[list[dict]]` of alternative
    acceptable tool-call sequences) — arithmetic sequence-diffing against a
    single reference doesn't apply because there is no single reference."""
    client = judge_client or build_default_judge_client()
    actual_kinds = _kinds(actual_tool_calls)
    alternatives_block = "\n".join(
        f"- Option {i + 1}: {_kinds(seq)}" for i, seq in enumerate(acceptable_reference_sequences)
    ) or "(no acceptable alternatives specified)"
    resolved = resolve(
        _PLAN_QUALITY_PROMPT_ID,
        required_vars={},
        runtime_vars={
            "task_description": task_description,
            "alternatives_block": alternatives_block,
            "actual_kinds": str(actual_kinds),
        },
    )
    system, user = resolved.messages
    import json

    raw = client.complete(system=system["content"], user=user["content"], prompt=resolved)
    score = float(json.loads(raw)["score"])
    if not (0.0 <= score <= 1.0):
        raise ValueError(f"judge score out of range [0,1]: {score}")
    return MetricScore(
        metric_name="plan_quality",
        domain=domain,
        conversation_id=conversation_id,
        gate_type="statistical",
        raw_values=[score],
        judge_model=_judge_model_record(
            prompt_id=resolved.id, prompt_sha256=resolved.sha256, prompt_version=resolved.version
        ),
        step_id=step_id,
    )


def kinds_from_domain_result(result: dict[str, Any]) -> list[str]:
    """T022/T023 glue: the four domain runners' `run_case` return a raw
    subgraph-state dict (`holds`/`confirmations`/`failures`, model objects,
    not `ConversationRecord.Turn` dicts) — this derives the same `kind`
    vocabulary `eval/conversation_record.py` uses (`hold`/`confirm`/
    `failure`) from that dict shape, in `holds` then `confirmations` then
    `failures` order.

    **Known approximation**: a subgraph-state dict has no single global
    ordering across those three lists the way a replayed checkpoint history
    does — this fixed order matches the common happy-path
    (hold -> confirm) and failure-path (hold -> failure) shapes seen in the
    existing 25-ish cases per domain, but a case with an unusual interleaving
    (e.g. hold, fail, retry-hold, confirm) would not be told apart from
    (hold, hold, fail, confirm) by kind alone. Acceptable for T022/T023's
    goal (closing SC-001's "all five domains get trajectory scoring"); a
    more precise ordering would require sourcing from
    `build_conversation_record` instead, which needs a real checkpoint
    history the domain runners don't build today."""
    kinds: list[str] = []
    kinds.extend("hold" for _ in result.get("holds", []) or [])
    kinds.extend("confirm" for _ in result.get("confirmations", []) or [])
    kinds.extend("failure" for _ in result.get("failures", []) or [])
    return kinds


def score_domain_result_trajectory(
    result: dict[str, Any],
    *,
    reference_kinds: list[str],
    domain: str,
    conversation_id: str,
) -> list[MetricScore]:
    """T022/T023 entrypoint: scores a domain runner's raw subgraph-result
    dict (see `kinds_from_domain_result`) against a case-declared
    `expect.tool_sequence` reference, without needing a full
    `ConversationRecord` rebuild inside each domain's `run_case`."""
    actual_tool_calls = [{"kind": k} for k in kinds_from_domain_result(result)]
    return [
        score_tool_selection(
            actual_tool_calls=actual_tool_calls,
            reference_kinds=reference_kinds,
            domain=domain,
            conversation_id=conversation_id,
        ),
        score_unnecessary_step_rate(
            actual_tool_calls=actual_tool_calls,
            reference_kinds=reference_kinds,
            domain=domain,
            conversation_id=conversation_id,
        ),
    ]


def attach_trajectory_scores(
    case: dict[str, Any], result: dict[str, Any], outcome: dict[str, Any], *, domain: str
) -> None:
    """T022/T023 wiring: if a case declares `expect.tool_sequence` (a list
    of reference `kind` strings — "hold"/"confirm"/"failure"), score the
    actual result against it and attach `outcome["trajectory_scores"]`.
    Cases without a declared reference are left untouched — this is
    additive to the domain runners' existing local boolean assertions, not
    a replacement gate (T022's ask), so the ~100 existing cases across the
    four domains that predate this feature don't need a reference sequence
    to keep passing."""
    reference_kinds = (case.get("expect") or {}).get("tool_sequence")
    if reference_kinds is None:
        return
    scores = score_domain_result_trajectory(
        result, reference_kinds=reference_kinds, domain=domain, conversation_id=case["id"]
    )
    outcome["trajectory_scores"] = {s.metric_name: s.raw_values[0] for s in scores}


def score_turn_trajectory(
    turn: Turn,
    *,
    reference_kinds: list[str],
    domain: str,
    conversation_id: str,
    step_id: str | None = None,
) -> list[MetricScore]:
    """Convenience entrypoint for domain runners (T022/T023): both
    arithmetic metrics for one `Turn` against its single reference
    sequence."""
    return [
        score_tool_selection(
            actual_tool_calls=turn.tool_calls,
            reference_kinds=reference_kinds,
            domain=domain,
            conversation_id=conversation_id,
            step_id=step_id,
        ),
        score_unnecessary_step_rate(
            actual_tool_calls=turn.tool_calls,
            reference_kinds=reference_kinds,
            domain=domain,
            conversation_id=conversation_id,
            step_id=step_id,
        ),
    ]
