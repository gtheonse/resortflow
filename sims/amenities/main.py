"""amenities-sim — Spa, Fitness, Cabana & Golf Booking.

Implements `docs/contracts/openapi/amenities-sim.openapi.yaml` exactly (paths, schemas,
status codes). Auth: HTTP Basic — the oldest/weakest scheme in the estate, deliberately
distinct from the other four sims (FR-007/SC-009).
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import aiosqlite
from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from sims._shared import holds, idempotency
from sims._shared.app import create_app, log_event, make_db_dependency, safe_unlink
from sims._shared.faults import active_fault, add_fault_middleware
from sims._shared.idempotency import require_idempotency_key
from sims._shared.tenancy import require_tenant_id

DB_PATH = Path(__file__).parent / "data" / "state.db"
TTL_DEFAULT_SECONDS = 600

AUTH_USERNAME = "amenities-mcp"
AUTH_PASSWORD = "amenities-sim-secret"

_security = HTTPBasic()


def check_basic_auth(credentials: Annotated[HTTPBasicCredentials, Depends(_security)]) -> str:
    valid_user = secrets.compare_digest(credentials.username, AUTH_USERNAME)
    valid_pass = secrets.compare_digest(credentials.password, AUTH_PASSWORD)
    if not (valid_user and valid_pass):
        raise HTTPException(status_code=401, detail={"err_code": "UNAUTHORIZED", "err_msg": "invalid credentials"})
    return credentials.username


SCHEMA = """
CREATE TABLE IF NOT EXISTS services (
    service_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    price REAL NOT NULL,
    location_zone TEXT NOT NULL,
    contraindications TEXT NOT NULL,
    PRIMARY KEY (service_id, tenant_id)
);
CREATE TABLE IF NOT EXISTS practitioners (
    practitioner_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    gender TEXT NOT NULL,
    accessible INTEGER NOT NULL,
    PRIMARY KEY (practitioner_id, service_id, tenant_id)
);
CREATE TABLE IF NOT EXISTS bookings (
    booking_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    hold_id TEXT NOT NULL,
    service_id TEXT NOT NULL,
    service_name TEXT NOT NULL,
    guest_id TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    location_zone TEXT NOT NULL,
    arrive_minutes_early INTEGER NOT NULL,
    cancellation_deadline TEXT NOT NULL,
    status TEXT NOT NULL
);
"""

DAILY_SLOTS = [f"{h:02d}:00" for h in range(9, 18)]


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
    from sims.amenities import seed as seed_mod

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
    sim_name="amenities",
    db_path=DB_PATH,
    ensure_seeded=ensure_seeded,
    reset=reset,
    title="amenities-sim",
    start_reaper=True,
)


def _amenities_envelope(code: str, message: str) -> dict:
    return {"err_code": code, "err_msg": message}


@app.exception_handler(HTTPException)
async def _flatten_amenities_errors(_request: Request, exc: HTTPException) -> JSONResponse:
    """The frozen contract's DELIBERATE QUIRK is a flat `{err_code, err_msg}` error
    envelope. FastAPI's default HTTPException handler always nests `detail` under a
    `"detail"` key, which would silently produce a *fourth* undocumented envelope
    shape rather than the contract's documented flat one — every raise in this module
    already passes a `{"err_code", "err_msg"}` dict as `detail`, so this handler emits
    it directly as the top-level body instead of double-wrapping it."""
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)
    return JSONResponse(status_code=exc.status_code, content={"err_code": "ERROR", "err_msg": str(exc.detail)}, headers=exc.headers)


add_fault_middleware(
    app,
    sim_name="amenities",
    supported_faults={"timeout", "error_500", "rate_limit", "hold_expired", "inventory_race", "schema_drift"},
    envelope_fn=_amenities_envelope,
)

PREFIX = "/amenities/v3"


@app.get(PREFIX + "/services")
async def list_services(
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_basic_auth)],
    category: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    query = "SELECT * FROM services WHERE tenant_id = ?"
    params: list = [tenant_id]
    if category is not None:
        query += " AND category = ?"
        params.append(category)
    cur = await db.execute(query, params)
    rows = await cur.fetchall()
    await cur.close()
    return {
        "services": [
            {
                "service_id": r["service_id"],
                "category": r["category"],
                "name": r["name"],
                "duration_minutes": r["duration_minutes"],
                "price": r["price"],
                "location_zone": r["location_zone"],
                "contraindications": json.loads(r["contraindications"]),
            }
            for r in rows
        ]
    }


@app.get(PREFIX + "/services/{service_id}/availability")
async def get_service_availability(
    service_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_basic_auth)],
    date: str,
    practitioner_gender_preference: str = "any",
    accessibility_required: bool = False,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute(
        "SELECT * FROM practitioners WHERE tenant_id = ? AND service_id = ?",
        (tenant_id, service_id),
    )
    practitioners = await cur.fetchall()
    await cur.close()
    now = datetime.now(UTC)
    slots = []
    for p in practitioners:
        if practitioner_gender_preference != "any" and p["gender"] != practitioner_gender_preference:
            continue
        if accessibility_required and not p["accessible"]:
            continue
        for time in DAILY_SLOTS:
            blocked = await holds.blocking_quantity(
                db, tenant_id, {"practitioner_id": p["practitioner_id"], "date": date, "time": time}, now
            )
            if blocked:
                continue
            slots.append(
                {
                    "time": time,
                    "practitioner_id": p["practitioner_id"],
                    "practitioner_gender": p["gender"],
                    "accessible": bool(p["accessible"]),
                }
            )
    return {"slots": slots}


@app.post(PREFIX + "/holds", status_code=201)
async def hold_amenity_slot(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_basic_auth)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    body = await request.json()
    for field in ("service_id", "date", "time", "practitioner_id"):
        if field not in body:
            raise HTTPException(status_code=422, detail={"err_code": "VALIDATION", "err_msg": f"missing {field}"})
    ttl_seconds = body.get("ttl_seconds")
    if ttl_seconds is None:
        ttl_seconds = TTL_DEFAULT_SECONDS
    now = datetime.now(UTC)
    resource_ref = {
        "service_id": body["service_id"],
        "date": body["date"],
        "time": body["time"],
        "practitioner_id": body["practitioner_id"],
    }
    blocked = await holds.blocking_quantity(db, tenant_id, resource_ref, now)
    if blocked or active_fault(request) == "inventory_race":
        raise HTTPException(status_code=409, detail={"err_code": "PRACTITIONER_TAKEN", "err_msg": "practitioner taken"})
    hold = await holds.place_hold(
        db, tenant_id=tenant_id, resource_ref=resource_ref, ttl_seconds=ttl_seconds,
        idempotency_key=idempotency_key, now=now,
    )
    log_event("hold_placed", sim="amenities", hold_id=hold.hold_id, tenant_id=tenant_id)
    return {"hold_id": hold.hold_id, "expires_at": hold.expires_at.isoformat()}


@app.delete(PREFIX + "/holds/{hold_id}", status_code=204)
async def release_amenity_hold(
    request: Request,
    hold_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_basic_auth)],
    db: aiosqlite.Connection = Depends(get_db),
) -> None:
    # The contract declares only 204 for this operation (no 404) — release is treated as
    # always-successful/idempotent from the caller's perspective, including for a hold_id
    # that never existed or is already in a terminal state.
    now = datetime.now(UTC)
    try:
        await holds.release_hold(
            db, hold_id, tenant_id, now=now, force_expired=active_fault(request) == "hold_expired"
        )
    except (holds.HoldNotFound, holds.HoldExpiredConflict, holds.HoldTerminalConflict):
        return
    log_event("hold_released", sim="amenities", hold_id=hold_id, tenant_id=tenant_id)
    return


@app.post(PREFIX + "/bookings", status_code=201, response_model=None)
async def confirm_amenity_booking(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_basic_auth)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> dict | JSONResponse:
    body = await request.json()
    endpoint = "POST /bookings"
    fp = idempotency.fingerprint(body)
    try:
        replay = await idempotency.check_replay(db, idempotency_key, endpoint, fp)
    except idempotency.IdempotencyKeyConflict:
        raise HTTPException(status_code=409, detail={"err_code": "IDEMPOTENCY_KEY_CONFLICT", "err_msg": "key reused for a different request"})
    if replay is not None:
        return JSONResponse(status_code=replay.status_code, content=replay.body)

    for field in ("hold_id", "guest_id"):
        if field not in body:
            raise HTTPException(status_code=422, detail={"err_code": "VALIDATION", "err_msg": f"missing {field}"})

    # The contract declares only 201/410 for this operation. A hold that doesn't exist,
    # is already expired, or has already reached another terminal state all collapse to
    # 410 — the only documented "cannot confirm this hold" response.
    now = datetime.now(UTC)
    try:
        hold = await holds.confirm_hold(
            db, body["hold_id"], tenant_id, now=now, force_expired=active_fault(request) == "hold_expired"
        )
    except (holds.HoldNotFound, holds.HoldExpiredConflict, holds.HoldTerminalConflict):
        raise HTTPException(status_code=410, detail={"err_code": "HOLD_EXPIRED", "err_msg": "hold expired or not confirmable"})

    cur = await db.execute(
        "SELECT * FROM services WHERE tenant_id = ? AND service_id = ?",
        (tenant_id, hold.resource_ref["service_id"]),
    )
    svc = await cur.fetchone()
    await cur.close()
    service_name = svc["name"] if svc else hold.resource_ref["service_id"]
    location_zone = svc["location_zone"] if svc else "unknown"

    import uuid

    booking_id = uuid.uuid4().hex
    cancellation_deadline = now + timedelta(hours=24)
    await db.execute(
        "INSERT INTO bookings (booking_id, tenant_id, hold_id, service_id, service_name, guest_id, "
        "date, time, location_zone, arrive_minutes_early, cancellation_deadline, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed')",
        (
            booking_id, tenant_id, hold.hold_id, hold.resource_ref["service_id"], service_name,
            body["guest_id"], hold.resource_ref["date"], hold.resource_ref["time"], location_zone,
            15, cancellation_deadline.isoformat(),
        ),
    )
    await db.commit()

    response_body = {
        "booking_id": booking_id,
        "service_name": service_name,
        "date": hold.resource_ref["date"],
        "time": hold.resource_ref["time"],
        "location_zone": location_zone,
        "arrive_minutes_early": 15,
        "cancellation_deadline": cancellation_deadline.isoformat(),
    }
    await idempotency.store_response(db, idempotency_key, endpoint, fp, 201, response_body)
    log_event("booking_confirmed", sim="amenities", booking_id=booking_id, hold_id=hold.hold_id, tenant_id=tenant_id)
    return response_body


@app.delete(PREFIX + "/bookings/{booking_id}", status_code=204)
async def cancel_amenity_booking(
    booking_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_basic_auth)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> None:
    cur = await db.execute(
        "SELECT * FROM bookings WHERE booking_id = ? AND tenant_id = ?", (booking_id, tenant_id)
    )
    booking = await cur.fetchone()
    await cur.close()
    if booking is None:
        # Only 204/409 are declared for this operation (no 404) — an unknown booking_id
        # reads as already-cancelled/no-op, per the same convention as hold release.
        return
    if booking["status"] == "cancelled":
        return
    deadline = datetime.fromisoformat(booking["cancellation_deadline"])
    now = datetime.now(UTC)
    if now > deadline:
        raise HTTPException(status_code=409, detail={"err_code": "INSIDE_CANCEL_WINDOW", "err_msg": "inside cancellation window"})
    await db.execute("UPDATE bookings SET status = 'cancelled' WHERE booking_id = ?", (booking_id,))
    await db.commit()
    log_event("booking_cancelled", sim="amenities", booking_id=booking_id, tenant_id=tenant_id)
    return
