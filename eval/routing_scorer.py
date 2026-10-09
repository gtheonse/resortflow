"""Feature 010 — deterministic routing metrics (T068,
`specs/010-tiered-intent-routing/contracts/routing-eval-gate.md`).

Runs `graph.routing.router.route()` directly against
`eval/data/routing_cases.yaml` — no graph, no tools, no retrieval — and
emits tenant-stratified `MetricScore`s per case. `requires_tier2` cases
need Azure credentials; without them they are reported as not-evaluated,
never scored with a fabricated result (mirrors `eval/intent_scorer.py`'s
own `IntentExtractorUnavailableError` handling).

**Blocking is computed on the `holdout` split only** — `dev` is reported
alongside for diagnosis (analyze finding D1: the case set was authored
before, and independently of, the vocabulary it measures)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from eval.models import JudgeModelRecord, MetricScore
from graph.models import BrowseFilters, Domain, RequestKind, RoutingTier
from graph.routing.classifier import (
    RoutingClassifier,
    UnavailableClassifier,
    build_default_classifier,
)
from graph.routing.kinds import PreviousTurn
from graph.routing.router import route
from graph.routing.vocabulary import DEFAULT as DEFAULT_VOCAB
from graph.routing.vocabulary import Vocabulary

_CASES_PATH = Path(__file__).parent / "data" / "routing_cases.yaml"


class RoutingCredentialsUnavailableError(RuntimeError):
    """No Azure OpenAI credentials configured — a `requires_tier2` case
    cannot be scored without fabricating a result."""


def _judge_model_record() -> JudgeModelRecord:
    return JudgeModelRecord(provider="", model_id="")


def load_routing_cases(path: Path = _CASES_PATH) -> list[dict[str, Any]]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = data["cases"]
    return cases


def default_routing_classifier() -> RoutingClassifier:
    """Raises when a `requires_tier2` case has no credentials to score
    against — the caller turns this into `not_evaluated_cases`, mirroring
    `eval.intent_scorer.default_intent_extractor`'s own philosophy."""
    import os

    if not (os.environ.get("AZURE_OPENAI_ENDPOINT") and os.environ.get("AZURE_OPENAI_API_KEY")):
        raise RoutingCredentialsUnavailableError(
            "no Azure OpenAI credentials configured — set AZURE_OPENAI_ENDPOINT+AZURE_OPENAI_API_KEY"
        )
    return build_default_classifier()


def _services(names: list[str]) -> list[Domain]:
    return [Domain(n) for n in names]


def _previous_from_case(case: dict[str, Any]) -> PreviousTurn | None:
    previous = case.get("previous")
    if previous is None:
        return None
    return PreviousTurn(
        kind=RequestKind(previous["kind"]),
        services=_services(previous.get("services", [])),
        had_booking=bool(previous.get("had_booking", False)),
        # Feature 011: a browse `previous` may also carry its filters/window (a refinement case).
        filters=BrowseFilters(**previous["filters"]) if previous.get("filters") else None,
    )


