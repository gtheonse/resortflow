"""pms-sim — Property Management System (Opera-equivalent).

Implements `docs/contracts/openapi/pms-sim.openapi.yaml` exactly, including its verbose
UPPER_SNAKE field names and `{"RESPONSE": {"STATUS", "DATA"}}` wrapper envelope. Auth:
OAuth2 client_credentials with its OWN token endpoint (`/pms/oauth2/token`), distinct
from ticketing's OAuth2 token endpoint and every other sim's scheme (FR-007/SC-009).
"""

from __future__ import annotations

import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import aiosqlite
from fastapi import Depends, Form, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sims._shared import holds, idempotency
from sims._shared.app import create_app, log_event, make_db_dependency, safe_unlink
from sims._shared.faults import active_fault, add_fault_middleware
from sims._shared.idempotency import require_idempotency_key
from sims._shared.tenancy import require_tenant_id

DB_PATH = Path(__file__).parent / "data" / "state.db"
TTL_DEFAULT_SECONDS = 900

CLIENT_ID = "pms-sim-client"
CLIENT_SECRET = "pms-sim-client-secret"
TOKEN_TTL_SECONDS = 3600

_issued_tokens: dict[str, float] = {}
_bearer = HTTPBearer()


def check_pms_bearer(credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)]) -> str:
    token = credentials.credentials
    expiry = _issued_tokens.get(token)
    if expiry is None or expiry < time.time():
        raise HTTPException(status_code=401, detail="invalid or expired access token")
    return token


SCHEMA = """
CREATE TABLE IF NOT EXISTS room_offers (
    offer_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    room_class TEXT NOT NULL,
    room_description TEXT NOT NULL,
    max_occupancy INTEGER NOT NULL,
    nightly_rate REAL NOT NULL,
    resort_fee_per_night REAL NOT NULL,
    cancellation_policy TEXT NOT NULL,
    available_units INTEGER NOT NULL,
    PRIMARY KEY (offer_id, tenant_id)
);
CREATE TABLE IF NOT EXISTS reservations (
    confirmation_number TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    hold_id TEXT NOT NULL,
    stay_id TEXT NOT NULL,
    room_class TEXT NOT NULL,
    arrival TEXT NOT NULL,
    departure TEXT NOT NULL,
    total_amount REAL NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS stays (
    stay_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    guest_profile_id TEXT NOT NULL,
    room_number TEXT NOT NULL,
    room_class TEXT NOT NULL,
    arrival TEXT NOT NULL,
    departure TEXT NOT NULL,
    checkout_time TEXT NOT NULL,
    status TEXT NOT NULL,
    location_zone TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS folio (
    stay_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    posted_date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    department TEXT NOT NULL
);
"""


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
    from sims.pms import seed as seed_mod

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
    _issued_tokens.clear()
    await ensure_seeded(db_path)


get_db = make_db_dependency(DB_PATH)

app = create_app(
    sim_name="pms",
    db_path=DB_PATH,
    ensure_seeded=ensure_seeded,
    reset=reset,
    title="pms-sim",
    start_reaper=True,
)


def _pms_envelope(code: str, message: str) -> dict:
    return {"error": {"code": code, "detail": message}}


add_fault_middleware(
    app,
    sim_name="pms",
    supported_faults={"timeout", "error_500", "rate_limit", "hold_expired", "inventory_race", "schema_drift"},
    envelope_fn=_pms_envelope,
)

PREFIX = "/pms/v5"


@app.post("/pms/oauth2/token")
async def issue_token(
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    client_secret: Annotated[str, Form()],
    scope: Annotated[str, Form()] = "pms.read pms.write",
) -> dict:
    if grant_type != "client_credentials" or not (
        secrets.compare_digest(client_id, CLIENT_ID) and secrets.compare_digest(client_secret, CLIENT_SECRET)
    ):
        raise HTTPException(status_code=401, detail="invalid client credentials")
    token = uuid.uuid4().hex
    _issued_tokens[token] = time.time() + TOKEN_TTL_SECONDS
    return {"access_token": token, "token_type": "bearer", "expires_in": TOKEN_TTL_SECONDS, "scope": scope}


