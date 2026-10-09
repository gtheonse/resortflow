"""Generic hold/TTL state machine shared by the four holding sims (amenities, dining,
pms, ticketing — never casino, which is read-only by contract).

State machine (see ``specs/002-simulation-ecosystem/data-model.md``)::

            create              confirm
    (none) --------> active -------------> confirmed (terminal)
                       |  |
              release  |  | ttl elapses, no confirm
                       v  v
                  released      expired      (both terminal)

Once a hold reaches ``confirmed``, ``released`` or ``expired`` every further transition
attempt is refused — this is the mechanism that protects against double-booking, not the
idempotency store (that only governs retries of the *same* call).

The clock is injectable so tests can compress the real 8-15 minute TTLs from the frozen
contracts (research.md item 4) instead of sleeping real wall-clock time.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)


class HoldNotFound(Exception):
    """No hold exists with this id for this tenant."""


class HoldTerminalConflict(Exception):
    """The hold has already reached a terminal state and refuses this transition."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"hold already {status}, terminal")


class HoldExpiredConflict(Exception):
    """The hold was active but its TTL has elapsed (lazily discovered)."""


@dataclass(frozen=True)
class Hold:
    hold_id: str
    tenant_id: str
    resource_ref: dict
    status: str
    ttl_seconds: int
    expires_at: datetime
    created_at: datetime
    idempotency_key: str | None

    def is_live(self, now: datetime) -> bool:
        return self.status == "active" and self.expires_at > now


SCHEMA = """
CREATE TABLE IF NOT EXISTS holds (
    hold_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    resource_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    ttl_seconds INTEGER NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    idempotency_key TEXT
);
CREATE INDEX IF NOT EXISTS idx_holds_tenant ON holds(tenant_id);
"""


async def init_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(SCHEMA)
    await conn.commit()


