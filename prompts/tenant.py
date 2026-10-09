"""T018 -- the closed, typed tenant model (FR-012a..FR-012d, V8; Principle IV, tenant isolation).

Tenant-specific content may enter a template ONLY through `TenantProfile`: a closed set of bounded,
validated fields populated server-side from the token-derived tenant -- never from model output, never
from registry or CMS free text, and never as a free-form paragraph. Anything richer (brand voice, house
notes) would be a per-tenant template under another name, with no hash, no lock entry and no gate; the
accepted trade-off (ADR-052 D10) is that brand voice cannot live in prompt text.

Adding a field here is a code change plus a per-template contract test (FR-012c), which is what makes it a
reviewed, visible change. Today no in-scope template uses any tenant variable.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, field_validator

if TYPE_CHECKING:
    from prompts.schema import PromptTemplate

TENANT_FIELD_NAMES: frozenset[str] = frozenset({"display_name", "venue_names"})
"""The `TenantProfile` fields a template may reference as `variables.tenant`."""

_MAX_LEN = 64
_MAX_VENUES = 12
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()\-]*$")


def _validate_short_label(value: str, field: str) -> str:
    if not value or len(value) > _MAX_LEN:
        raise ValueError(f"{field} must be 1..{_MAX_LEN} characters")
    if not _SAFE_TEXT.fullmatch(value):
        raise ValueError(f"{field} contains characters outside the safe label set (no newlines or markup)")
    return value


class TenantProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: Literal["MGV", "MHB"]
    display_name: str
    venue_names: list[str]

    _TABLE: ClassVar[dict[str, tuple[str, list[str]]]] = {
        "MGV": ("Meridian Grand", ["Meridian Steakhouse"]),
        "MHB": ("Meridian Harbor", []),
    }

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        return _validate_short_label(value, "display_name")

    @field_validator("venue_names")
    @classmethod
    def _venue_names(cls, value: list[str]) -> list[str]:
        if len(value) > _MAX_VENUES:
            raise ValueError(f"at most {_MAX_VENUES} venue names")
        return [_validate_short_label(v, "venue_names[]") for v in value]

    @classmethod
    def from_tenant_id(cls, tenant_id: str) -> TenantProfile:
        """Static, server-side table keyed by the token-derived tenant id."""
        if tenant_id not in cls._TABLE:
            raise ValueError(f"unknown tenant {tenant_id!r}")
        display_name, venues = cls._TABLE[tenant_id]
        return cls(tenant_id=tenant_id, display_name=display_name, venue_names=list(venues))  # type: ignore[arg-type]

    def as_variables(self) -> dict[str, str]:
        """Plain-string template variables for the declared fields only."""
        return {"display_name": self.display_name, "venue_names": ", ".join(self.venue_names)}


def check_template_tenant_vars(template: PromptTemplate) -> list[str]:
    """V8: names in `variables.tenant` that are not `TenantProfile` fields (empty list = clean)."""
    return [name for name in template.variables.tenant if name not in TENANT_FIELD_NAMES]
