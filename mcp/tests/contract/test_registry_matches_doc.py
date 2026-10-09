"""T035 [US1] — registry-vs-contract test (F4).

Parses `specs/003-mcp-tool-layer/contracts/tool-registry.md`'s per-domain
tables and asserts the live registry matches it row for row: same tool
names, same entitlement action, same idempotency-key requirement, same
composite flag, and the same roster totals table. This is what keeps the doc
from drifting from the code a second time.

Per the task brief: if the sibling doc-drift correction (entertainment's row
count) has not landed in `tool-registry.md` yet when this runs, the mismatch
is a REAL, correct failure — this file must not special-case it away. Only
report it; do not adjust the assertion to paper over a stale doc.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from ..fixtures.wire import DOMAINS

CONTRACT_PATH = (
    pathlib.Path(__file__).parents[3]
    / "specs" / "003-mcp-tool-layer" / "contracts" / "tool-registry.md"
)

# One row: | `resortflow.dining.list_venues` | read | no | yes | ... |
ROW_RE = re.compile(
    r"^\|\s*`(resortflow\.[a-z_]+\.[a-z_]+)`\s*\|\s*(read|hold|commit)\s*\|\s*(yes|no)\s*\|\s*(yes|no)\s*\|"
)

# `## `dining` → sims/dining (8 tools)` style section headers.
SECTION_RE = re.compile(r"^##\s*`(\w+)`\s*→")


def parse_contract_rows() -> dict[str, dict[str, dict[str, str]]]:
    """Returns {domain: {tool_name: {"ent": ..., "idem": ..., "comp": ...}}}."""
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    domains: dict[str, dict[str, dict[str, str]]] = {}
    current_domain: str | None = None
    for line in text.splitlines():
        section_match = SECTION_RE.match(line)
        if section_match:
            current_domain = section_match.group(1)
            domains.setdefault(current_domain, {})
            continue
        row_match = ROW_RE.match(line)
        if row_match and current_domain:
            tool_name, ent, idem, comp = row_match.groups()
            domains[current_domain][tool_name] = {"ent": ent, "idem": idem, "comp": comp}
    return domains


def parse_roster_totals() -> dict[str, int]:
    """Parses the "## Roster totals" table's per-domain tool-count column."""
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    totals: dict[str, int] = {}
    in_totals = False
    for line in text.splitlines():
        if line.strip().startswith("## Roster totals"):
            in_totals = True
            continue
        if in_totals and line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 2 and cells[0] not in ("Domain", "**Total**", "---") and not set(cells[0]) <= {"-"}:
                if cells[0] == "**Total**":
                    continue
                try:
                    totals[cells[0]] = int(cells[1])
                except ValueError:
                    continue
        if in_totals and line.strip() == "---":
            break
    return totals


@pytest.mark.skipif(not CONTRACT_PATH.exists(), reason="tool-registry.md not found")
def test_contract_doc_is_parseable():
    rows = parse_contract_rows()
    assert rows, "no domain sections parsed from tool-registry.md — regex or doc structure drifted"
    for domain in ("dining", "entertainment", "stay", "wellness", "gaming"):
        assert domain in rows, f"contract doc has no section for {domain}"
        assert rows[domain], f"contract doc section for {domain} has no tool rows"


@pytest.mark.asyncio
@pytest.mark.parametrize("domain_name", sorted(DOMAINS))
async def test_live_registry_matches_contract_doc_rows(domain_name: str):
    """Row-for-row comparison: tool names, entitlement, idempotency, composite flag."""
    contract_rows = parse_contract_rows().get(domain_name, {})
    info = DOMAINS[domain_name]

    module_tools = [
        obj
        for obj in vars(info.module).values()
        if callable(obj) and hasattr(obj, "full_name") and getattr(obj, "domain", None) == domain_name
    ]
    live_by_name = {t.full_name: t for t in module_tools}

    assert set(live_by_name) == set(contract_rows), (
        f"{domain_name}: live registry tool names do not match tool-registry.md.\n"
        f"  live only: {sorted(set(live_by_name) - set(contract_rows))}\n"
        f"  doc only:  {sorted(set(contract_rows) - set(live_by_name))}"
    )

    for tool_name, doc_row in contract_rows.items():
        live_tool = live_by_name[tool_name]
        assert live_tool.entitlement_action == doc_row["ent"], (
            f"{tool_name}: live entitlement {live_tool.entitlement_action!r} != "
            f"doc {doc_row['ent']!r}"
        )
        live_is_composite = bool(live_tool.sub_calls)
        doc_is_composite = doc_row["comp"] == "yes"
        assert live_is_composite == doc_is_composite, (
            f"{tool_name}: live composite={live_is_composite} != doc composite={doc_is_composite}"
        )
        live_requires_idem = live_tool.is_mutating
        doc_requires_idem = doc_row["idem"] == "yes"
        assert live_requires_idem == doc_requires_idem, (
            f"{tool_name}: live idempotency-required={live_requires_idem} != "
            f"doc idempotency={doc_requires_idem}"
        )


@pytest.mark.asyncio
async def test_roster_totals_match_live_counts():
    """The "Roster totals" table's per-domain tool count matches the live registry."""
    totals = parse_roster_totals()
    for domain_name, info in DOMAINS.items():
        module_tools = [
            obj
            for obj in vars(info.module).values()
            if callable(obj) and hasattr(obj, "full_name") and getattr(obj, "domain", None) == domain_name
        ]
        assert domain_name in totals, f"roster totals table missing a row for {domain_name}"
        assert totals[domain_name] == len(module_tools), (
            f"{domain_name}: roster totals table says {totals[domain_name]} tools, "
            f"live registry has {len(module_tools)}"
        )
