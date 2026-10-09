"""Shared FastAPI app factory, SQLite bootstrap, and structured logging helper.

Imported by all five sims' ``main.py`` (per ``plan.md``'s Project Structure). Mounts the
reset route (``sims/_shared/reset.py``) on every app; the tenancy dependency
(``sims/_shared/tenancy.py``) is applied per-route by each sim, not globally, since a
handful of routes (e.g. the reset route itself) are deliberately tenant-agnostic.

``log_event`` is the only sanctioned logging entry point for sim event logging: it logs
identifiers and event types only (fault triggered, idempotency replay, hold expired) and
never full request/response bodies, so guest contact fields and free-text
``DietaryRequirement`` detail can never reach a log line, per ``plan.md``'s logging-
discipline constraint.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import aiosqlite
from fastapi import FastAPI

from sims._shared.holds import Reaper
from sims._shared.reset import build_reset_router

logger = logging.getLogger("resortflow.sims")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


def log_event(kind: str, **ids: object) -> None:
    """Log an event by identifiers and event type only. NEVER pass request/response
    bodies, guest contact fields, or dietary free text here."""
    logger.info(json.dumps({"event": kind, **ids}, default=str))


async def safe_unlink(db_path: Path, retries: int = 10, delay_seconds: float = 0.05) -> None:
    """Delete a sim's SQLite file, tolerating a brief Windows file-lock window right
    after a connection (e.g. the reaper's) closes. Used by every sim's ``reset()``."""
    import asyncio

    if not db_path.exists():
        return
    last_error: OSError | None = None
    for _ in range(retries):
        try:
            db_path.unlink()
            return
        except PermissionError as exc:  # pragma: no cover — Windows-only race
            last_error = exc
            await asyncio.sleep(delay_seconds)
    if last_error is not None:
        raise last_error


def make_db_dependency(db_path: Path) -> Callable[[], AsyncIterator[aiosqlite.Connection]]:
    """Builds a FastAPI dependency yielding a fresh aiosqlite connection per request.
    Deliberately not a long-lived shared connection: reset (FR-014) deletes the
    underlying file on demand, which is only safe if no connection holds it open between
    requests."""

    async def get_db() -> AsyncIterator[aiosqlite.Connection]:
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        try:
            yield conn
        finally:
            await conn.close()

    return get_db


def create_app(
    *,
    sim_name: str,
    db_path: Path,
    ensure_seeded: Callable[[Path], Awaitable[None]],
    reset: Callable[[Path], Awaitable[None]],
    title: str | None = None,
    start_reaper: bool = False,
    reaper_interval_seconds: float = 1.0,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Build a sim's FastAPI app: bootstraps its SQLite file on startup (creating and
    seeding it if missing), mounts ``POST /__sim__/reset``, and optionally starts the
    background hold-expiry reaper (US4) for the four holding sims."""

    reaper_holder: dict[str, Reaper] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        await ensure_seeded(db_path)
        log_event("sim_started", sim=sim_name)
        if start_reaper:
            from sims._shared.holds import utcnow

            active_clock = clock or utcnow

            async def _conn_factory() -> aiosqlite.Connection:
                conn = await aiosqlite.connect(db_path)
                conn.row_factory = aiosqlite.Row
                return conn

            reaper = Reaper(
                _conn_factory, clock=active_clock, interval_seconds=reaper_interval_seconds
            )
            reaper.start()
            reaper_holder["reaper"] = reaper
        yield
        if "reaper" in reaper_holder:
            await reaper_holder["reaper"].stop()
        log_event("sim_stopped", sim=sim_name)

    app = FastAPI(title=title or f"{sim_name}-sim", lifespan=lifespan)
    app.state.sim_name = sim_name
    app.state.db_path = db_path

    async def _reset() -> None:
        if "reaper" in reaper_holder:
            await reaper_holder["reaper"].stop()
        await reset(db_path)
        if start_reaper:
            from sims._shared.holds import utcnow

            active_clock = clock or utcnow

            async def _conn_factory() -> aiosqlite.Connection:
                conn = await aiosqlite.connect(db_path)
                conn.row_factory = aiosqlite.Row
                return conn

            reaper = Reaper(
                _conn_factory, clock=active_clock, interval_seconds=reaper_interval_seconds
            )
            reaper.start()
            reaper_holder["reaper"] = reaper
        log_event("reset", sim=sim_name)

    app.include_router(build_reset_router(_reset))

    return app
