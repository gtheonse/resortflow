"""T031 -- the template lint (FR-028e, FR-012d; Principle IV).

Policy must not live in prompt text. A template may not contain:

* a literal dietary restriction or severity value (`gluten`, `allergy_severe`, ...): that vocabulary is injected
  from the code-defined enumerations, so wording that names it could drift from -- or quietly override -- the
  structured policy. The token list is DERIVED from `graph.models` at check time, never hard-coded;
* a tenant identifier (`MGV`, `MHB`): one tenant-agnostic template per prompt (FR-012a);
* a jurisdiction or regulator name (`Nevada`, `Massachusetts`, `NGCB`, `MGC`): jurisdiction wording is fixed text
  appended by code after generation (ADR-034), never asked of a model.

Matching is word-bounded and case-insensitive, and placeholders are ignored. Some dietary values are ordinary
English words (`preference`, `egg`, `fish`); a collision is handled ONLY by an explicit, reviewed exemption
scoped per token and per prompt with a non-empty reason (`prompts/lint_exemptions.yaml`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from prompts.schema import PLACEHOLDER_RE, PROMPTS_DIR, PromptTemplate

EXEMPTIONS_PATH = PROMPTS_DIR / "lint_exemptions.yaml"

TENANT_IDENTIFIERS: tuple[str, ...] = ("MGV", "MHB")
JURISDICTION_NAMES: tuple[str, ...] = ("Nevada", "Massachusetts", "NGCB", "MGC")

Kind = Literal["dietary", "tenant", "jurisdiction"]


class LintConfigError(ValueError):
    """The exemptions file is malformed (for example an exemption without a reason)."""


@dataclass(frozen=True)
class LintFinding:
    prompt_id: str
    token: str
    kind: Kind

    def __str__(self) -> str:
        return f"{self.prompt_id}: literal {self.kind} token {self.token!r} in template text"


@dataclass(frozen=True)
class Exemption:
    prompt: str
    token: str
    reason: str


def dietary_tokens() -> list[str]:
    """Every `DietaryRestriction` and `DietarySeverity` value, read from the live enumerations."""
    from graph.models import DietaryRestriction, DietarySeverity

    return [m.value for m in DietaryRestriction] + [m.value for m in DietarySeverity]


def _pattern(token: str) -> re.Pattern[str]:
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(token) + r"(?![A-Za-z0-9_])", re.IGNORECASE)


def _text_of(template: PromptTemplate) -> str:
    joined = "\n".join(message.content for message in template.messages)
    return PLACEHOLDER_RE.sub(" ", joined)


def lint_template(template: PromptTemplate, exemptions: Iterable[Exemption]) -> list[LintFinding]:
    exempt = {(e.prompt, e.token.lower()) for e in exemptions}
    text = _text_of(template)
    findings: list[LintFinding] = []
    groups: tuple[tuple[Kind, Iterable[str]], ...] = (
        ("dietary", dietary_tokens()),
        ("tenant", TENANT_IDENTIFIERS),
        ("jurisdiction", JURISDICTION_NAMES),
    )
    for kind, tokens in groups:
        for token in tokens:
            if (template.id, token.lower()) in exempt:
                continue
            if _pattern(token).search(text):
                findings.append(LintFinding(prompt_id=template.id, token=token, kind=kind))
    return findings


def lint_all(templates: Mapping[str, PromptTemplate], exemptions: Iterable[Exemption]) -> list[LintFinding]:
    listed = list(exemptions)
    return [finding for template in templates.values() for finding in lint_template(template, listed)]


def load_exemptions(path: Path = EXEMPTIONS_PATH) -> list[Exemption]:
    """A missing file means no exemptions. Every exemption needs a prompt, a token and a non-empty reason."""
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    items = data.get("exemptions", []) if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise LintConfigError(f"{path.name}: expected an 'exemptions' list")
    out: list[Exemption] = []
    for item in items:
        if not isinstance(item, dict):
            raise LintConfigError(f"{path.name}: each exemption must be a mapping")
        prompt, token, reason = item.get("prompt"), item.get("token"), item.get("reason")
        if not (isinstance(prompt, str) and prompt.strip() and isinstance(token, str) and token.strip()):
            raise LintConfigError(f"{path.name}: an exemption needs a 'prompt' and a 'token'")
        if not (isinstance(reason, str) and reason.strip()):
            raise LintConfigError(f"{path.name}: exemption {prompt}/{token} needs a non-empty reason")
        out.append(Exemption(prompt=prompt.strip(), token=token.strip().lower(), reason=reason.strip()))
    return out
