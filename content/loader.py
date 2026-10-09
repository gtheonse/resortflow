"""T036 — Content corpus loader.

Loads and lightly validates the per-tenant, per-domain YAML corpus under
``content/corpus/<tenant_id>/<domain>.yaml``. This module does no chunking
and produces no ``graph.models.Chunk`` instances — it hands back the raw
parsed YAML structure (plus a defensive top-level shape check) for
``content/chunker.py`` to turn into chunks.

Kept intentionally dumb: the corpus schema is loose YAML on purpose (it
stands in for a headless CMS API response), so validation here is limited
to "does this file have the shape the chunker expects", not full schema
enforcement of every domain's item fields.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CORPUS_ROOT = Path(__file__).parent / "corpus"

KNOWN_TENANTS: tuple[str, ...] = ("mgv", "mhb")
KNOWN_DOMAINS: tuple[str, ...] = (
    "dining",
    "rooms",
    "wellness",
    "entertainment",
    "policies",
    "faq",
)


class CorpusError(ValueError):
    """Raised when a corpus file is missing or malformed."""


def _tenant_dir(tenant_id: str) -> Path:
    return CORPUS_ROOT / tenant_id.lower()


def load_domain(tenant_id: str, domain: str) -> dict[str, Any]:
    """Load a single domain YAML file for a tenant.

    Raises ``CorpusError`` if the file is missing, empty, isn't a mapping,
    or its declared ``tenant_id``/``domain`` fields don't match the
    requested ones (a corpus authoring mistake, e.g. a copy-pasted file
    left with the wrong tenant_id).
    """
    path = _tenant_dir(tenant_id) / f"{domain}.yaml"
    if not path.exists():
        raise CorpusError(f"missing corpus file: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise CorpusError(f"{path}: expected a YAML mapping at top level")

    declared_tenant = data.get("tenant_id")
    if declared_tenant is None:
        raise CorpusError(f"{path}: missing 'tenant_id' field")
    if declared_tenant.upper() != tenant_id.upper():
        raise CorpusError(
            f"{path}: declared tenant_id={declared_tenant!r} does not match "
            f"requested tenant_id={tenant_id!r}"
        )

    declared_domain = data.get("domain")
    if declared_domain != domain:
        raise CorpusError(
            f"{path}: declared domain={declared_domain!r} does not match "
            f"requested domain={domain!r}"
        )

    return data


def load_corpus(tenant_id: str) -> dict[str, dict[str, Any]]:
    """Load every known domain file for a tenant.

    Returns ``{domain: raw_dict}``. Raises ``CorpusError`` if any of the six
    domain files is missing or malformed for this tenant — a partial corpus
    is treated as a build error, not a soft-skip, so a missing file is
    caught at load time rather than surfacing later as silently-thin
    retrieval.
    """
    return {domain: load_domain(tenant_id, domain) for domain in KNOWN_DOMAINS}


def load_all_corpora() -> dict[str, dict[str, dict[str, Any]]]:
    """Load every domain for every known tenant. Convenience for indexing."""
    return {tenant_id: load_corpus(tenant_id) for tenant_id in KNOWN_TENANTS}
