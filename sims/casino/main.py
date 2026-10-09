"""casino-sim — Casino Management & Loyalty API (READ-ONLY for ResortFlow).

Implements `docs/contracts/openapi/casino-sim.openapi.yaml` exactly. No holds import,
no mutating endpoint anywhere — the gaming domain is structurally read-only per the
constitution's Governed Integration Surface principle and spec.md's Clarifications
correction. Auth: HTTP Bearer, a static token distinct from every other sim (FR-007/
SC-009).
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Annotated

import aiosqlite
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from sims._shared.app import create_app, make_db_dependency, safe_unlink
from sims._shared.faults import add_fault_middleware
from sims._shared.tenancy import require_tenant_id

DB_PATH = Path(__file__).parent / "data" / "state.db"

AUTH_TOKEN = "casino-sim-static-bearer-token"

_bearer = HTTPBearer()


def check_bearer_auth(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)]
) -> str:
    if not secrets.compare_digest(credentials.credentials, AUTH_TOKEN):
        raise HTTPException(status_code=401, detail="invalid bearer token")
    return credentials.credentials


SCHEMA = """
CREATE TABLE IF NOT EXISTS responsible_gaming (
    guest_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    jurisdiction TEXT NOT NULL,
    self_excluded INTEGER NOT NULL,
    exclusion_scope TEXT,
    limit_flags TEXT NOT NULL,
    offers_permitted INTEGER NOT NULL,
    gaming_content_permitted INTEGER NOT NULL,
    mandated_message TEXT NOT NULL,
    support_resources TEXT NOT NULL,
    PRIMARY KEY (guest_id, tenant_id)
);
CREATE TABLE IF NOT EXISTS loyalty (
    guest_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    tier TEXT NOT NULL,
    points_balance INTEGER NOT NULL,
    comp_dollars REAL NOT NULL,
    tier_expires TEXT NOT NULL,
    entitlements TEXT NOT NULL,
    PRIMARY KEY (guest_id, tenant_id)
);
CREATE TABLE IF NOT EXISTS loyalty_offers (
    offer_id TEXT PRIMARY KEY,
    guest_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    value REAL NOT NULL,
    expires TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS floor_games (
    tenant_id TEXT NOT NULL,
    game_type TEXT NOT NULL,
    location_zone TEXT NOT NULL,
    table_minimum REAL,
    hours TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tournaments (
    tournament_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    start TEXT NOT NULL,
    buy_in REAL NOT NULL,
    registration_channel TEXT NOT NULL
);
"""

MANDATED_MESSAGE = {
    "MGV": "Nevada Council on Problem Gambling: 1-800-522-4700. Gambling problem? Call NCPG.",
    "MHB": "Massachusetts GameSense/MGC: call or text the Massachusetts Problem Gambling Helpline 1-800-327-5050.",
}
JURISDICTION = {"MGV": "NV", "MHB": "MA"}


async def init_db(db_path: Path) -> None:
    conn = await aiosqlite.connect(db_path)
    try:
        await conn.executescript(SCHEMA)
        await conn.commit()
    finally:
        await conn.close()


async def ensure_seeded(db_path: Path) -> None:
    from sims.casino import seed as seed_mod

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
    sim_name="casino",
    db_path=DB_PATH,
    ensure_seeded=ensure_seeded,
    reset=reset,
    title="casino-sim",
    start_reaper=False,
)


def _casino_envelope(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


add_fault_middleware(
    app,
    sim_name="casino",
    supported_faults={"timeout", "error_500", "rate_limit", "schema_drift"},
    envelope_fn=_casino_envelope,
)

PREFIX = "/casino/v1"


@app.get(PREFIX + "/responsible-gaming/status")
async def get_responsible_gaming_status(
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_bearer_auth)],
    guest_id: str,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute(
        "SELECT * FROM responsible_gaming WHERE guest_id = ? AND tenant_id = ?",
        (guest_id, tenant_id),
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        # No 404 is declared on this contract for an unknown guest_id — the operation's
        # only documented response is 200, so an unrecognized (or fuzz-generated) guest
        # id reads as a default, unflagged guest rather than an error.
        return {
            "guest_id": guest_id,
            "jurisdiction": JURISDICTION[tenant_id],
            "self_excluded": False,
            "exclusion_scope": None,
            "limit_flags": [],
            "offers_permitted": True,
            "gaming_content_permitted": True,
            "mandated_message": MANDATED_MESSAGE[tenant_id],
            "support_resources": [],
        }
    return {
        "guest_id": row["guest_id"],
        "jurisdiction": row["jurisdiction"],
        "self_excluded": bool(row["self_excluded"]),
        "exclusion_scope": row["exclusion_scope"],
        "limit_flags": json.loads(row["limit_flags"]),
        "offers_permitted": bool(row["offers_permitted"]),
        "gaming_content_permitted": bool(row["gaming_content_permitted"]),
        "mandated_message": row["mandated_message"],
        "support_resources": json.loads(row["support_resources"]),
    }


@app.get(PREFIX + "/loyalty/{guest_id}")
async def get_loyalty_profile(
    guest_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_bearer_auth)],
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute(
        "SELECT * FROM loyalty WHERE guest_id = ? AND tenant_id = ?", (guest_id, tenant_id)
    )
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        # No 404 declared on this operation either — default new-member profile.
        return {
            "guest_id": guest_id,
            "tier": "MEMBER",
            "points_balance": 0,
            "comp_dollars": 0.0,
            "tier_expires": "2099-12-31",
            "entitlements": [],
        }
    return {
        "guest_id": row["guest_id"],
        "tier": row["tier"],
        "points_balance": row["points_balance"],
        "comp_dollars": row["comp_dollars"],
        "tier_expires": row["tier_expires"],
        "entitlements": json.loads(row["entitlements"]),
    }


@app.get(PREFIX + "/loyalty/{guest_id}/offers")
async def get_loyalty_offers(
    guest_id: str,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_bearer_auth)],
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute(
        "SELECT * FROM responsible_gaming WHERE guest_id = ? AND tenant_id = ?",
        (guest_id, tenant_id),
    )
    rg = await cur.fetchone()
    await cur.close()
    if rg is not None and not bool(rg["offers_permitted"]):
        raise HTTPException(status_code=403, detail="offers suppressed — responsible gaming flag active")
    cur = await db.execute(
        "SELECT * FROM loyalty_offers WHERE guest_id = ? AND tenant_id = ?", (guest_id, tenant_id)
    )
    rows = await cur.fetchall()
    await cur.close()
    return {
        "offers": [
            {
                "offer_id": r["offer_id"],
                "category": r["category"],
                "description": r["description"],
                "value": r["value"],
                "expires": r["expires"],
            }
            for r in rows
        ]
    }


@app.get(PREFIX + "/floor/games")
async def list_floor_games(
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_bearer_auth)],
    game_type: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    query = "SELECT * FROM floor_games WHERE tenant_id = ?"
    params: list = [tenant_id]
    if game_type is not None:
        query += " AND game_type = ?"
        params.append(game_type)
    cur = await db.execute(query, params)
    rows = await cur.fetchall()
    await cur.close()
    return {
        "games": [
            {
                "game_type": r["game_type"],
                "location_zone": r["location_zone"],
                "table_minimum": r["table_minimum"],
                "hours": r["hours"],
            }
            for r in rows
        ],
        "mandated_message": MANDATED_MESSAGE[tenant_id],
    }


@app.get(PREFIX + "/tournaments")
async def list_tournaments(
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    _auth: Annotated[str, Depends(check_bearer_auth)],
    from_date: str | None = None,
    db: aiosqlite.Connection = Depends(get_db),
) -> dict:
    cur = await db.execute("SELECT * FROM tournaments WHERE tenant_id = ?", (tenant_id,))
    rows = await cur.fetchall()
    await cur.close()
    return {
        "tournaments": [
            {
                "tournament_id": r["tournament_id"],
                "name": r["name"],
                "start": r["start"],
                "buy_in": r["buy_in"],
                "registration_channel": r["registration_channel"],
            }
            for r in rows
        ],
        "mandated_message": MANDATED_MESSAGE[tenant_id],
    }