async def score_routing_case(
    case: dict[str, Any], *, classifier: RoutingClassifier, vocab: Vocabulary = DEFAULT_VOCAB
) -> list[MetricScore]:
    """Scores one case against every metric it contributes to
    (`contracts/routing-eval-gate.md`). A `requires_tier2` case with
    `UnavailableClassifier` still scores — the caller decides whether to
    use `UnavailableClassifier` (skip) or a real classifier (score) for
    such cases; this function only runs `route()` once and derives every
    metric from the one decision, so a case's tier-2 need is a property of
    which classifier the CALLER passed in, not of this function."""
    text = case["text"]
    tenant_id = case["tenant_id"]
    conversation_id = case["id"]
    category = case["category"]
    judge_model = _judge_model_record()
    previous = _previous_from_case(case)

    decision = await route(text, previous=previous, classifier=classifier, vocab=vocab)

    expected_kind = case["expected_kind"]
    actual_kind = decision.kind.value if decision.kind is not None else "undecided"
    expected_services = sorted(case.get("expected_services", []))
    actual_services = sorted(s.value for s in decision.services)
    is_correct = actual_kind == expected_kind and actual_services == expected_services

    scores: list[MetricScore] = []

    def _score(metric_name: str, value: float) -> MetricScore:
        return MetricScore(
            metric_name=metric_name, domain=category, conversation_id=conversation_id,
            gate_type="statistical", raw_values=[value], judge_model=judge_model, tenant_id=tenant_id,
        )

    scores.append(
        MetricScore(
            metric_name="routing_accuracy", domain=category, conversation_id=conversation_id,
            gate_type="statistical", raw_values=[1.0 if is_correct else 0.0],
            judge_model=judge_model, tenant_id=tenant_id,
        )
    )
    scores.append(
        MetricScore(
            metric_name="llm_tier_share", domain=category, conversation_id=conversation_id,
            gate_type="statistical", raw_values=[1.0 if decision.tier == RoutingTier.LANGUAGE_MODEL else 0.0],
            judge_model=judge_model, tenant_id=tenant_id,
        )
    )
    if category == "out_of_scope":
        passed_through = actual_kind in ("book", "faq")
        scores.append(
            MetricScore(
                metric_name="oos_passthrough_rate", domain=category, conversation_id=conversation_id,
                gate_type="statistical", raw_values=[1.0 if passed_through else 0.0],
                judge_model=judge_model, tenant_id=tenant_id,
            )
        )
    if category == "substring_trap":
        scores.append(
            MetricScore(
                metric_name="substring_trap_misroutes", domain=category, conversation_id=conversation_id,
                gate_type="statistical", raw_values=[0.0 if is_correct else 1.0],
                judge_model=judge_model, tenant_id=tenant_id,
            )
        )
    if category == "modify":
        scores.append(
            MetricScore(
                metric_name="modify_book_starts", domain=category, conversation_id=conversation_id,
                gate_type="statistical", raw_values=[1.0 if actual_kind == "book" else 0.0],
                judge_model=judge_model, tenant_id=tenant_id,
            )
        )
    # Feature 011 (contracts/browse-eval-gate.md): browse metrics, tenant-stratified like every routing metric.
    if category.startswith("browse_"):
        scores.append(_score("browse_accuracy", 1.0 if is_correct else 0.0))
        scores.append(_score("browse_catalogue_misroute", 1.0 if actual_kind in ("faq", "out_of_scope") else 0.0))
        scores.append(
            _score("browse_llm_tier_share", 1.0 if decision.tier == RoutingTier.LANGUAGE_MODEL else 0.0)
        )
    if category.startswith("near_miss_"):
        scores.append(_score("near_miss_browse_leak", 1.0 if actual_kind == "browse" else 0.0))
    if category == "stay_length":
        scores.append(_score("stay_length_accuracy", 1.0 if is_correct else 0.0))
    if category == "followup":
        scores.append(
            MetricScore(
                metric_name="followup_accuracy", domain=category, conversation_id=conversation_id,
                gate_type="statistical", raw_values=[1.0 if is_correct else 0.0],
                judge_model=judge_model, tenant_id=tenant_id,
            )
        )
    return scores


async def score_routing_cases(
    cases: list[dict[str, Any]], *, split: str | None = "holdout", vocab: Vocabulary = DEFAULT_VOCAB
) -> tuple[list[MetricScore], list[str]]:
    """Scores every case whose `split` matches (`None` scores all splits —
    used for the `dev`-diagnostic report). Returns `(scores,
    not_evaluated_case_ids)`; a `requires_tier2` case without credentials
    lands in the second list, never scored with a fabricated pass."""
    unavailable = UnavailableClassifier()
    try:
        live_classifier: RoutingClassifier | None = default_routing_classifier()
    except RoutingCredentialsUnavailableError:
        live_classifier = None

    all_scores: list[MetricScore] = []
    not_evaluated: list[str] = []
    for case in cases:
        if split is not None and case.get("split") != split:
            continue
        if case.get("requires_tier2"):
            if live_classifier is None:
                not_evaluated.append(case["id"])
                continue
            classifier: RoutingClassifier = live_classifier
        else:
            classifier = unavailable
        all_scores.extend(await score_routing_case(case, classifier=classifier, vocab=vocab))
    return all_scores, not_evaluated
