"""T013/T014 — coverage-requirement detection (FR-009a, ADR-040) and the
Coverage Exemption lifecycle (FR-025-029, ADR-042's clarify session,
`contracts/coverage-exemption-schema.md`).

Detection is git-diff heuristics tailored per capability type — no new
central registry (research.md R2/ADR-040): a decorator/regex scan for tool
entitlements, a path glob for tenant corpora, and coarse file-path
heuristics for saga/compensation and policy rules (deliberately coarser than
function-level — see ADR-040 for why that trade was accepted).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import yaml

UrgencyTier = Literal["tier_1", "tier_2", "other"]

_EXEMPTIONS_PATH = Path(__file__).parent / "data" / "exemptions.yaml"

_TENANT_CORPUS_RE = re.compile(r"^content/corpus/([^/]+)/[^/]+\.ya?ml$")
_POLICY_RULE_RE = re.compile(r"^policy/(gaming|dietary|injection|distress|jurisdiction)\.py$")
_REGISTER_TOOL_RE = re.compile(
    r'^\+.*@register_tool\([^)]*entitlement_action=["\'](commit|hold|read)["\']', re.MULTILINE
)
_DEF_RE = re.compile(r"^\+\s*(?:async\s+)?def\s+(\w+)\(", re.MULTILINE)

_EXPIRY_WINDOW_DAYS: dict[UrgencyTier, int] = {"tier_1": 7, "tier_2": 14, "other": 30}


@dataclass
class CoverageRequirement:
    surface_id: str
    urgency_tier: UrgencyTier
    blocking: bool = True
    """`False` for read-only-tool / config-only changes, which only warn
    (FR-009a)."""


def _tool_entitlement_requirements(diff_text: str) -> list[CoverageRequirement]:
    """Decorator/regex scan of the diff for `@register_tool(...,
    entitlement_action="commit"|"hold"|"read")`, per ADR-040 — this repo's
    tools declare entitlements consistently enough (one decorator, one
    keyword) for this to be reliable without a new registry."""
    requirements: list[CoverageRequirement] = []
    for match in _REGISTER_TOOL_RE.finditer(diff_text):
        action = match.group(1)
        if action == "read":
            continue  # read-only tools only warn (FR-009a) — not a coverage requirement
        tail = diff_text[match.end() : match.end() + 400]
        name_match = _DEF_RE.search(tail)
        surface_id = f"tool.{name_match.group(1)}" if name_match else "tool.unknown"
        tier: UrgencyTier = "tier_1" if action == "commit" else "tier_2"
        requirements.append(CoverageRequirement(surface_id=surface_id, urgency_tier=tier))
    return requirements


def _tenant_corpus_requirements(changed_files: list[str]) -> list[CoverageRequirement]:
    requirements = []
    for path in changed_files:
        match = _TENANT_CORPUS_RE.match(path.replace("\\", "/"))
        if match:
            requirements.append(
                CoverageRequirement(surface_id=f"corpus.{path}", urgency_tier="tier_2")
            )
    return requirements


def _is_saga_surface(path: str) -> bool:
    """The saga engine and everything that plugs into it (ADR-040, ADR-061): the
    supervisor package, the participant abstraction/registry, and each domain's
    own `participant.py` adapter."""
    return (
        path.startswith("graph/supervisor/")
        or path in {"graph/participants.py", "graph/domains.py"}
        or (path.startswith("agents/") and path.endswith("/participant.py"))
    )


def _saga_participant_requirements(changed_files: list[str]) -> list[CoverageRequirement]:
    """Coarse, file-granularity heuristic (ADR-040): any change to the saga
    surface is treated as touching a saga participant or compensation path.
    Before ADR-061 this was the single file `graph/supervisor.py`; the surface is
    now the `graph/supervisor/` package plus the participants it delegates to."""
    normalized = {p.replace("\\", "/") for p in changed_files}
    if any(_is_saga_surface(p) for p in normalized):
        return [CoverageRequirement(surface_id="graph.supervisor.saga", urgency_tier="tier_1")]
    return []


def _policy_rule_requirements(changed_files: list[str]) -> list[CoverageRequirement]:
    requirements = []
    for path in changed_files:
        normalized = path.replace("\\", "/")
        if "/tests/" in normalized:
            continue
        match = _POLICY_RULE_RE.match(normalized)
        if match:
            requirements.append(
                CoverageRequirement(surface_id=f"policy.{match.group(1)}", urgency_tier="tier_2")
            )
    return requirements


def detect_coverage_requirements(
    changed_files: list[str], diff_text: str = ""
) -> list[CoverageRequirement]:
    """FR-009a: the full set of coverage requirements a change introduces,
    derived automatically from what it touches — never a maintained list."""
    return [
        *_tool_entitlement_requirements(diff_text),
        *_tenant_corpus_requirements(changed_files),
        *_saga_participant_requirements(changed_files),
        *_policy_rule_requirements(changed_files),
    ]


# --- Coverage Exemption lifecycle (FR-025-029) ------------------------------


@dataclass
class CoverageExemption:
    exemption_id: str
    surface_id: str
    urgency_tier: UrgencyTier
    reason: str
    tracking_issue: str
    created_at: datetime
    expires_at: datetime
    status: Literal["active", "cleared", "void"] = "active"
    request_sequence: int = 1
    adr_ref: str | None = None

    def effective_status(self, *, now: datetime | None = None) -> str:
        """FR-027: an `active` entry past `expires_at` is `void` at READ
        time regardless of the stored value — the runner never trusts a
        stale `active` left in the file."""
        now = now or datetime.now(UTC)
        if self.status == "active" and now >= self.expires_at:
            return "void"
        return self.status


def new_exemption(
    *,
    surface_id: str,
    urgency_tier: UrgencyTier,
    reason: str,
    tracking_issue: str,
    request_sequence: int = 1,
    adr_ref: str | None = None,
    now: datetime | None = None,
) -> CoverageExemption:
    """FR-029: a 3rd-or-later request against the same surface requires a
    non-null `adr_ref` — enforced here, at construction, not left to the
    caller to remember."""
    if request_sequence >= 3 and not adr_ref:
        raise ValueError(
            f"3rd-or-later exemption request against {surface_id!r} requires adr_ref "
            "(FR-029) — record a recorded architectural decision or remove the capability"
        )
    now = now or datetime.now(UTC)
    window = timedelta(days=_EXPIRY_WINDOW_DAYS[urgency_tier])
    return CoverageExemption(
        exemption_id=f"{surface_id}-{now.date().isoformat()}",
        surface_id=surface_id,
        urgency_tier=urgency_tier,
        reason=reason,
        tracking_issue=tracking_issue,
        created_at=now,
        expires_at=now + window,
        request_sequence=request_sequence,
        adr_ref=adr_ref,
    )


def load_exemptions(path: Path = _EXEMPTIONS_PATH) -> list[CoverageExemption]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text()) or []
    return [
        CoverageExemption(
            exemption_id=e["exemption_id"],
            surface_id=e["surface_id"],
            urgency_tier=e["urgency_tier"],
            reason=e["reason"],
            tracking_issue=e["tracking_issue"],
            created_at=_parse_dt(e["created_at"]),
            expires_at=_parse_dt(e["expires_at"]),
            status=e.get("status", "active"),
            request_sequence=e.get("request_sequence", 1),
            adr_ref=e.get("adr_ref"),
        )
        for e in raw
    ]


def save_exemptions(exemptions: list[CoverageExemption], path: Path = _EXEMPTIONS_PATH) -> None:
    payload = [
        {
            "exemption_id": e.exemption_id,
            "surface_id": e.surface_id,
            "urgency_tier": e.urgency_tier,
            "reason": e.reason,
            "tracking_issue": e.tracking_issue,
            "created_at": e.created_at.isoformat(),
            "expires_at": e.expires_at.isoformat(),
            "status": e.status,
            "request_sequence": e.request_sequence,
            "adr_ref": e.adr_ref,
        }
        for e in exemptions
    ]
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def clear_exemption_if_covered(
    exemptions: list[CoverageExemption], surface_id: str, *, now: datetime | None = None
) -> list[CoverageExemption]:
    """FR-026: clears early the moment the coverage gate actually passes for
    `surface_id` — never merely because a tracking issue closed (this
    function has no issue-tracker integration at all, by design). An
    already-`void` exemption is not reactivated by this call — FR-028
    requires a fresh exemption, not resurrecting the expired one."""
    for exemption in exemptions:
        if exemption.surface_id == surface_id and exemption.effective_status(now=now) == "active":
            exemption.status = "cleared"
    return exemptions


def evaluate_coverage(
    requirements: list[CoverageRequirement],
    covered_surface_ids: set[str],
    exemptions: list[CoverageExemption],
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[CoverageExemption]]:
    """Returns (coverage report rows, active exemptions) per
    `contracts/eval-report-schema.md`'s `coverage`/`active_exemptions`
    fields."""
    active = [e for e in exemptions if e.effective_status(now=now) == "active"]
    exempted_surfaces = {e.surface_id for e in active}
    rows = [
        {
            "surface_id": r.surface_id,
            "urgency_tier": r.urgency_tier,
            "covered": r.surface_id in covered_surface_ids or r.surface_id in exempted_surfaces,
        }
        for r in requirements
    ]
    return rows, active