def _row_to_hold(row: aiosqlite.Row) -> Hold:
    return Hold(
        hold_id=row["hold_id"],
        tenant_id=row["tenant_id"],
        resource_ref=json.loads(row["resource_ref"]),
        status=row["status"],
        ttl_seconds=row["ttl_seconds"],
        expires_at=datetime.fromisoformat(row["expires_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        idempotency_key=row["idempotency_key"],
    )


MIN_TTL_SECONDS = 1
MAX_TTL_SECONDS = 30 * 24 * 3600  # 30 days — no real per-resource TTL from the frozen
# contracts comes close to this; it exists only to reject the pathological integers
# property-based contract fuzzing generates for an OpenAPI `integer` field the contracts
# leave otherwise unbounded (no min/max declared on `ttl_seconds`), which would
# otherwise overflow `timedelta`.


async def place_hold(
    conn: aiosqlite.Connection,
    *,
    tenant_id: str,
    resource_ref: dict,
    ttl_seconds: int,
    idempotency_key: str | None,
    now: datetime,
) -> Hold:
    ttl_seconds = max(MIN_TTL_SECONDS, min(ttl_seconds, MAX_TTL_SECONDS))
    hold_id = uuid.uuid4().hex
    expires_at = now + timedelta(seconds=ttl_seconds)
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        "INSERT INTO holds (hold_id, tenant_id, resource_ref, status, ttl_seconds, "
        "expires_at, created_at, idempotency_key) VALUES (?, ?, ?, 'active', ?, ?, ?, ?)",
        (
            hold_id,
            tenant_id,
            json.dumps(resource_ref),
            ttl_seconds,
            expires_at.isoformat(),
            now.isoformat(),
            idempotency_key,
        ),
    )
    await conn.commit()
    return Hold(
        hold_id=hold_id,
        tenant_id=tenant_id,
        resource_ref=resource_ref,
        status="active",
        ttl_seconds=ttl_seconds,
        expires_at=expires_at,
        created_at=now,
        idempotency_key=idempotency_key,
    )


async def get_hold(conn: aiosqlite.Connection, hold_id: str, tenant_id: str) -> Hold | None:
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute(
        "SELECT * FROM holds WHERE hold_id = ? AND tenant_id = ?", (hold_id, tenant_id)
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        return None
    return _row_to_hold(row)


async def _effective_status(hold: Hold, now: datetime) -> str:
    """Lazily-computed status: an active hold past its TTL reads as expired even before
    the background reaper has swept it, so confirm/release/availability logic never has
    to race the reaper for correctness."""
    if hold.status == "active" and hold.expires_at <= now:
        return "expired"
    return hold.status


async def _mark_status(conn: aiosqlite.Connection, hold_id: str, status: str) -> None:
    await conn.execute("UPDATE holds SET status = ? WHERE hold_id = ?", (status, hold_id))
    await conn.commit()


async def confirm_hold(
    conn: aiosqlite.Connection,
    hold_id: str,
    tenant_id: str,
    *,
    now: datetime,
    force_expired: bool = False,
) -> Hold:
    """Transition active -> confirmed. Raises HoldNotFound / HoldTerminalConflict /
    HoldExpiredConflict as appropriate. On a lazily-discovered expiry, the row is marked
    expired here (not left ambiguous) before the exception is raised."""
    hold = await get_hold(conn, hold_id, tenant_id)
    if hold is None:
        raise HoldNotFound()
    effective = "expired" if force_expired else await _effective_status(hold, now)
    if effective == "expired" and hold.status == "active":
        await _mark_status(conn, hold_id, "expired")
        raise HoldExpiredConflict()
    if hold.status != "active":
        raise HoldTerminalConflict(hold.status)
    await _mark_status(conn, hold_id, "confirmed")
    return await get_hold(conn, hold_id, tenant_id)  # type: ignore[return-value]


async def release_hold(
    conn: aiosqlite.Connection,
    hold_id: str,
    tenant_id: str,
    *,
    now: datetime,
    force_expired: bool = False,
) -> Hold:
    """Transition active -> released. A release of an already-released hold is a safe
    no-op (returns the hold, does not raise). A release against a confirmed hold raises
    HoldTerminalConflict since confirmed is terminal."""
    hold = await get_hold(conn, hold_id, tenant_id)
    if hold is None:
        raise HoldNotFound()
    if hold.status == "released":
        return hold  # safe no-op, spec.md edge case
    effective = "expired" if force_expired else await _effective_status(hold, now)
    if effective == "expired" and hold.status == "active":
        await _mark_status(conn, hold_id, "expired")
        raise HoldExpiredConflict()
    if hold.status != "active":
        raise HoldTerminalConflict(hold.status)
    await _mark_status(conn, hold_id, "released")
    return await get_hold(conn, hold_id, tenant_id)  # type: ignore[return-value]


async def sweep_expired(conn: aiosqlite.Connection, now: datetime) -> list[str]:
    """Sweep all active holds past their TTL to 'expired'. Returns the swept hold ids.
    Safe to call frequently; idempotent (a hold already expired is not touched again)."""
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute(
        "SELECT hold_id FROM holds WHERE status = 'active' AND expires_at <= ?",
        (now.isoformat(),),
    )
    rows = await cur.fetchall()
    await cur.close()
    ids = [r["hold_id"] for r in rows]
    if ids:
        await conn.executemany(
            "UPDATE holds SET status = 'expired' WHERE hold_id = ?", [(i,) for i in ids]
        )
        await conn.commit()
    return ids


async def blocking_quantity(
    conn: aiosqlite.Connection, tenant_id: str, resource_match: dict, now: datetime
) -> int:
    """Sum the quantity committed against a resource by every active (non-expired) or
    confirmed hold matching ``resource_match`` (a subset-match on resource_ref fields,
    e.g. {"offer_id": "..."}). Used by availability queries so that a hold's release —
    explicit, expired, lazily-expired, or reaper-swept — is reflected purely by the
    absence of a blocking row, with no separate inventory-decrement bookkeeping needed.
    ``quantity`` defaults to 1 per hold if the resource_ref carries no quantity field
    (the common case: one hold == one unit, e.g. amenities/dining/pms room)."""
    conn.row_factory = aiosqlite.Row
    cur = await conn.execute(
        "SELECT resource_ref, status, expires_at FROM holds WHERE tenant_id = ? "
        "AND status IN ('active', 'confirmed')",
        (tenant_id,),
    )
    rows = await cur.fetchall()
    await cur.close()
    total = 0
    for row in rows:
        ref = json.loads(row["resource_ref"])
        if not all(ref.get(k) == v for k, v in resource_match.items()):
            continue
        if row["status"] == "active" and datetime.fromisoformat(row["expires_at"]) <= now:
            continue  # lazily expired, does not block
        total += int(ref.get("quantity", 1))
    return total


class Reaper:
    """Background asyncio task that periodically sweeps expired holds for one sim's DB,
    started from the shared app factory (US4, T029/T030). Runs against the injectable
    clock so compressed-time tests can drive it deterministically without a real
    8-15 minute wait.

    Deliberately never uses ``task.cancel()`` to stop: cancelling mid-``connect()``
    would abandon aiosqlite's background connection thread with the SQLite file handle
    still open (aiosqlite offloads the blocking C-level open/close calls to a worker
    thread; cancelling the awaiting coroutine does not stop that thread), which then
    makes the very next ``reset()`` (FR-014, delete-and-reseed) fail on Windows with a
    file-in-use PermissionError. Instead ``stop()`` sets a flag the loop observes
    between iterations and waits for the current connect/sweep/close cycle to finish
    naturally, so the connection is always cleanly closed before ``stop()`` returns.
    """

    def __init__(
        self,
        conn_factory: Callable[[], Awaitable[aiosqlite.Connection]],
        clock: Clock = utcnow,
        interval_seconds: float = 1.0,
    ) -> None:
        self._conn_factory = conn_factory
        self._clock = clock
        self._interval = interval_seconds
        self._task: object | None = None
        self._stop_event: object | None = None

    async def _loop(self) -> None:
        import asyncio

        assert self._stop_event is not None
        while not self._stop_event.is_set():  # type: ignore[attr-defined]
            try:
                conn = await self._conn_factory()
                try:
                    await sweep_expired(conn, self._clock())
                finally:
                    await conn.close()
            except Exception as exc:  # noqa: BLE001 — a background sweep MUST NOT
                # crash the loop on a transient DB error (e.g. a reset racing a sweep);
                # the next iteration retries. Logged so a persistent failure is still
                # observable rather than silently swallowed forever.
                from sims._shared.app import log_event

                log_event("reaper_sweep_error", error=str(exc))
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval)  # type: ignore[attr-defined]
            except TimeoutError:
                pass

    def start(self) -> None:
        import asyncio

        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Signal the loop to stop and wait for its current iteration to finish —
        guaranteed to leave no dangling open connection (see class docstring)."""
        if self._stop_event is not None:
            self._stop_event.set()  # type: ignore[attr-defined]
        if self._task is not None:
            await self._task  # type: ignore[misc]
