"""T008 — the hold-ledger reaper (ADR-022, `contracts/hold-ledger-reaper-contract.md`).

Runs independently of the gateway/supervisor process lifecycle: a `PENDING`/`HELD`
`hold_ledger` row whose owning thread crashed before reaching a natural terminal state
would otherwise sit forever, silently holding vendor inventory a guest never sees again.
This process is the only thing that ever reconciles that crash window.

Cadence: every 60 seconds (`ResortFlow-HITL-Durability-State.md` §3, ADR-019). "No active
thread" is approximated by staleness of `updated_at` — the reaper contract explicitly
leaves the precise mechanism (a join against the checkpointer's own thread table vs. a
time-since-`updated_at` heuristic) as an implementation detail, not a schema-level
concern. A real join would need the checkpointer's own thread table shape, which
`graph/ledger.py` deliberately doesn't couple to (checkpointer and ledger are kept
separate per Constitution Principle V) — the heuristic below is the disclosed, simpler
choice: comfortably longer than any real single-turn interaction (a guest holding a table
for `HELD` and returning to approve within minutes), short enough that a genuine crash is
caught well inside a shift.

Crash-window disposition per the contract:
  PENDING, hold_id IS NULL -> no cross-domain "probe by idempotency_key" tool exists in
      any domain's MCP contract today (each domain's own status/lookup tools are keyed by
      hold_id/reservation_id/order_id, never by idempotency_key) — disclosed
      simplification: mark_orphan_released directly, skipping the vendor probe the
      contract describes as the ideal. Revisit if a domain ever adds an
      idempotency-key-keyed status lookup.
  HELD, hold_id present -> release directly via the domain's release_* tool using the
      recorded hold_id, then mark_orphan_released.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

from graph.ledger import HoldLedger, PostgresHoldLedger
from graph.mcp_client import McpClient, McpToolError
from graph.models import Domain

logger = logging.getLogger("infra.jobs.hold_ledger_reaper")

REAP_INTERVAL_SECONDS: float = 60.0
"""ADR-019 / ResortFlow-HITL-Durability-State.md §3."""

STALE_AFTER: timedelta = timedelta(minutes=5)
"""Disclosed heuristic for "no active thread" — see module docstring. Comfortably
longer than a single conversational turn, short enough to catch a real crash within
one shift."""

_RELEASE_TOOL_BY_DOMAIN: dict[Domain, str] = {
    Domain.DINING: "release_hold",
    Domain.ENTERTAINMENT: "release_seat_hold",
    Domain.STAY: "release_room_hold",
    Domain.WELLNESS: "release_amenity_hold",
    # Domain.GAMING intentionally absent: gaming is read-only by construction
    # (no hold ever exists to reap, Constitution Principle II).
}

_RELEASE_SCOPE_BY_DOMAIN: dict[Domain, str] = {
    Domain.DINING: "dining:hold",
    Domain.ENTERTAINMENT: "entertainment:mutate",
    Domain.STAY: "stay:hold",
    Domain.WELLNESS: "wellness:hold",
}

_SYSTEM_GUEST_ID: str = "resortflow-hold-ledger-reaper"
"""Identifies the reaper's own calls in vendor/gateway telemetry — never a real guest."""


async def _fetch_stale_rows(conn_string: str, *, older_than: datetime) -> list[dict]:
    import psycopg
    from psycopg.rows import dict_row

    async with (
        await psycopg.AsyncConnection.connect(conn_string) as conn,
        conn.cursor(row_factory=dict_row) as cur,
    ):
        await cur.execute(
            """
            SELECT idempotency_key, hold_id, domain, tenant_id, status
            FROM hold_ledger
            WHERE status IN ('PENDING', 'HELD')
              AND updated_at < %(cutoff)s
            """,
            {"cutoff": older_than},
        )
        return list(await cur.fetchall())


async def _reap_one(row: dict, ledger: HoldLedger) -> None:
    domain = Domain(row["domain"])
    idempotency_key = row["idempotency_key"]
    hold_id = row["hold_id"]

    if hold_id is not None:
        tool_name = _RELEASE_TOOL_BY_DOMAIN.get(domain)
        scope = _RELEASE_SCOPE_BY_DOMAIN.get(domain)
        if tool_name is not None and scope is not None:
            client = McpClient(
                domain=domain.value,
                tenant_id=row["tenant_id"],
                guest_id=_SYSTEM_GUEST_ID,
                scopes=[scope],
            )
            try:
                await client.call_tool(tool_name, hold_id=hold_id, idempotency_key=idempotency_key)
            except McpToolError as exc:
                # A vendor that already has no record of this hold (e.g. it already
                # expired vendor-side) is not a reaper failure — proceed to mark
                # terminal regardless, per the Protocol's idempotent-write guarantee.
                logger.warning(
                    "reaper: release call failed for %s (domain=%s, hold_id=%s): %s",
                    idempotency_key, domain.value, hold_id, exc.detail,
                )

    await ledger.mark_orphan_released(idempotency_key=idempotency_key)
    logger.info(
        "reaper: orphan-released %s (domain=%s, was %s)",
        idempotency_key, domain.value, row["status"],
    )


async def run_once(*, conn_string: str | None = None) -> int:
    """One reap pass. Returns the number of rows reaped — used by tests to assert on
    a single deterministic pass rather than the infinite loop."""
    conn_string = conn_string or os.environ["POSTGRES_URL"]
    ledger = PostgresHoldLedger(conn_string)
    cutoff = datetime.now(UTC) - STALE_AFTER
    rows = await _fetch_stale_rows(conn_string, older_than=cutoff)
    for row in rows:
        await _reap_one(row, ledger)
    return len(rows)


async def run_forever(*, conn_string: str | None = None) -> None:  # pragma: no cover — operator entry point
    """Loop forever at `REAP_INTERVAL_SECONDS` cadence. Run as its own process
    (ADR-022) — independent of the gateway/supervisor lifecycle, so a supervisor
    crash never stops the reaper from cleaning up after it."""
    logger.info("hold_ledger_reaper starting, interval=%ss", REAP_INTERVAL_SECONDS)
    while True:
        try:
            count = await run_once(conn_string=conn_string)
            if count:
                logger.info("reaper pass: reaped %d row(s)", count)
        except Exception:
            logger.exception("reaper pass failed; will retry next interval")
        await asyncio.sleep(REAP_INTERVAL_SECONDS)


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_forever())