def _offer_to_contract(r: aiosqlite.Row, nights: list[str]) -> dict:
    return {
        "OFFER_ID": r["offer_id"],
        "ROOM_CLASS": r["room_class"],
        "ROOM_DESCRIPTION": r["room_description"],
        "MAX_OCCUPANCY": r["max_occupancy"],
        "NIGHTLY_RATES": [{"DATE": d, "AMOUNT": r["nightly_rate"]} for d in nights],
        "RESORT_FEE_PER_NIGHT": r["resort_fee_per_night"],
        "CANCELLATION_POLICY": r["cancellation_policy"],
        "AVAILABLE_UNITS": r["available_units"],
    }


def _date_range(arrival: str, departure: str) -> list[str]:
    start = datetime.fromisoformat(arrival)
    end = datetime.fromisoformat(departure)
    nights = []
    d = start
    while d < end:
        nights.append(d.date().isoformat())
        d += timedelta(days=1)
    return nights or [arrival]


@app.get(PREFIX + "/availability/rooms")
async def search_rooms(
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_pms_bearer)],
    arrival: str,
    departure: str,
    adults: int,
    children: int = 0,
    room_class: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    query = "SELECT * FROM room_offers WHERE tenant_id = ?"
    params: list = [tenant_id]
    if room_class is not None:
        query += " AND room_class = ?"
        params.append(room_class)
    cur = await db.execute(query, params)
    rows = await cur.fetchall()
    await cur.close()
    now = datetime.now(UTC)
    nights = _date_range(arrival, departure)
    offers = []
    for r in rows:
        blocked = await holds.blocking_quantity(db, tenant_id, {"offer_id": r["offer_id"]}, now)
        remaining = r["available_units"] - blocked
        if remaining <= 0:
            continue
        offer = _offer_to_contract(r, nights)
        offer["AVAILABLE_UNITS"] = remaining
        offers.append(offer)
    return {"RESPONSE": {"STATUS": "OK", "DATA": {"ROOM_OFFERS": offers}}}


@app.post(PREFIX + "/holds/rooms", status_code=201)
async def hold_room(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_pms_bearer)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    body = await request.json()
    for field in ("OFFER_ID", "ARRIVAL", "DEPARTURE"):
        if field not in body:
            raise HTTPException(status_code=422, detail=f"missing {field}")
    ttl_seconds = body.get("TTL_SECONDS")
    if ttl_seconds is None:
        ttl_seconds = TTL_DEFAULT_SECONDS
    now = datetime.now(UTC)

    cur = await db.execute("SELECT * FROM room_offers WHERE offer_id = ? AND tenant_id = ?", (body["OFFER_ID"], tenant_id))
    offer = await cur.fetchone()
    await cur.close()
    if offer is not None:
        blocked = await holds.blocking_quantity(db, tenant_id, {"offer_id": body["OFFER_ID"]}, now)
        if blocked + 1 > offer["available_units"] or active_fault(request) == "inventory_race":
            raise HTTPException(status_code=409, detail="no rooms remaining for this offer")
    elif active_fault(request) == "inventory_race":
        raise HTTPException(status_code=409, detail="no rooms remaining for this offer")

    resource_ref = {"offer_id": body["OFFER_ID"], "arrival": body["ARRIVAL"], "departure": body["DEPARTURE"]}
    hold = await holds.place_hold(
        db, tenant_id=tenant_id, resource_ref=resource_ref, ttl_seconds=ttl_seconds,
        idempotency_key=idempotency_key, now=now,
    )
    log_event("hold_placed", sim="pms", hold_id=hold.hold_id, tenant_id=tenant_id)
    return {
        "HOLD_ID": hold.hold_id,
        "OFFER_ID": body["OFFER_ID"],
        "STATUS": "ACTIVE",
        "EXPIRES_AT": hold.expires_at.isoformat(),
    }


@app.delete(PREFIX + "/holds/rooms/{hold_id}", status_code=204)
async def release_room_hold(
    request: Request,
    hold_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_pms_bearer)],
    db: aiosqlite.Connection = Depends(get_db),
) -> None:
    now = datetime.now(UTC)
    try:
        await holds.release_hold(
            db, hold_id, tenant_id, now=now, force_expired=active_fault(request) == "hold_expired"
        )
    except (holds.HoldNotFound, holds.HoldExpiredConflict, holds.HoldTerminalConflict):
        return
    log_event("hold_released", sim="pms", hold_id=hold_id, tenant_id=tenant_id)
    return


