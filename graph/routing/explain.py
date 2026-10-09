"""Feature 010, T042 — `python -m graph.routing.explain "<text>"`: a debug
CLI printing the tiers 0-1 `RoutingDecision` and the vocabulary entry that
matched (quickstart Scenario 1). Deterministic tiers only — never calls
the tier-2 classifier, so it needs no credentials and no network access."""

from __future__ import annotations

import sys

from graph.routing.kinds import classify_kind
from graph.routing.matcher import match_services


def explain(text: str) -> str:
    services = match_services(text)
    decision = classify_kind(text, services)
    lines = [
        f"text:       {text!r}",
        f"kind:       {decision.kind.value if decision.kind else 'undecided'}",
        f"services:   {[s.value for s in decision.services]}",
        f"tier:       {decision.tier.value}",
        f"confidence: {decision.confidence:.2f}",
        f"ambiguous:  {decision.ambiguous}",
    ]
    if decision.secondary_kind is not None:
        lines.append(
            f"secondary:  {decision.secondary_kind.value} {[s.value for s in decision.secondary_services]}"
        )
    if services.excluded:
        lines.append(f"excluded:   {services.excluded}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: python -m graph.routing.explain \"<guest message>\"", file=sys.stderr)
        return 2
    print(explain(" ".join(argv)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
