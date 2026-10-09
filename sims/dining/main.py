"""dining-sim — SevenRooms-equivalent Reservation API.

Implements `docs/contracts/openapi/dining-sim.openapi.yaml` exactly. Auth: static API
key in `X-Dining-Key` (not OAuth2) — deliberately distinct from the other four sims
(FR-007/SC-009). Error envelope: `{"error": {"code", "detail", "retryable"}}`.
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import aiosqlite
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader

from sims._shared import holds, idempotency
from sims._shared.app import create_app, log_event, make_db_dependency, safe_unlink
from sims._shared.faults import active_fault, add_fault_middleware
from sims._shared.idempotency import require_idempotency_key
from sims._shared.tenancy import require_tenant_id

DB_PATH = Path(__file__).parent / "data" / "state.db"
TTL_DEFAULT_SECONDS = 600
TTL_MAX_SECONDS = 900
VENUE_CAPACITY_COVERS = 40

API_KEY = "dining-sim-static-key-MRI"

_api_key_header = APIKeyHeader(name="X-Dining-Key", auto_error=False)


def check_dining_key(key: Annotated[str | None, Depends(_api_key_header)]) -> str:
    if key is None or not secrets.compare_digest(key, API_KEY):
        raise HTTPException(
            status_code=401,
            detail={"error": {"code": "UPSTREAM_ERROR", "detail": "invalid X-Dining-Key", "retryable": False}},
        )
    return key


def err(code: str, detail: str, retryable: bool = False) -> dict:
    return {"error": {"code": code, "detail": detail, "retryable": retryable}}


SCHEMA = """
CREATE TABLE IF NOT EXISTS venues (
    venue_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    cuisine TEXT NOT NULL,
    price_band TEXT NOT NULL,
    dress_code TEXT NOT NULL,
    min_party INTEGER NOT NULL,
    max_party INTEGER NOT NULL,
    location_zone TEXT NOT NULL,
    supported_dietary TEXT NOT NULL,
    service_windows TEXT NOT NULL,
    PRIMARY KEY (venue_id, tenant_id)
);
CREATE TABLE IF NOT EXISTS reservations (
    reservation_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    hold_id TEXT NOT NULL,
    venue_id TEXT NOT NULL,
    venue_name TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    party_size INTEGER NOT NULL,
    status TEXT NOT NULL,
    dietary TEXT NOT NULL,
    occasion TEXT,
    confirmation_code TEXT NOT NULL,
    cancellation_deadline TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS waitlist (
    waitlist_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    venue_id TEXT NOT NULL,
    party_size INTEGER NOT NULL,
    position INTEGER NOT NULL,
    quoted_wait_minutes INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS venue_events (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    venue_id TEXT NOT NULL,
    title TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    price_per_cover REAL NOT NULL,
    seats_remaining INTEGER NOT NULL
);
"""


def _slot_grid() -> list[str]:
    slots = []
    for hour in range(17, 22):
        for minute in (0, 15, 30, 45):
            if hour == 21 and minute > 45:
                continue
            slots.append(f"{hour:02d}:{minute:02d}")
    return slots


SLOT_GRID = _slot_grid()


async def init_db(db_path: Path) -> None:
    conn = await aiosqlite.connect(db_path)
    try:
        await conn.executescript(SCHEMA)
        await holds.init_schema(conn)
        await idempotency.init_schema(conn)
        await conn.commit()
    finally:
        await conn.close()


async def ensure_seeded(db_path: Path) -> None:
    from sims.dining import seed as seed_mod

    fresh = not db_path.exists()
    await init_db(db_path)
    if fresh:
        conn = await aiosqlite.connect(db_path)
        try:
            await seed_mod.seed(conn)
        finally:
            await conn.close()


async def reset(db_path: Path) -> None:
    await safe_unlink(db_path)
    await ensure_seeded(db_path)


get_db = make_db_dependency(DB_PATH)

app = create_app(
    sim_name="dining",
    db_path=DB_PATH,
    ensure_seeded=ensure_seeded,
    reset=reset,
    title="dining-sim",
    start_reaper=True,
)

add_fault_middleware(
    app,
    sim_name="dining",
    supported_faults={"timeout", "error_500", "rate_limit", "hold_expired", "inventory_race", "schema_drift"},
    envelope_fn=err,
)

PREFIX = ""  # contract server url already carries /v1; mount at root of this app's own port


@app.get("/v1/venues")
async def list_venues(
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_dining_key)],
    cuisine: str | None = None,
    price_band: str | None = None,
    supports_dietary: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute("SELECT * FROM venues WHERE tenant_id = ?", (tenant_id,))
    rows = await cur.fetchall()
    await cur.close()
    venues = []
    for r in rows:
        if cuisine is not None and r["cuisine"] != cuisine:
            continue
        if price_band is not None and r["price_band"] != price_band:
            continue
        supported = json.loads(r["supported_dietary"])
        if supports_dietary is not None and supports_dietary not in supported:
            continue
        venues.append(
            {
                "venue_id": r["venue_id"],
                "tenant_id": r["tenant_id"],
                "name": r["name"],
                "cuisine": r["cuisine"],
                "price_band": r["price_band"],
                "dress_code": r["dress_code"],
                "min_party": r["min_party"],
                "max_party": r["max_party"],
                "location_zone": r["location_zone"],
                "supported_dietary": supported,
                "service_windows": json.loads(r["service_windows"]),
            }
        )
    return {"venues": venues}


@app.get("/v1/venues/{venue_id}/availability")
async def get_availability(
    venue_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_dining_key)],
    date: str,
    party_size: int,
    earliest: str | None = None,
    latest: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
) -> JSONResponse:
    cur = await db.execute(
        "SELECT * FROM venues WHERE venue_id = ? AND tenant_id = ?", (venue_id, tenant_id)
    )
    venue = await cur.fetchone()
    await cur.close()
    if venue is not None and not (venue["min_party"] <= party_size <= venue["max_party"]):
        return JSONResponse(status_code=400, content=err("INVALID_PARTY_SIZE", "party size outside venue limits"))
    now = datetime.now(UTC)
    slots = []
    for time in SLOT_GRID:
        if earliest is not None and time < earliest:
            continue
        if latest is not None and time > latest:
            continue
        blocked = await holds.blocking_quantity(db, tenant_id, {"venue_id": venue_id, "date": date, "time": time}, now)
        available = VENUE_CAPACITY_COVERS - blocked
        if available <= 0:
            continue
        slots.append({"time": time, "available_covers": available, "seating_types": ["standard"]})
    return JSONResponse(status_code=200, content={"venue_id": venue_id, "date": date, "slots": slots})


@app.post("/v1/holds", status_code=201)
async def create_hold(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_dining_key)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> JSONResponse:
    body = await request.json()
    for field in ("venue_id", "date", "time", "party_size"):
        if field not in body:
            return JSONResponse(status_code=400, content=err("INVALID_PARTY_SIZE", f"missing {field}"))
    requested_ttl = body.get("ttl_seconds")
    ttl_seconds = min(requested_ttl if requested_ttl is not None else TTL_DEFAULT_SECONDS, TTL_MAX_SECONDS)
    now = datetime.now(UTC)
    resource_ref = {
        "venue_id": body["venue_id"],
        "date": body["date"],
        "time": body["time"],
        "quantity": body["party_size"],
    }
    blocked = await holds.blocking_quantity(db, tenant_id, {"venue_id": body["venue_id"], "date": body["date"], "time": body["time"]}, now)
    if blocked + body["party_size"] > VENUE_CAPACITY_COVERS or active_fault(request) == "inventory_race":
        return JSONResponse(status_code=409, content=err("SLOT_UNAVAILABLE", "slot taken between availability check and hold", retryable=True))
    hold = await holds.place_hold(
        db, tenant_id=tenant_id, resource_ref=resource_ref, ttl_seconds=ttl_seconds,
        idempotency_key=idempotency_key, now=now,
    )
    log_event("hold_placed", sim="dining", hold_id=hold.hold_id, tenant_id=tenant_id)
    return JSONResponse(
        status_code=201,
        content={
            "hold_id": hold.hold_id,
            "venue_id": body["venue_id"],
            "date": body["date"],
            "time": body["time"],
            "party_size": body["party_size"],
            "status": "active",
            "expires_at": hold.expires_at.isoformat(),
        },
    )


def _hold_to_contract(hold: holds.Hold) -> dict:
    status_map = {"active": "active", "confirmed": "converted", "released": "released", "expired": "expired"}
    return {
        "hold_id": hold.hold_id,
        "venue_id": hold.resource_ref["venue_id"],
        "date": hold.resource_ref["date"],
        "time": hold.resource_ref["time"],
        "party_size": hold.resource_ref.get("quantity", 1),
        "status": status_map[hold.status],
        "expires_at": hold.expires_at.isoformat(),
    }


@app.get("/v1/holds/{hold_id}")
async def get_hold_endpoint(
    hold_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_dining_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> JSONResponse:
    hold = await holds.get_hold(db, hold_id, tenant_id)
    if hold is None:
        return JSONResponse(status_code=404, content=err("NOT_FOUND", "expired or never existed"))
    now = datetime.now(UTC)
    if hold.status == "active" and hold.expires_at <= now:
        return JSONResponse(status_code=404, content=err("NOT_FOUND", "expired or never existed"))
    return JSONResponse(status_code=200, content=_hold_to_contract(hold))


@app.delete("/v1/holds/{hold_id}", status_code=204, response_model=None)
async def release_hold_endpoint(
    request: Request,
    hold_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_dining_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> JSONResponse | Response:
    now = datetime.now(UTC)
    try:
        await holds.release_hold(
            db, hold_id, tenant_id, now=now, force_expired=active_fault(request) == "hold_expired"
        )
    except holds.HoldNotFound:
        return JSONResponse(status_code=404, content=err("NOT_FOUND", "already expired"))
    except holds.HoldExpiredConflict:
        return JSONResponse(status_code=404, content=err("NOT_FOUND", "already expired"))
    except holds.HoldTerminalConflict:
        return Response(status_code=204)
    log_event("hold_released", sim="dining", hold_id=hold_id, tenant_id=tenant_id)
    return Response(status_code=204)


@app.post("/v1/reservations")
async def confirm_reservation(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_dining_key)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> JSONResponse:
    body = await request.json()
    endpoint = "POST /reservations"
    fp = idempotency.fingerprint(body)
    try:
        replay = await idempotency.check_replay(db, idempotency_key, endpoint, fp)
    except idempotency.IdempotencyKeyConflict:
        return JSONResponse(status_code=409, content=err("UPSTREAM_ERROR", "idempotency key reused for a different request"))
    if replay is not None:
        return JSONResponse(status_code=200, content=replay.body)

    for field in ("hold_id", "guest"):
        if field not in body:
            return JSONResponse(status_code=400, content=err("INVALID_PARTY_SIZE", f"missing {field}"))

    # Only 201/200/410 are declared for this operation — a hold that doesn't exist, is
    # expired, or is already in another terminal state all collapse to 410.
    now = datetime.now(UTC)
    try:
        hold = await holds.confirm_hold(
            db, body["hold_id"], tenant_id, now=now, force_expired=active_fault(request) == "hold_expired"
        )
    except (holds.HoldNotFound, holds.HoldExpiredConflict, holds.HoldTerminalConflict):
        return JSONResponse(status_code=410, content=err("HOLD_EXPIRED", "hold expired before confirm — triggers compensation"))

    cur = await db.execute("SELECT * FROM venues WHERE venue_id = ? AND tenant_id = ?", (hold.resource_ref["venue_id"], tenant_id))
    venue = await cur.fetchone()
    await cur.close()
    venue_name = venue["name"] if venue else hold.resource_ref["venue_id"]

    reservation_id = uuid.uuid4().hex
    confirmation_code = reservation_id[:8].upper()
    cancellation_deadline = now + timedelta(hours=2)
    dietary = body.get("dietary", [])
    occasion = body.get("occasion")
    await db.execute(
        "INSERT INTO reservations (reservation_id, tenant_id, hold_id, venue_id, venue_name, date, time, "
        "party_size, status, dietary, occasion, confirmation_code, cancellation_deadline) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, ?, ?)",
        (
            reservation_id, tenant_id, hold.hold_id, hold.resource_ref["venue_id"], venue_name,
            hold.resource_ref["date"], hold.resource_ref["time"], hold.resource_ref.get("quantity", 1),
            json.dumps(dietary), occasion, confirmation_code, cancellation_deadline.isoformat(),
        ),
    )
    await db.commit()

    response_body = {
        "reservation_id": reservation_id,
        "tenant_id": tenant_id,
        "venue_id": hold.resource_ref["venue_id"],
        "venue_name": venue_name,
        "date": hold.resource_ref["date"],
        "time": hold.resource_ref["time"],
        "party_size": hold.resource_ref.get("quantity", 1),
        "status": "confirmed",
        "dietary": dietary,
        "occasion": occasion,
        "confirmation_code": confirmation_code,
        "cancellation_deadline": cancellation_deadline.isoformat(),
    }
    await idempotency.store_response(db, idempotency_key, endpoint, fp, 201, response_body)
    log_event("reservation_confirmed", sim="dining", reservation_id=reservation_id, hold_id=hold.hold_id, tenant_id=tenant_id)
    return JSONResponse(status_code=201, content=response_body)


@app.get("/v1/reservations/{reservation_id}")
async def get_reservation(
    reservation_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_dining_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute("SELECT * FROM reservations WHERE reservation_id = ? AND tenant_id = ?", (reservation_id, tenant_id))
    r = await cur.fetchone()
    await cur.close()
    if r is None:
        raise HTTPException(status_code=404, detail="not found")
    return {
        "reservation_id": r["reservation_id"],
        "tenant_id": r["tenant_id"],
        "venue_id": r["venue_id"],
        "venue_name": r["venue_name"],
        "date": r["date"],
        "time": r["time"],
        "party_size": r["party_size"],
        "status": r["status"],
        "dietary": json.loads(r["dietary"]),
        "occasion": r["occasion"],
        "confirmation_code": r["confirmation_code"],
        "cancellation_deadline": r["cancellation_deadline"],
    }


@app.delete("/v1/reservations/{reservation_id}", status_code=204, response_model=None)
async def cancel_reservation(
    reservation_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_dining_key)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> JSONResponse | Response:
    cur = await db.execute("SELECT * FROM reservations WHERE reservation_id = ? AND tenant_id = ?", (reservation_id, tenant_id))
    r = await cur.fetchone()
    await cur.close()
    if r is None:
        # Only 204/409 are declared for this operation — an unknown reservation_id reads
        # as already-cancelled/no-op.
        return Response(status_code=204)
    if r["status"] == "cancelled":
        return Response(status_code=204)
    deadline = datetime.fromisoformat(r["cancellation_deadline"])
    now = datetime.now(UTC)
    if now > deadline:
        return JSONResponse(status_code=409, content=err("INSIDE_CANCEL_WINDOW", "inside no-cancel window — requires human"))
    await db.execute("UPDATE reservations SET status = 'cancelled' WHERE reservation_id = ?", (reservation_id,))
    await db.commit()
    log_event("reservation_cancelled", sim="dining", reservation_id=reservation_id, tenant_id=tenant_id)
    return Response(status_code=204)


@app.post("/v1/waitlist", status_code=201)
async def join_waitlist(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_dining_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    body = await request.json()
    waitlist_id = uuid.uuid4().hex
    cur = await db.execute("SELECT COUNT(*) as n FROM waitlist WHERE tenant_id = ? AND venue_id = ?", (tenant_id, body["venue_id"]))
    row = await cur.fetchone()
    await cur.close()
    assert row is not None  # COUNT(*) always returns exactly one row
    position = row["n"] + 1
    quoted = position * 10
    # `party_size` is an unbounded OpenAPI `integer` in the frozen contract (no min/max
    # declared) — clamp to SQLite's signed-64-bit INTEGER range so a pathological value
    # (property-based fuzzing routinely generates arbitrary-precision Python ints) can't
    # overflow the column; this never affects any realistic caller.
    party_size = max(-(2**63), min(int(body["party_size"]), 2**63 - 1))
    await db.execute(
        "INSERT INTO waitlist (waitlist_id, tenant_id, venue_id, party_size, position, quoted_wait_minutes) VALUES (?, ?, ?, ?, ?, ?)",
        (waitlist_id, tenant_id, body["venue_id"], party_size, position, quoted),
    )
    await db.commit()
    return {"waitlist_id": waitlist_id, "position": position, "quoted_wait_minutes": quoted}


@app.get("/v1/venue-events")
async def list_venue_events(
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_dining_key)],
    from_date: str | None = None,
    to_date: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute("SELECT * FROM venue_events WHERE tenant_id = ?", (tenant_id,))
    rows = await cur.fetchall()
    await cur.close()
    events = []
    for r in rows:
        if from_date is not None and r["date"] < from_date:
            continue
        if to_date is not None and r["date"] > to_date:
            continue
        events.append(
            {
                "event_id": r["event_id"],
                "venue_id": r["venue_id"],
                "title": r["title"],
                "date": r["date"],
                "time": r["time"],
                "price_per_cover": r["price_per_cover"],
                "seats_remaining": r["seats_remaining"],
            }
        )
    return {"events": events}