@app.post(PREFIX + "/reservations")
async def confirm_room_reservation(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_pms_bearer)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> Response:
    from fastapi.responses import JSONResponse

    body = await request.json()
    endpoint = "POST /reservations"
    fp = idempotency.fingerprint(body)
    try:
        replay = await idempotency.check_replay(db, idempotency_key, endpoint, fp)
    except idempotency.IdempotencyKeyConflict:
        raise HTTPException(status_code=409, detail="idempotency key reused for a different request")
    if replay is not None:
        return JSONResponse(status_code=200, content=replay.body)

    for field in ("HOLD_ID", "GUEST_PROFILE_ID", "PAYMENT_TOKEN"):
        if field not in body:
            raise HTTPException(status_code=422, detail=f"missing {field}")

    now = datetime.now(UTC)
    try:
        hold = await holds.confirm_hold(
            db, body["HOLD_ID"], tenant_id, now=now, force_expired=active_fault(request) == "hold_expired"
        )
    except holds.HoldNotFound:
        raise HTTPException(status_code=404, detail="hold not found")
    except holds.HoldExpiredConflict:
        raise HTTPException(status_code=410, detail="hold expired")
    except holds.HoldTerminalConflict:
        raise HTTPException(status_code=409, detail="hold already terminal")

    cur = await db.execute("SELECT * FROM room_offers WHERE offer_id = ? AND tenant_id = ?", (hold.resource_ref["offer_id"], tenant_id))
    offer = await cur.fetchone()
    await cur.close()
    room_class = offer["room_class"] if offer else "STANDARD"
    nights = _date_range(hold.resource_ref["arrival"], hold.resource_ref["departure"])
    nightly_rate = offer["nightly_rate"] if offer else 0.0
    total_amount = nightly_rate * len(nights)

    confirmation_number = uuid.uuid4().hex.upper()[:10]
    stay_id = uuid.uuid4().hex
    await db.execute(
        "INSERT INTO reservations (confirmation_number, tenant_id, hold_id, stay_id, room_class, arrival, "
        "departure, total_amount, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'CONFIRMED')",
        (confirmation_number, tenant_id, hold.hold_id, stay_id, room_class, hold.resource_ref["arrival"], hold.resource_ref["departure"], total_amount),
    )
    await db.execute(
        "INSERT INTO stays (stay_id, tenant_id, guest_profile_id, room_number, room_class, arrival, departure, "
        "checkout_time, status, location_zone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED', ?)",
        (stay_id, tenant_id, body["GUEST_PROFILE_ID"], "TBD", room_class, hold.resource_ref["arrival"], hold.resource_ref["departure"], "11:00", "main_tower"),
    )
    await db.commit()

    response_body = {
        "CONFIRMATION_NUMBER": confirmation_number,
        "STAY_ID": stay_id,
        "ROOM_CLASS": room_class,
        "ARRIVAL": hold.resource_ref["arrival"],
        "DEPARTURE": hold.resource_ref["departure"],
        "TOTAL_AMOUNT": total_amount,
        "STATUS": "CONFIRMED",
    }
    await idempotency.store_response(db, idempotency_key, endpoint, fp, 201, response_body)
    log_event("reservation_confirmed", sim="pms", confirmation_number=confirmation_number, hold_id=hold.hold_id, tenant_id=tenant_id)
    return JSONResponse(status_code=201, content=response_body)


@app.get(PREFIX + "/stays/{stay_id}")
async def get_stay(
    stay_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_pms_bearer)],
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute("SELECT * FROM stays WHERE stay_id = ? AND tenant_id = ?", (stay_id, tenant_id))
    r = await cur.fetchone()
    await cur.close()
    if r is None:
        raise HTTPException(status_code=404, detail="stay not found")
    return {
        "STAY_ID": r["stay_id"],
        "TENANT_ID": r["tenant_id"],
        "GUEST_PROFILE_ID": r["guest_profile_id"],
        "ROOM_NUMBER": r["room_number"],
        "ROOM_CLASS": r["room_class"],
        "ARRIVAL": r["arrival"],
        "DEPARTURE": r["departure"],
        "CHECKOUT_TIME": r["checkout_time"],
        "STATUS": r["status"],
        "LOCATION_ZONE": r["location_zone"],
    }


