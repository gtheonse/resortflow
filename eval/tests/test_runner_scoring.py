"""T024/T025/T026 tests — the PR/merge-queue tier scoring wiring in
`eval/runner.py`. Every judge call goes through `FakeJudgeClient`
(monkeypatched over `build_default_judge_client`) — no live network call,
per repo convention."""

from __future__ import annotations

import pytest

import eval.runner as runner_mod
from eval.intent_scorer import IntentExtractorUnavailableError
from eval.judge import FakeJudgeClient, JudgeUnavailableError
from eval.models import EvaluationRun, JudgeModelRecord


@pytest.fixture(autouse=True)
def _no_live_intent_extractor(monkeypatch: pytest.MonkeyPatch) -> None:
    """T053/T056 added `_score_intent` to both tiers, which — like the judge client — must never
    make a live call from a unit test. This repo's `.env` carries real Azure credentials for local
    development, so without this the tests below would silently start hitting a live endpoint the
    moment `_score_intent` was wired in. Tests that want to exercise the intent-scoring path do so
    directly against `eval.intent_scorer` with a fake extractor (`test_intent_scorer.py`,
    `test_degraded_prompt_gate.py`), not through `eval.runner`."""

    def _raise() -> None:
        raise IntentExtractorUnavailableError("no credentials in this test")

    monkeypatch.setattr(runner_mod, "default_intent_extractor", _raise)


_ROUTING_METRIC_NAMES = frozenset(
    {
        "routing_accuracy", "llm_tier_share", "oos_passthrough_rate",
        "substring_trap_misroutes", "modify_book_starts", "followup_accuracy",
        # Feature 011 (contracts/browse-eval-gate.md)
        "browse_accuracy", "browse_catalogue_misroute", "near_miss_browse_leak", "stay_length_accuracy",
        "browse_llm_tier_share",
    }
)
"""Feature 010's deterministic, judge-independent metrics — excluded from
this file's judge-repeat and judge-outage assertions, both written before
`eval.routing_scorer` existed."""


def _new_run(tier: str = "pr") -> EvaluationRun:
    return EvaluationRun(
        run_id="test-run",
        tier=tier,  # type: ignore[arg-type]
        code_ref="deadbeef",
        judge_model=JudgeModelRecord(provider="fake", model_id="fake-model"),
    )


def test_pr_tier_scores_retrieval_and_marks_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeJudgeClient('{"score": 0.95, "reasoning": "good"}')
    monkeypatch.setattr(runner_mod, "build_default_judge_client", lambda: fake)
    monkeypatch.setattr(runner_mod, "_changed_files", list)

    run = _new_run("pr")
    runner_mod._run_pr_tier(run)

    assert run.metric_scores, "expected retrieval metric scores to be recorded"
    assert all(s.passed is not None for s in run.metric_scores)
    # T053/T056: `_score_intent` marks its own case set NOT_EVALUATED when no credentials are
    # configured (the `_no_live_intent_extractor` fixture simulates that) — distinct from a
    # retrieval-scoring failure, and does not affect the retrieval assertions above.
    # T080: since the holdout split gained real `requires_tier2` cases (previously it had none,
    # which made `llm_tier_share` vacuously 0.0 on every holdout run — the exact gap T080 fixed),
    # `_score_routing` also marks `routing_tier2_cases` NOT_EVALUATED here, the same
    # no-credentials-in-this-test-process behavior as `dining_intent_cases`, never a fabricated
    # pass.
    assert set(run.not_evaluated_cases) == {"dining_intent_cases", "routing_tier2_cases"}


def test_merge_queue_tier_repeats_three_times(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeJudgeClient('{"score": 0.9, "reasoning": "good"}')
    monkeypatch.setattr(runner_mod, "build_default_judge_client", lambda: fake)
    monkeypatch.setattr(runner_mod, "_changed_files", list)

    run = _new_run("merge_queue")
    runner_mod._run_merge_queue_tier(run)

    assert run.metric_scores
    # Feature 010: `_score_routing` (deterministic, no judge) intentionally
    # runs each case ONCE regardless of tier — repeating a pure function
    # three times produces zero additional variance information, unlike
    # the judge-scored retrieval metrics this assertion was written for.
    retrieval_scores = [s for s in run.metric_scores if s.metric_name not in _ROUTING_METRIC_NAMES]
    assert retrieval_scores
    for score in retrieval_scores:
        assert len(score.raw_values) == 3
        assert score.variance_adjusted is not None
    routing_scores = [s for s in run.metric_scores if s.metric_name in _ROUTING_METRIC_NAMES]
    for score in routing_scores:
        assert len(score.raw_values) == 1


def test_judge_unavailable_marks_not_evaluated_not_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    class _AlwaysUnavailable:
        def complete(self, *, system: str, user: str, prompt) -> str:
            raise JudgeUnavailableError("no provider configured")

    monkeypatch.setattr(runner_mod, "build_default_judge_client", lambda: _AlwaysUnavailable())
    monkeypatch.setattr(runner_mod, "_changed_files", list)
    # keep the retry loop fast in the test
    monkeypatch.setattr(runner_mod.time, "sleep", lambda _s: None)

    run = _new_run("pr")
    runner_mod._run_pr_tier(run)

    assert run.not_evaluated_cases, "a permanently unavailable judge must mark cases NOT_EVALUATED"
    # Feature 010: `_score_routing` is independent of the judge (it scores
    # deterministic tiers 0/1 with no external call for `requires_tier2:
    # false` cases) — a judge outage correctly does not silence it. Only
    # judge-dependent (retrieval) scores must be absent.
    assert not [s for s in run.metric_scores if s.metric_name not in _ROUTING_METRIC_NAMES]


def test_retry_recovers_after_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    class _FlakyOnceThenFake:
        def complete(self, *, system: str, user: str, prompt) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise JudgeUnavailableError("transient")
            return '{"score": 0.8, "reasoning": "ok"}'

    monkeypatch.setattr(runner_mod, "build_default_judge_client", lambda: _FlakyOnceThenFake())
    monkeypatch.setattr(runner_mod.time, "sleep", lambda _s: None)

    result = runner_mod._run_with_retry(lambda: _FlakyOnceThenFake().complete(system="s", user="u", prompt=None))
    assert result in ('{"score": 0.8, "reasoning": "ok"}',)


def test_is_deliberate_fault_response_true_for_envelope() -> None:
    class _Resp:
        def json(self):
            return {"error": {"code": "UPSTREAM_TIMEOUT", "message": "simulated timeout"}}

    class _Exc(Exception):
        response = _Resp()

    assert runner_mod._is_deliberate_fault_response(_Exc())


def test_is_deliberate_fault_response_false_for_transport_error() -> None:
    class _Exc(Exception):
        pass

    assert not runner_mod._is_deliberate_fault_response(_Exc())


def test_touched_domains_from_agents_paths() -> None:
    domains = runner_mod._touched_domains(
        ["agents/dining/graph.py", "agents/stay/nodes/intent_parser.py", "docs/adr/foo.md"]
    )
    assert domains == {"dining", "stay"}
