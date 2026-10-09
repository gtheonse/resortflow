"""T015 -- the prompt template schema (contracts/prompt-template-schema.md).

A template is one YAML file under `prompts/templates/`; Git is the canonical source of every
prompt (ADR-052 D1). This module owns the *shape* checks that must hold for any template, from
any source:

* V1  unknown top-level key, or a `config` key outside `{temperature, response_format}` -> reject.
      `model` / `model_tier` are deliberately NOT allowed: runtime tier is static per node and
      declared in code (Principle VI, spec amendment S-1), so a prompt or a registry editor can
      never demote or swap the model.
* V7  `resolution: exact` is only for judge families.
* ids are snake_case with no `/` and equal the filename stem; `owner` must be a key in
  `prompts/owners.yaml`.

Placeholder-level checks (undeclared / missing / unrendered) live in `prompts.compiler`; the
tenant-variable check (V8) lives in `prompts.tenant` beside the closed tenant model.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

PROMPTS_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PROMPTS_DIR / "templates"
OWNERS_PATH = PROMPTS_DIR / "owners.yaml"

ALLOWED_CONFIG_KEYS: frozenset[str] = frozenset({"temperature", "response_format"})
JUDGE_FAMILIES: frozenset[str] = frozenset({"eval_retrieval_judge", "eval_trajectory_judge"})

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
"""`{{name}}` with optional inner spaces. Single braces are literal (JSON examples in prompt text
are safe). The ONLY placeholder syntax in this package (research R-04)."""

_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class TemplateError(ValueError):
    """A template file is malformed or violates the schema."""


def placeholders_in(text: str) -> list[str]:
    """Placeholder names referenced by `text`, in order of appearance (duplicates kept)."""
    return PLACEHOLDER_RE.findall(text)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: Literal["system", "user"]
    content: str


class TemplateVariables(BaseModel):
    """`runtime`: data the call site supplies (guest text, retrieved context, ...).
    `tenant`: `TenantProfile` field names. Code-mandated *required* variables (the dietary
    vocabularies) are NOT listed here: they are declared at the call site in code (FR-007), so a
    template edit cannot remove the obligation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    runtime: list[str] = Field(default_factory=list)
    tenant: list[str] = Field(default_factory=list)


class PromptTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    family: Literal["dining", "eval_retrieval_judge", "eval_trajectory_judge", "routing"]
    """Feature 010 adds `"routing"` (the `intent_routing_classification` prompt) — additive,
    per AGENTS.md's contract-stability standard: no existing family value is removed or
    redefined."""
    type: Literal["chat"]
    resolution: Literal["label", "exact"]
    owner: str
    config: dict[str, Any] = Field(default_factory=dict)
    variables: TemplateVariables = Field(default_factory=TemplateVariables)
    messages: list[Message]

    @field_validator("id")
    @classmethod
    def _id_is_snake_case(cls, value: str) -> str:
        if not _ID_RE.fullmatch(value):
            raise ValueError(f"id {value!r} must be snake_case (no '/', no spaces, starts with a letter)")
        return value

    @field_validator("config")
    @classmethod
    def _config_keys_and_shapes(cls, value: dict[str, Any]) -> dict[str, Any]:
        return validate_config(value)

    @field_validator("messages")
    @classmethod
    def _at_least_one_message(cls, value: list[Message]) -> list[Message]:
        if not value:
            raise ValueError("a template needs at least one message")
        return value

    @model_validator(mode="after")
    def _exact_only_for_judge_families(self) -> PromptTemplate:
        if self.resolution == "exact" and self.family not in JUDGE_FAMILIES:
            raise ValueError("resolution 'exact' is only for judge families (V7)")
        return self


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    """The config allow-list (V1). Shared with the registry, which applies it to registry-sourced
    config too (`bad_config`). Raises `ValueError`."""
    extra = sorted(set(config) - ALLOWED_CONFIG_KEYS)
    if extra:
        raise ValueError(f"config keys outside {sorted(ALLOWED_CONFIG_KEYS)}: {extra}")
    if "temperature" in config:
        temperature = config["temperature"]
        if isinstance(temperature, bool) or not isinstance(temperature, int | float):
            raise ValueError("config.temperature must be a number")
    if "response_format" in config and config["response_format"] != {"type": "json_object"}:
        raise ValueError("config.response_format must be {'type': 'json_object'}")
    return config


def load_owners(path: Path = OWNERS_PATH) -> dict[str, Any]:
    """`owners.yaml` -> `{group_key: {team_slug, scope, members}}`."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, dict):
        raise TemplateError(f"{path}: expected a top-level 'groups' mapping")
    return groups


def load_template(path: Path, *, owners: dict[str, Any] | None = None) -> PromptTemplate:
    """Load and validate one template file. `owners=None` reads `prompts/owners.yaml`."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TemplateError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise TemplateError(f"{path.name}: a template must be a YAML mapping")
    try:
        template = PromptTemplate.model_validate(data)
    except ValidationError as exc:
        raise TemplateError(f"{path.name}: {exc}") from exc
    if template.id != path.stem:
        raise TemplateError(f"{path.name}: id {template.id!r} must equal the filename stem {path.stem!r}")
    known = owners if owners is not None else load_owners()
    if template.owner not in known:
        raise TemplateError(f"{path.name}: owner {template.owner!r} is not a key in owners.yaml")
    return template


def load_all_templates(
    directory: Path = TEMPLATES_DIR, *, owners: dict[str, Any] | None = None
) -> dict[str, PromptTemplate]:
    """Every `*.yaml` in `directory`, keyed by template id."""
    known = owners if owners is not None else load_owners()
    return {
        template.id: template
        for template in (load_template(path, owners=known) for path in sorted(directory.glob("*.yaml")))
    }