@app.patch(PREFIX + "/stays/{stay_id}/checkout-time")
async def request_late_checkout(
    stay_id: str,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_pms_bearer)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> Response:
    from fastapi.responses import JSONResponse

    body = await request.json() if await request.body() else {}
    requested_time = body.get("REQUESTED_TIME", "13:00")
    cur = await db.execute("SELECT * FROM stays WHERE stay_id = ? AND tenant_id = ?", (stay_id, tenant_id))
    r = await cur.fetchone()
    await cur.close()
    if r is None:
        raise HTTPException(status_code=404, detail="stay not found")
    if requested_time >= "15:00":
        return JSONResponse(status_code=409, content={"detail": "unavailable — high occupancy"})
    await db.execute("UPDATE stays SET checkout_time = ? WHERE stay_id = ?", (requested_time, stay_id))
    await db.commit()
    charge = 0.0 if requested_time <= "13:00" else 50.0
    return JSONResponse(status_code=200, content={"GRANTED_TIME": requested_time, "CHARGE": charge})


@app.get(PREFIX + "/stays/{stay_id}/upgrade-offers")
async def get_upgrade_offers(
    stay_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_pms_bearer)],
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute("SELECT * FROM stays WHERE stay_id = ? AND tenant_id = ?", (stay_id, tenant_id))
    stay = await cur.fetchone()
    await cur.close()
    if stay is None:
        raise HTTPException(status_code=404, detail="stay not found")
    order = ["STANDARD", "DELUXE", "SUITE", "VILLA"]
    offers = []
    if stay["room_class"] in order and order.index(stay["room_class"]) < len(order) - 1:
        next_class = order[order.index(stay["room_class"]) + 1]
        cur = await db.execute(
            "SELECT * FROM room_offers WHERE tenant_id = ? AND room_class = ? LIMIT 1", (tenant_id, next_class)
        )
        target = await cur.fetchone()
        await cur.close()
        if target is not None:
            offers.append(
                {
                    "OFFER_ID": target["offer_id"],
                    "FROM_CLASS": stay["room_class"],
                    "TO_CLASS": next_class,
                    "NIGHTLY_UPLIFT": target["nightly_rate"] * 0.4,
                    "AVAILABLE_UNITS": target["available_units"],
                }
            )
    return {"OFFERS": offers}


@app.post(PREFIX + "/stays/{stay_id}/service-requests", status_code=201)
async def create_service_request(
    stay_id: str,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_pms_bearer)],
    idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    body = await request.json()
    if "REQUEST_TYPE" not in body:
        raise HTTPException(status_code=422, detail="missing REQUEST_TYPE")
    eta = {"HOUSEKEEPING": 30, "MAINTENANCE": 45, "AMENITY": 20, "IN_ROOM_DINING": 40, "TURNDOWN": 25, "LUGGAGE": 15}
    request_id = uuid.uuid4().hex
    log_event("service_request_created", sim="pms", stay_id=stay_id, request_id=request_id, tenant_id=tenant_id)
    return {"REQUEST_ID": request_id, "ETA_MINUTES": eta.get(body["REQUEST_TYPE"], 30), "STATUS": "ACKNOWLEDGED"}


@app.get(PREFIX + "/stays/{stay_id}/folio")
async def get_folio(
    stay_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_pms_bearer)],
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute("SELECT * FROM folio WHERE stay_id = ? AND tenant_id = ?", (stay_id, tenant_id))
    rows = await cur.fetchall()
    await cur.close()
    balance = sum(r["amount"] for r in rows)
    return {
        "BALANCE": balance,
        "LINE_ITEMS": [
            {"POSTED_DATE": r["posted_date"], "DESCRIPTION": r["description"], "AMOUNT": r["amount"], "DEPARTMENT": r["department"]}
            for r in rows
        ],
    }
